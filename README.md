# wled-usermod-ls-formula

[![CI](https://github.com/jmeagher/wled-usermod-ls-formula/actions/workflows/ci.yml/badge.svg)](https://github.com/jmeagher/wled-usermod-ls-formula/actions/workflows/ci.yml)

A [WLED](https://github.com/wled/WLED) usermod that lets you **define new effects without reflashing**. You write a small formula that says what colour one pixel should be, upload it over WiFi, and it runs as a normal WLED effect, with WLED's palettes, sliders, segments, presets and 2D matrix support.

WLED's effects are compiled C++, so adding one normally means rebuilding and flashing firmware. This usermod registers a few effect *slots* once, at build time. After that, each slot's behaviour is a formula you can change at any moment:

```lsf
# Hue travels along the strip; brightness ripples on top.
hsv(x + t / 8 + sp, 255, scale(sin(x * 2 - t / 6), 90, 255))
```

```sh
python3 tools/lsfx.py wled-1234.local push 0 examples/rainbow_wave.lsf --name "Rainbow Wave" --activate
```

The formula language is deliberately tiny: integers only, no loops, no memory access. That keeps it safe to accept from the network (a formula always finishes, and the worst it can do is look wrong) and fast enough for a microcontroller.

## What you can build

Everything in [`examples/`](examples/) is a complete formula; see [`examples/README.md`](examples/README.md) for what each looks like and how it works.

| | |
|---|---|
| Rainbow waves, plasma, gradients, pulses | stateless: just a function of position and time |
| Comets, sparkles, twinkling, falling leaves | `hash()` for repeatable randomness |
| Fire, trails, diffusion | `prev()` reads the previous frame, so a formula can have memory |
| Ripples and radial patterns on a matrix | `cx`, `cy`, `w`, `h` on 2D segments |

It is not a general scripting language. There are no strings and no fonts, so it cannot draw text (use WLED's own *Scrolling Text* effect for that).

## Install

You need a WLED build environment ([PlatformIO](https://platformio.org/) plus the WLED source; see WLED's [compiling guide](https://kno.wled.ge/advanced/compiling-wled/)). Then add the usermod to `custom_usermods` in `platformio_override.ini` in your WLED checkout:

```ini
[env:my_esp32]
extends = env:esp32dev
custom_usermods =
  ${env:esp32dev.custom_usermods}
  https://github.com/jmeagher/wled-usermod-ls-formula.git#v0.1.0
```

Build and flash as usual (`pio run -e my_esp32 -t upload`). After boot, WLED's effect list contains **LS Formula 1** to **LS Formula 4**. [`platformio_override.sample.ini`](platformio_override.sample.ini) has ready-made environments for ESP32, ESP32-S3 and ESP32-C6, and the build flags that tune the usermod.

**ESP32-C6.** WLED only supports the C6 on `main` (marked experimental) and not in any release yet, so use a `main` checkout for those boards. This usermod is tested against the commit pinned in [`scripts/dev-setup.sh`](scripts/dev-setup.sh).

## Use

1. Write or pick a formula (`.lsf` file).
2. Check it, then upload it into a slot and make a segment run it:

   ```sh
   python3 tools/lsfx.py wled-1234.local validate 0 my_effect.lsf       # compile only
   python3 tools/lsfx.py wled-1234.local push 0 my_effect.lsf --name "My Effect" --activate
   python3 tools/lsfx.py wled-1234.local info                          # slots, cost, speed
   python3 tools/lsfx.py wled-1234.local pull 0                        # read a slot back
   python3 tools/lsfx.py wled-1234.local clear 0
   ```

   `lsfx.py` needs only Python 3 and talks to WLED's normal JSON API. You can also use `curl`; see [docs/API.md](docs/API.md).
3. Adjust the **Speed**, **Intensity** and the three custom sliders in the WLED UI. They arrive in your formula as `sp`, `ix`, `c1`, `c2`, `c3`.

A bad formula is rejected with its line number and the old formula keeps running:

```text
$ lsfx.py wled-1234.local push 1 broken.lsf
error: unknown function (line 2)
  foo(a)
```

Formulas are saved on the device and survive a reboot. Save a WLED **preset** to keep which segment runs which slot.

### Try a formula without hardware

```sh
tests/run.sh                                     # builds build/lsf_cli and runs the tests
build/lsf_cli examples/fire.lsf -n 16 -f 60      # 16 pixels after 60 frames
build/lsf_cli examples/ripple2d.lsf -w 8 -h 8    # an 8x8 panel
```

## One-button control for a local display

For a display with no phone or network in the loop, the usermod can read **one push button** directly:

| Press | Does |
|---|---|
| **click** | next preset (in id order, wrapping; presets that exist only; powers the display on first if it was off) |
| **double click** | power on / off |
| **hold** | brightness goes up in 5 % steps (about four a second) while the button is down, wraps from 100 % back to 5 %, and **stops when you let go** |
| **double click and hold** (press, release, press again and keep holding) | brightness goes **down** in 5 % steps while the button is down, stops at 5 % (it never turns the display off), and stays where it is when you let go |

Set it up in **Settings > Usermods > ls_formula**: put the button's GPIO in *button pin* (`-1`, the default, switches the feature off) and save. No reboot is needed. *button active low* is on for a button that connects the pin to ground (the usual wiring; the internal pull-up is used, so no resistor is needed); turn it off for a button that connects the pin to 3.3 V. *brightness step* is the step size in percent (1 to 50).

- The pin must not be one WLED already uses (a button in *Time & Macros*, an LED data pin, ...). If it is taken, the usermod leaves it alone and `lsf.button.ok` in the state JSON stays `false`.
- A click that turns into a hold is not a click: pressing and holding right after a click is the dimming hold.
- A single click is acted on about 0.35 s after the release: that is how the double click is told apart.
- A button already held when the board starts is ignored until it is released (so holding BOOT during a reset does nothing).
- The timings are build flags: `LSF_BTN_DEBOUNCE_MS` (30), `LSF_BTN_DOUBLE_MS` (350), `LSF_BTN_HOLD_MS` (500), `LSF_BTN_STEP_MS` (250).
- Everything is local: it changes this device only and needs no WiFi. The gesture logic is in [`lsf_button.h`](lsf_button.h) and is tested on the desktop.

## Documentation

| | |
|---|---|
| [docs/LANGUAGE.md](docs/LANGUAGE.md) | The formula language: inputs, operators, every function, limits, error messages |
| [docs/API.md](docs/API.md) | The JSON API, replies, persistence, how a frame is drawn, the speed budget, serial notes |
| [examples/README.md](examples/README.md) | The examples, with measurements and a walk-through of the largest one |
| [AGENTS.md](AGENTS.md) | For AI coding agents and contributors: how the mod works and how to write and test effects |
| [platformio_override.sample.ini](platformio_override.sample.ini) | Build environments and tuning flags |

## Limits and safety

- **Size.** 1024 bytes of source, 256 instructions, 16 variables (adjustable at build time).
- **Speed budget.** A formula is accepted only while its estimated frame time for your LED count is under 15 ms. Too heavy for the strip: it is refused with the number of LEDs it *would* fit. This is checked at upload and again every frame, so adding LEDs later cannot make a slot stall WiFi.
- **Atomic updates.** The old formula runs until a new one has compiled and passed every check; the swap happens with rendering paused for an instant.
- **No authentication.** Like the rest of WLED's JSON API, anyone on your network can upload a formula. A formula cannot touch memory, files or the network, but keep the device off the public internet.

## Status

Early (v0.1). What has been verified:

- The compiler and interpreter: 4,400+ unit checks under AddressSanitizer and UBSan, plus fuzzing, in CI.
- On hardware: a Waveshare ESP32-C6 with a 50-LED strip, running WLED `main` at `fffa05a`: install, validation errors, persistence across reboot, all the examples, the upload tool over WiFi, and the over-budget rejection.

What has **not** been verified:

- A real 2D matrix. The 2D path is exercised only by the desktop simulator.
- Other chips. The original ESP32 was built (against WLED v16.0.1) but never run; the ESP32-S3 environment in the sample file has never been built. CI builds `esp32dev` and `esp32c6dev_8MB`.
- Strips much larger than 50 LEDs.
- The speed-estimate constants are calibrated for the ESP32-C6 only.

## Development

```sh
tests/run.sh                                   # engine tests with sanitizers; builds build/lsf_cli
python3 -I -m unittest discover -s tests -v    # upload tool + documentation tests
scripts/dev-setup.sh                           # fetch the pinned WLED into .wled and wire this tree in
cd .wled && npm ci && npm run build && pio run -e lsf_esp32c6
```

The engine (`lsf.h`, `lsf.cpp`) is plain C++11 with no WLED dependency, so it is tested on the desktop. `ls_formula.cpp` is the thin WLED side. See [AGENTS.md](AGENTS.md) for the architecture.

## License

[MIT](LICENSE) for this usermod. A firmware image that includes WLED is a combined work and WLED itself is under the [EUPL-1.2](https://github.com/wled/WLED/blob/main/LICENSE); that applies to what you distribute, not to this source file.
