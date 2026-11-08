# AGENTS.md

Guidance for AI coding agents and contributors working on this repository, or using it to write new effects. Read this before changing code or writing a formula. Human-oriented docs are in [README.md](README.md), [docs/LANGUAGE.md](docs/LANGUAGE.md) and [docs/API.md](docs/API.md); this file is the map and the rules.

> Every code block tagged `lsf` in this file is a complete program, and `tests/test_docs.py` compiles all of them. If you add a recipe, tag it `lsf` and run the tests.

## 1. What this is

A [WLED](https://github.com/wled/WLED) usermod. WLED effects are compiled C++ functions, so a new effect normally needs a firmware rebuild. This usermod registers `LSF_SLOTS` (default 4) effects once at boot, "LS Formula 1..4". Each slot runs a **formula** (a small integer expression language) that is uploaded over WiFi and saved on the device. A formula returns one pixel's colour; WLED calls it for every pixel of the segment, every frame.

It is for defining effects **as data**. It is not a general scripting language and it does not draw text.

## 2. Repository map

| Path | Role |
|---|---|
| `lsf.h`, `lsf.cpp`, `lsf_sintab.inc` | The engine: compiler, verifier, interpreter, speed estimate. **Plain C++11, no WLED/Arduino includes, no heap, no floats.** Compiled on the desktop for tests and into the firmware. |
| `ls_formula.cpp` | The WLED side: registers effects, receives uploads, persists, renders. Compiled only inside a WLED build (`#ifdef ARDUINO_ARCH_ESP32`). |
| `library.json` | PlatformIO manifest. `libArchive: false` is required by WLED, and `srcFilter` lists exactly the two `.cpp` files so `tools/` and `tests/` (which contain a `main()`) are never linked into firmware. |
| `tools/lsfx.py` | Upload/validate/activate/pull/clear tool. Standard library only. |
| `tools/lsf_cli.cpp` | Desktop renderer: compile a `.lsf` and print pixels. |
| `tools/gen_sintab.py` | Generates `lsf_sintab.inc`. CI fails if the checked-in table differs. |
| `tests/` | `run.sh` + `test_lsf.cpp` (engine), `test_lsfx.py` (tool against a fake device that uses the real compiler), `test_docs.py` (every `lsf` block in the docs compiles). |
| `examples/` | Complete formulas with a README. All are compiled by the engine tests. |
| `scripts/dev-setup.sh` | Fetches the pinned WLED into `.wled/` and wires this tree in. |
| `platformio_override.sample.ini` | Build environments and tuning flags. |
| `.github/workflows/ci.yml` | Host tests, plus firmware builds against the pinned WLED commit and WLED `main`. |

## 3. How the mod works

### Lifecycle

1. **Boot.** WLED loads `cfg.json`, then calls `readFromConfig()` (before `setup()`): each stored source is recompiled into its slot. Bytecode is never stored. If the usermod's section is missing it returns `false` so WLED writes defaults.
2. **`setup()`.** `strip.addEffect(255, &mode_lsf<S>, g_slots[S].meta)` registers each slot, and the returned id is kept as `fxId`. `registered` becomes true; from then on, changes happen with the strip suspended.
3. **Upload.** `POST /json/state` with `{"lsf":{...}}` reaches `readFromJsonState()`: `compile -> verify -> budget check -> swap -> configNeedsWrite = true`. The result is stored in `g_last` and returned by `addToJsonState()`.
4. **Every frame.** WLED calls `mode_lsf<S>()` -> `renderSlot(S)` for each segment running the effect.

### One frame: `renderSlot`

- Empty, disabled or over-budget slot: fill black and return.
- 2D only when `strip.isMatrix && SEGMENT.is2D()`; otherwise 1D, including a strip trailing a matrix.
- Admission control: `lsf_estimate_us(prog, pixels) > LSF_BUDGET_US` marks the slot over budget and fills black.
- If `prog.usesPrev`, `SEGENV.allocateData(pixels * 6)` gives two RGB frames (previous, next) in the segment's effect data; on failure show the first colour.
- Per pixel: fill the `LsfCtx.in[]` inputs, call `lsf_run`, write the colour with `setPixelColor` (1D) or `setPixelColorXY` (2D), and copy it to the `next` buffer. After the loop, `next` is copied over `prev`.
- Time the loop with `micros()`; update `avgUs` and `worstUs`; three frames over 60 ms disable the slot.

### Invariants (do not break these)

- `lsf_run` assumes a program that passed `lsf_verify`. `lsf_compile` always verifies; the verifier is independent of the code generator on purpose, so a compiler bug cannot become a memory-safety bug.
- Install is **all or nothing**: compile into a heap temporary (`new (std::nothrow) LsfProgram`, because the async_tcp task stack is small), check everything, then `strip.suspend(); strip.waitForIt();` copy in; `strip.resume()`. A failed upload leaves the old program running.
- The engine core never allocates, uses no floating point and has no undefined behaviour on any input: wrapping unsigned arithmetic, defined division by zero, shift counts masked. The tests run under ASan and UBSan and fuzz this.
- The metadata string for a slot is a RAM buffer (`meta`) that WLED keeps a pointer to. Never move or free it.
- `LsfProgram::n` is 16 bits; `static_assert`s in `lsf.cpp` guard the limits against their field widths.

### Memory

About 3.2 KB per slot is static: a 256-instruction program is 2 KB (8 bytes per instruction), plus 1 KB of source and small fields. Four slots are about 13 KB. A formula that calls `prev`/`prev2` also uses 6 bytes per pixel of heap while running.

### The speed budget

`cost` is a weighted per-pixel operation count computed at compile time (`kFn[]` costs in `lsf.cpp`; simple operators 1, `mul/div/mod` 2). Estimated frame time = `pixels * (0.3 * cost + 1)` microseconds, calibrated on an ESP32-C6 (160 MHz) against eight real formulas, and `tests/test_lsf.cpp::testEstimate` fails if any of them leaves 0.9x..1.5x of its measured worst frame. If you change a function's cost, measure it on hardware first. See [docs/API.md](docs/API.md#speed-budget-admission-control).

## 4. Writing effects

### Workflow

1. **Design the effect as a function of position and time.** Prefer stateless formulas (`x`, `i`, `t` only). Use `prev()` only when the effect truly needs memory (fire, trails, diffusion).
2. **Write `my_effect.lsf`.** Keep comments short: they count toward the 1024-byte limit.
3. **Preview on the desktop** (no hardware):
   ```sh
   tests/run.sh                                        # once, builds build/lsf_cli
   build/lsf_cli my_effect.lsf -n 50 -t 3000           # 50 pixels at t = 3000 ms
   build/lsf_cli my_effect.lsf -n 50 -f 120            # after 120 frames (needed for prev())
   build/lsf_cli my_effect.lsf -w 8 -h 8 -t 500        # a panel
   ```
   It prints one hex colour per pixel and a line on stderr with instruction count, locals, `cost` and whether it uses `prev`. `pal()` is a rainbow stand-in on the desktop; WLED uses the real palette.
4. **Check against a device** without installing: `python3 tools/lsfx.py HOST validate SLOT my_effect.lsf`. It reports the estimate and how many LEDs the formula fits.
5. **Install and run:** `python3 tools/lsfx.py HOST push SLOT my_effect.lsf --name "My Effect" --activate`.
6. **Measure:** `python3 tools/lsfx.py HOST info` shows `avg` and `worst` frame time next to the estimate. Judge by `avg`: `worst` includes preemption by WiFi and HTTP.
7. If the effect is worth keeping, add it to `examples/` with a line in `examples/README.md`; the engine tests then compile it automatically (add its name to the list in `tests/test_lsf.cpp::testExamplesCompile`).

### The effect "configuration"

An effect is **two small pieces of data**: the formula (a `.lsf` file) and, optionally, the WLED segment settings that make it look right.

The formula is plain text. Its inputs are: position `x`, `y` (0..255), index `i`, count `n`, column/row `cx`, `cy`, size `w`, `h`, time `t` (ms), the sliders `sp`, `ix`, `c1`, `c2`, `c3`, and the segment colours `k0`, `k1`, `k2`. The final expression is the colour. The full reference is [docs/LANGUAGE.md](docs/LANGUAGE.md).

The segment settings are ordinary WLED state: effect id (`fx`), `sx` (= `sp`), `ix`, `c1`, `c2`, `c3`, palette `pal`, colours `col`. For example, to run the formula in slot 3 with chosen sliders:

```json
{"seg": [{"id": 0, "fx": 171, "sx": 128, "ix": 200, "c1": 100, "pal": 0,
          "col": [[255, 160, 0], [0, 0, 0], [0, 0, 0]]}]}
```

Read the slot's real `fx` from `lsfx.py info` or the `lsf.slots[].fx` field of `/json/state`; it varies by build.

### Techniques

**Position and time.** `x` is 0..255 across the segment, so a pattern that should look the same on any length uses `x`; one that should be per-pixel uses `i`. A moving pattern is a function of `x + t / k` (moves toward lower `x`) or `x - t / k` (toward higher). Divide `t` early to keep numbers small.

**Sliders.** `sp` and `ix` are 0..255. Map them with `scale(sp, lo, hi)` (linear, input clamped), or `4 + sp / 16` for "mostly 4, up to 20". `c3` is only 0..31.

**Colour.** `hsv(h, s, v)` with `h` 0..255 wrapping (0 red, 43 yellow, 85 green, 128 cyan, 171 blue, 213 magenta). `pal(i)` samples the segment's palette so the user can change the look from the UI. `dim(c, f)` and `blend(c1, c2, f)` work on whole colours.

**Randomness without state.** `hash(a)` is repeatable, so use it on a *cell*: `hash(cell)` decides the whole life of an event. Take different bits of one hash for different decisions (`h & 255`, `(h >> 8) % 28`) or hash it again (`hash(h + 12345)`).

**Motion without state.** A feature that sits at a fixed value of `fall - distance` moves outward as `fall` grows with time. This is how `examples/falling_leaves.lsf` makes leaves travel with no buffers.

**Symmetry.** `2 * i - (n - 1)` is the signed distance from the middle in half-pixels and is exact for even and odd `n`; `abs()` of it is the distance and `> 0` picks the side.

**Memory.** `prev(d)` is the previous frame's colour of pixel `i + d`. Trails: `dim(prev(0), 230)`. Diffusion: average neighbours. Heat/intensity can be kept in a colour channel and read back with `red()`.

### Recipes

A scrolling palette gradient:

```lsf
# A palette gradient that scrolls. sp sets the speed.
pal(x + t * (sp + 16) / 2048)
```

Breathing in the segment's first colour:

```lsf
# The whole segment breathes. sp sets the rate.
dim(k0, tri(t / (4 + (255 - sp) / 8)))
```

Random white sparkles over a dim palette:

```lsf
# ix sets how many sparkles.
sel((hash(i * 7919 + t / 60) & 255) < ix / 4, 0xFFFFFF, dim(pal(x), 40))
```

Moving outward from the middle:

```lsf
# Palette moving outward from the middle of the strip.
pal(abs(2 * i - (n - 1)) * 255 / n - t / 16)
```

Evenly spaced running dots (`ix` sets the spacing):

```lsf
sel((i - t / 64 * (sp + 16) / 16) % (4 + ix / 16) == 0, k0, k1)
```

Fading random pixels (uses the previous frame, so test with `-f 100`):

```lsf
sel(hash(i + t / 50) % 40 == 0, hsv(t / 8, 255, 255), dim(prev(0), 230))
```

Rings spreading from the centre of a panel:

```lsf
let d = abs(cx - w / 2) + abs(cy - h / 2)
pal(d * 12 - t / 8)
```

Cross-fading between the segment's two colours (`c1` sets the speed):

```lsf
blend(k0, k1, tri(t / (2 + (255 - c1) / 8)))
```

### Pitfalls

- **Reserved names.** `c1`, `c2`, `c3` (and `x`, `y`, `i`, `n`, `t`, `w`, `h`, `sp`, `ix`, `k0..k2`, `cx`, `cy`) are inputs, and every function name is taken. A `let` that reuses one fails with `name already in use`.
- **Comments count.** The 1024-byte limit includes them. Trim comments before cutting code.
- **Newlines.** A newline ends a statement unless you are inside parentheses. Wrap a long `sel(...)` in its own parentheses; do not break after an operator outside them.
- **Integer division truncates toward zero**, so `-7 / 2` is `-3`, and `%` can be negative. `>>` floors. Use `& 31` rather than `% 32` for a wrapping index that may be negative.
- **Precedence.** `&` binds *looser* than `==` here as in C: write `(h & 255) < ix`, not `h & 255 < ix`. When in doubt, add parentheses.
- **`&&`, `||` and `sel` evaluate every operand.** There is no short circuit; this only matters for cost, not correctness.
- **`sin` and `tri` return 0..255**, not signed. `sin(a) - 128` gives the signed wave. `sin`'s argument is a phase where 256 is a full turn.
- **`scale(v, lo, hi)` clamps `v` to 0..255** first. Use `mix(a, b, f)` for interpolation without that clamp on `a` and `b`.
- **`noise(a, b)` takes 8.8 fixed point**: 256 units is one lattice cell, so pass `x * 4` or `t / 8`, not `x / 100`.
- **Overflow wraps.** `t * 20` overflows 32 bits after 107 million ms (about 30 hours), which shows as one sudden jump. Divide `t` first (`t / 64 * k`) when a long-running effect matters. `t` also becomes negative after about 25 days.
- **Colours are integers.** `dim`, `blend`, `red`, `green`, `blue` take whole colours; adding two colours with `+` does not saturate and will carry between channels.
- **16 variables, 256 instructions, depth 16.** Share work in `let`; long `sel` chains with repeated subexpressions use many instructions.
- **Budget.** Heavy formulas are refused for a big strip. Use `validate` to see `fits N LEDs`; `noise` and `blend` are the expensive calls.
- **2D vs 1D.** On a strip `y` is 0, `h` is 1, `cx` equals `i`. Write 2D formulas in terms of `cx, cy, w, h` and they degrade to a line.
- **Opening the serial port resets the board** and long serial requests are dropped; use HTTP (see [docs/API.md](docs/API.md#trying-it-without-wifi-serial)).

## 5. Testing

```sh
tests/run.sh                                   # engine: compile with -Werror, ASan+UBSan, run; builds build/lsf_cli
python3 -I -m unittest discover -s tests -v    # lsfx.py against a fake device, and every lsf block in the docs
python3 tools/gen_sintab.py | diff - lsf_sintab.inc   # table is in sync
```

CI runs all three, then builds `esp32dev` and `esp32c6dev_8MB` firmware against the pinned WLED commit (required) and against WLED `main` (allowed to fail: it is the early warning that WLED's API moved).

To test on a device, `tools/lsfx.py HOST info` first (it fails clearly if the usermod is not in the firmware). Hardware checks worth repeating after an engine change: install, a bad upload leaves the old formula running, reboot persistence, and `avgUs` against `estUs`.

## 6. Changing the engine

- Keep `lsf.*` free of WLED/Arduino headers, heap allocation and floating point. Anything that touches WLED belongs in `ls_formula.cpp`.
- **Adding a function:** add it to the `LsfFn` enum and the `kFn[]` table (name, arity, cost) in `lsf.cpp`, implement it in the `switch` in `lsf_run`, add tests (values, edge cases, arity), document it in `docs/LANGUAGE.md`, and measure its cost on hardware before choosing the weight. Function names become reserved words, so check existing examples do not use the name as a variable.
- **Changing a limit:** update `lsf.h`, the `static_assert`s if a field width is involved, the docs (`LANGUAGE.md`, `README.md`) and the device `limits` reply. The tests derive their limits from the macros.
- **Changing the estimate or a cost weight:** re-measure on hardware and update the calibration points in `testEstimate`.
- Every new behaviour needs a test; compile errors need a test that asserts the message and line.

## 7. Known gaps

- The 2D path has only been run in the desktop simulator, not on a real matrix.
- Slider labels are fixed ("Speed, Intensity, P1, P2, P3"): WLED reads the metadata string once at registration.
- There is no web UI for editing formulas; use `tools/lsfx.py` or `curl`.
- No authentication beyond WLED's own.
- Speed-estimate constants are calibrated on the ESP32-C6 only.
