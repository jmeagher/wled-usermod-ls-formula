# The formula language

A formula computes **one pixel's colour** from its position, the time and the WLED sliders. WLED runs it once per pixel, every frame. There are no loops, no strings, no arrays and no floating point, which is what makes it safe to accept from the network: a formula always finishes, and it can only ever write a colour.

> Every code block tagged `lsf` in this file is a complete program, and `tests/test_docs.py` compiles all of them.

## Program shape

```lsf
# comment
let wave = sin(x * 2 - t / 6)
hsv(x + t / 8 + sp, 255, scale(wave, 90, 255))
```

- A program is zero or more `let name = expression` statements followed by **exactly one final expression**. The final expression is the pixel colour, packed as `0xRRGGBB`.
- Statements end at a newline or a `;`.
- `#` starts a comment that runs to the end of the line.
- **Newlines inside parentheses are whitespace**, so a long call can be wrapped:

```lsf
sel(x > 128,
  hsv(t / 8, 255, 255),     # right half
  hsv(t / 8 + 128, 255, 255))  # left half
```

  Outside parentheses a newline ends the statement, so `1 +` followed by a newline is an error.
- A variable is visible only *after* its own definition (no self reference), and may not reuse the name of an input or a function.

## Values

All values are **32-bit signed integers**.

- Arithmetic wraps on overflow and never traps.
- `x / 0` and `x % 0` give `0`.
- `>>` is an arithmetic (sign-extending) shift. Shift counts use their low 5 bits.
- Comparisons and logical operators give `0` or `1`; any non-zero value is "true".
- Numbers are decimal (`255`) or hex (`0xFF8800`). Floats and suffixes such as `1.5` or `12px` are rejected.
- A colour is just an integer, `0xRRGGBB`. Only the low 24 bits of the final result are used.

## Inputs

| Name | Range | Meaning |
|---|---|---|
| `x` | 0..255 | position across the segment (0 if it has one pixel) |
| `y` | 0..255 | position down the segment (always 0 on a strip) |
| `i` | 0..n-1 | pixel index (2D: `row * w + column`) |
| `n` | | pixel count of the segment |
| `cx`, `cy` | | column and row (strip: `cx = i`, `cy = 0`) |
| `w`, `h` | | width and height (strip: `w = n`, `h = 1`) |
| `t` | ms | milliseconds since boot. It is a 32-bit counter read as a signed integer, so it goes **negative after about 25 days** of uptime; use `&` or `>>` rather than `%` on it if that matters |
| `sp`, `ix` | 0..255 | WLED **Speed** and **Intensity** sliders |
| `c1`, `c2` | 0..255 | WLED custom sliders 1 and 2 |
| `c3` | 0..31 | WLED custom slider 3 (WLED gives it a smaller range) |
| `k0`, `k1`, `k2` | colour | the segment's three colours |

Notice that `c1`, `c2` and `c3` are input names, so they cannot be used as variable names.

## Operators

From lowest to highest precedence; all binary operators are left associative.

| Level | Operators |
|---|---|
| 1 | `\|\|` |
| 2 | `&&` |
| 3 | `\|` |
| 4 | `^` |
| 5 | `&` |
| 6 | `==` `!=` |
| 7 | `<` `>` `<=` `>=` |
| 8 | `<<` `>>` |
| 9 | `+` `-` |
| 10 | `*` `/` `%` |
| unary | `-` `!` `~` |

`&&` and `||` evaluate both sides; there is nothing with side effects to skip.

## Functions

| Function | Result |
|---|---|
| `sin(a)` | 0..255. `a` is a phase where 256 is one full turn (it wraps), 0 gives 128, 64 gives 255 |
| `tri(a)` | triangle wave 0..254 with period 256 |
| `noise(a, b)` | smooth value noise 0..255. Coordinates are 8.8 fixed point: 256 units is one lattice cell |
| `hash(a)` | pseudo-random 0..65535, the same every time for the same `a` |
| `scale(v, lo, hi)` | maps `v` (clamped to 0..255) linearly onto `lo..hi` |
| `min(a, b)` `max(a, b)` `abs(a)` | as expected |
| `clamp(v, lo, hi)` | limits `v` to `lo..hi` (swapped bounds are accepted) |
| `mix(a, b, f)` | integer interpolation, `f` 0..255 |
| `sel(c, a, b)` | `a` when `c` is non-zero, else `b` |
| `hsv(h, s, v)` | colour. `h` wraps at 256 (0 red, 43 yellow, 85 green, 128 cyan, 171 blue, 213 magenta); `s` and `v` clamp to 0..255 |
| `rgb(r, g, b)` | colour; each channel is clamped to 0..255 |
| `pal(i)` | colour from the segment's current WLED palette; `i` wraps at 256 |
| `red(c)` `green(c)` `blue(c)` | one channel of a colour, 0..255 |
| `dim(c, f)` | scales a colour by `f/255` |
| `blend(c1, c2, f)` | mixes two colours, `f` 0..255 |
| `prev(d)` | colour of pixel `i + d` in the **previous frame**; 0 outside the segment |
| `prev2(dx, dy)` | the same on a 2D segment, relative to `(cx, cy)` |

`prev` and `prev2` give a formula **memory** of the last frame. That is what makes fire, trails and diffusion possible. A formula that calls them keeps two RGB frame buffers (6 bytes per pixel) while it runs. If that allocation fails the segment shows its first colour.

## Limits

| Limit | Default | Build flag |
|---|---|---|
| Source size | 1024 bytes | `LSF_MAX_SRC` (comments count) |
| Instructions after compiling | 256 | `LSF_MAX_INSNS` (8 bytes of RAM each, per slot) |
| `let` variables | 16 | `LSF_MAX_LOCALS` |
| Expression depth | 16 | `LSF_MAX_STACK` |
| Variable name length | 12 | |

Besides these, the **speed budget** applies: a formula is accepted only while its estimated frame time on your strip stays under 15 ms. See [API.md](API.md#speed-budget-admission-control).

## Compile errors

The compiler reports a message and a line number. Messages you can get:

| Message | Meaning |
|---|---|
| `source too long`, `program too long`, `expression too deep` | a limit above was exceeded |
| `too many variables`, `name too long` | more than 16 `let`s, or a name over 12 characters |
| `name already in use` | a `let` reuses an input, a function or an earlier variable |
| `unknown name`, `unknown function`, `wrong argument count` | the identifier or call is not valid |
| `expression expected` | an operator or `(` has nothing after it (or a newline ended the statement early) |
| `')' expected`, `',' or ')' expected` | unbalanced parentheses or a missing comma |
| `integers only`, `number too large`, `hex digits expected` | bad numeric literal |
| `final expression must be last`, `missing final expression` | the program must end with exactly one expression |
| `unexpected text after expression`, `unexpected character` | something the grammar does not allow |
| `NUL in source` | a zero byte in the text |
