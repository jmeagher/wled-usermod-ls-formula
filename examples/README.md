# Examples

Each `.lsf` file is a complete formula. Try one on a device (replace the address):

```sh
python3 tools/lsfx.py wled-1234.local push 0 examples/rainbow_wave.lsf --name "Rainbow Wave" --activate
```

or preview it on your computer first, with no hardware:

```sh
tests/run.sh                                     # builds build/lsf_cli
build/lsf_cli examples/fire.lsf -n 16 -f 60      # 16 pixels after 60 frames, as hex colours
build/lsf_cli examples/ripple2d.lsf -w 8 -h 8    # an 8x8 panel
```

The **Speed** and **Intensity** sliders in WLED are `sp` and `ix` in every formula; the examples say what they do.

| File | Looks like | `sp` / `ix` | Instrs | Cost | Fits (LEDs) | Uses |
|---|---|---|---|---|---|---|
| [`rainbow_wave.lsf`](rainbow_wave.lsf) | A rainbow drifting along the strip with a brightness ripple on top | `sp` shifts the hue | 20 | 33 | 1376 | `hsv`, `sin`, `scale` |
| [`plasma.lsf`](plasma.lsf) | Classic plasma: three sines through the segment palette | `sp` shifts the palette | 37 | 51 | 920 | `pal`, `sin`, 2D `y` |
| [`comet_trail.lsf`](comet_trail.lsf) | A bright head sweeping along with a fading tail (stateless) | `sp` speed, `ix` shortens the tail | 37 | 57 | 828 | `hsv`, `dim`, `clamp` |
| [`twinkle.lsf`](twinkle.lsf) | Pixels fading in and out at random phases | `sp` speed, `ix` how many are lit | 36 | 55 | 857 | `hash`, `tri`, `dim`, `pal` |
| [`ripple2d.lsf`](ripple2d.lsf) | Concentric ripples from the centre of a panel | | 35 | 52 | 903 | `cx`, `cy`, `w`, `h`, `pal` |
| [`fire.lsf`](fire.lsf) | A flame: heat rises, cools, and is re-seeded at the base | `sp` cooling, `ix` spark rate | 84 | 114 | 426 | `prev()` (frame memory) |
| [`falling_leaves.lsf`](falling_leaves.lsf) | Leaves falling from the middle of a strand out to both ends (a strand draped over a door frame), over a few green foliage pixels | `sp` fall speed, `ix` how many leaves | 161 | 224 | 219 | `hash`, `hsv`, cells and layers |
| [`falling_leaves_autumn.lsf`](falling_leaves_autumn.lsf) | A door-frame variant of the leaves: slowly wandering green dots with brown and amber leaves that fall at a gentle pace and pass *behind* the green | none | 145 | 217 | 226 | `hash`, `noise`, `blend`, `hsv` |
| [`autumn_twinkle.lsf`](autumn_twinkle.lsf) | Thanksgiving colours: every pixel slowly swells and fades on its own 6-12 s cycle and picks a new fall colour each time | none | 61 | 98 | 493 | `hash`, `sin`, `scale`, `hsv` |
| [`end_flames.lsf`](end_flames.lsf) | A crackling fire that starts at both ends, with wisps that peel off and drift all the way to the centre | none | 186 | 242 | 203 | `noise`, `tri`, `hash`, stateless |
| [`bench_heavy_noise.lsf`](bench_heavy_noise.lsf) | A benchmark, not a nice effect: 36 `noise()` calls per pixel | | 226 | 486 | 102 | used to calibrate the speed estimate |

"Fits" is the number of LEDs the formula can drive within the default 15 ms budget (see [docs/API.md](../docs/API.md#speed-budget-admission-control)). The figures are produced by the compiler's own estimate, not typed in.

## Measured on hardware

On an ESP32-C6 (WLED main, 160 MHz) with 50 LEDs, the worst single frame in a quiet run was about 0.5 ms for `rainbow_wave`, 0.7 ms for `plasma`, `comet_trail`, `twinkle` and `ripple2d`, 1.8 ms for `fire`, 2.6 ms for `falling_leaves` and 6.4 ms for `bench_heavy_noise`. The three autumn examples averaged 2.3 ms (`falling_leaves_autumn`), 1.1 ms (`autumn_twinkle`) and 2.8 ms (`end_flames`) as reported by the device on a 50-LED strip. These are one board and one strip; the `avgUs` and `worstUs` fields the device reports give the numbers for yours.

## Reading `falling_leaves.lsf`

It is the largest example and shows several techniques that carry over to other effects.

1. **Symmetry from the centre.** `let ct = 2 * i - (n - 1)` is the signed distance from the middle in half-pixels, so `abs(ct)` is the distance and `ct > 0` picks the side. Working in half-pixels keeps the centre exact for both even and odd pixel counts.
2. **A moving pattern with no state.** `fall` grows with time, and `fall - abs(ct)` is the pattern's coordinate: a feature at a fixed value of that coordinate appears at a larger distance as time passes, so it *moves outward*. This is stateless, so there is nothing to allocate or fall out of sync.
3. **Random but repeatable events.** `hash(cell)` turns a cell number into a pseudo-random number. Each 32-half-pixel cell on each side gets one hash, which decides whether it holds a leaf, where in the cell, how wide (1 or 2 pixels) and what colour (hue 0..43 is red through orange to yellow).
4. **One expression for "is this pixel inside the leaf".** `((w - offset) & 31) < width` is true for `width` positions starting at `offset`, and it wraps correctly for negative `w`.
5. **Layers.** The same idea with a different speed (`fall * 5 / 8`) and a different hash seed gives a second population of leaves. Layers are tried in order with nested `sel(...)`.
6. **Exactly 10% green.** `i % 10 == hash(i / 10) % 10` lights one pixel at a hashed position inside every block of ten.

The program is 895 of the 1024 source bytes, so the comments are short on purpose: comments count toward the limit.

## The autumn set

`falling_leaves_autumn.lsf`, `autumn_twinkle.lsf` and `end_flames.lsf` were written for a door frame with 50 LEDs (the middle of the strand is the top), and they all depend only on position and time, so they need no frame memory. Three techniques are worth borrowing:

- **A slowly wandering dot with soft edges.** In `falling_leaves_autumn`, each block of ten pixels holds one green dot whose position is `noise(block, t / 64)`, in 1/256 pixel. The brightness of a pixel falls off with its distance from the dot, so the dot slides between pixels instead of hopping.
- **Drawing something behind something else.** The final colour is `blend(leaf, green, dot_brightness)` where there is a leaf and plain green where there is not, so a leaf passing a dot leaves the green lit, and the dot's dim edges fade into the leaf.
- **Threads of different speeds.** In `end_flames`, the main fire is noise bands drifting at about 3 px/s, while thin wisps are moving packets at about 5 px/s. The wisps only start near the base (`e > 3`, fading in) so they look as if they peel off the fire, and they dim with distance on their way to the centre. Pixels combine the layers with `max()`.

Both of the larger formulas are close to the 1024-byte source limit (comments count), so their comments are short on purpose. To install them on a board as a rotating playlist, see [`board_setup/Readme.md`](../board_setup/Readme.md#per-device-effects).
