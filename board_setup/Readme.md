# ESP32-C6 board setup

How to put WLED plus the LS Formula usermod on an ESP32-C6 board, repeatably. Written from a real install on 2026-11-12.

**Short version** (board plugged in over USB, macOS or Linux):

```sh
cp .wled.secrets.example .wled.secrets && chmod 600 .wled.secrets   # once; then put your WiFi in it
board_setup/c6-install.sh all --name "Desk" --mdns desk \
    --strip type=ws2812,order=grb,pin=7,len=50
```

That builds the firmware, flashes it, writes the WiFi/name/LED-strip configuration and waits for the board to answer at `http://desk.local`.

The rest of this file explains what that does and the C6-specific traps.

## What was installed

| | |
|---|---|
| Board | ESP32-C6FH4 (QFN32), revision v0.2, **4 MB flash** (embedded in the package), no PSRAM, native USB (USB-Serial/JTAG) |
| WLED | `main` at commit `fffa05a` (2026-11-06), the commit this usermod is tested against. `main` is required: no WLED release supports the C6 yet |
| PlatformIO env | `lsf_esp32c6_4MB` (defined in [`platformio_override.sample.ini`](../platformio_override.sample.ini)), which extends WLED's `esp32c6dev_4MB` |
| Result | 88% of the app partition, 28.7% of RAM; effects **LS Formula 1** to **4** appear in the effect list |

## C6-specific notes

1. **Check the flash size first; it picks the environment.** The repo's older `lsf_esp32c6` env extends `esp32c6dev_8MB`, whose partition table does not fit a 4 MB chip. Find out what you have:

   ```sh
   ~/.platformio/penv/bin/python ~/.platformio/packages/tool-esptoolpy/esptool.py \
     --port /dev/cu.usbmodem1101 flash_id        # look for "Detected flash size"
   ```

   | Flash | Environment |
   |---|---|
   | 4 MB | `lsf_esp32c6_4MB` (this guide) |
   | 8 MB | `lsf_esp32c6` |

   Use PlatformIO's own Python for esptool (`~/.platformio/penv/bin/python`): a system `python3` will fail with `No module named 'rich_click'`.

2. **Keep the WLED checkout outside this repository.** `scripts/dev-setup.sh` clones WLED into `./.wled`, inside the usermod tree. PlatformIO treats the usermod (`symlink://`) as a library and scans everything below it, which hides WLED's own `lib/wled_ADCmanager` and `lib/wled_espnow`. The build then dies with:

   ```
   wled00/wled.h:119:12: fatal error: wled_ADCmanager.h: No such file or directory
   ```

   `c6-install.sh` clones WLED to `~/.cache/wled-ls-formula/WLED` instead (override with `WLED_DIR`) and refuses a `WLED_DIR` inside the repo. Stock WLED builds and CI are unaffected because they check the two repositories out side by side.

3. **Native USB means the port is `/dev/cu.usbmodemXXXX`** (macOS) or `/dev/ttyACM0` (Linux), and it re-enumerates on reset. If upload cannot connect, hold **BOOT**, tap **RESET**, release **BOOT**, then retry.

4. **The default LED data pin is GPIO 7** (`-D DATA_PINS=7`). Change the pin and LED count in the WLED UI (*Config > LED Preferences*) to match your wiring; no rebuild needed.

5. **WLED writes to serial only a little** (`Ada` at boot), so a quiet console is normal. Do not use it as a health check; use the web UI.

## Steps in detail

Requirements: `git`, `node`/`npm`, `python3` and [PlatformIO](https://platformio.org/) (`pio`) on `PATH`.

The installer has four actions. `all` is the other three in order, and it checks your provisioning options first, so a typo fails in a second rather than after the build.

```sh
board_setup/c6-install.sh build                       # fetch the pinned WLED, build the web UI, compile
board_setup/c6-install.sh flash                       # bootloader + partition table + app
board_setup/c6-install.sh provision OPTIONS...        # WiFi, names, LED strips (see below)
board_setup/c6-install.sh all OPTIONS...              # build + flash + provision
PORT=/dev/cu.usbmodem1101 board_setup/c6-install.sh flash   # name the port instead of auto-detecting
```

Variables: `PORT`, `WLED_REF` (commit or tag), `WLED_DIR`, `ENV_NAME` (e.g. `lsf_esp32c6` for an 8 MB board).

For a completely clean install (wipes saved WLED settings and presets, and any leftover filesystem from a previous firmware), erase first:

```sh
cd ~/.cache/wled-ls-formula/WLED && pio run -e lsf_esp32c6_4MB -t erase
```

Then run `flash` and `provision` (an erase removes the configuration too). The board may need the BOOT/RESET sequence from note 3 after an erase.

## Provisioning: WiFi, names and LED strips

`provision` writes the device's configuration straight into its filesystem partition (WLED's `cfg.json`, `wsec.json` and `presets.json`), so there is no access-point step and no rebuild. It only touches the filesystem partition, so it takes a few seconds and can be repeated at will. **Each run replaces the whole configuration, including saved presets and any formulas stored on the board.**

### Secrets: `.wled.secrets`

Local, gitignored, never passed on the command line or printed:

```ini
WIFI_SSID=my-home-network       # the network the board should join
WIFI_PASSWORD=...               # 8-63 characters, or empty for an open network
AP_PASSWORD=...                 # the board's own access point; 8-63 characters
```

Without `AP_PASSWORD` the access point keeps WLED's default password (`wled1234`) and you get a warning. Use `--secrets PATH` for another file or `--no-wifi` to skip the home WiFi.

### Options

| Option | Meaning |
|---|---|
| `--name NAME` | WLED server name shown in the UI and apps (1-32 characters) |
| `--mdns HOST` | local DNS name; the board answers at `HOST.local` (lowercase letters, digits, hyphens) |
| `--ap-ssid SSID` | the board's own access-point name (default `WLED-AP`) |
| `--strip SPEC` | one LED strip; repeat for more strips on the same board |
| `--dry-run` | validate and print the result (passwords hidden); touch nothing |
| `--out DIR` | also save the generated files to `DIR` (they contain passwords); implies `--dry-run` |
| `--no-verify` | do not wait for the board on the network afterwards |

A strip is `pin=N,len=N` plus optional `type=` (default `ws2812`) and `order=` (default `grb`), and `reversed=yes`:

| Key | Values |
|---|---|
| `type` | `ws2812` (also WS2813/WS2815 clones), `ws2813`, `ws2811`, `ws2811_400`, `apa106`, `sk6812` (RGBW) |
| `order` | `grb`, `rgb`, `brg`, `rbg`, `bgr`, `gbr` |
| `pin` | GPIO 0 to 23. GPIO 12 and 13 are refused (they are the USB port); 4, 5, 8, 9 and 15 are strapping pins and give a warning |
| `len` | number of LEDs. At most 4096 in total and 8 strips on a C6 |

```sh
# One strip: 50 WS2812 in GRB order on GPIO 7
board_setup/c6-install.sh provision --name "Desk" --mdns desk --strip type=ws2812,order=grb,pin=7,len=50

# Two strips on one board
board_setup/c6-install.sh provision --name "Shelf" --mdns shelf \
    --strip type=ws2812,order=grb,pin=7,len=50 --strip type=sk6812,order=grb,pin=6,len=30

# Look before you flash
python3 board_setup/provision.py --name Desk --mdns desk --strip pin=7,len=50 --dry-run
```

Each strip becomes one WLED output (LEDs numbered consecutively, strip 1 first) **and** one segment, named `Strip N (GPIO pin)`, in a boot preset (preset 1, applied at power-up). So every strip can be given its own effect and colours from the start.

Before writing anything, `provision` reads the chip and prints its MAC address (`board: ESP32 MAC ... firmware present`), so you can see which physical board it is about to change. If the bootloader or the application is missing (a blank board), it stops and tells you to run `flash` first, or to use `all`. `provision` on its own is for boards that already have the firmware.

When it finishes, `provision` waits up to 90 s for `http://<mdns>.local` and checks that the name, LED count and strip settings the board reports match what you asked for, and that the answering device's MAC is the chip it just wrote (so a different WLED device with the same name or address is caught). If it cannot reach the board it says so and exits non-zero. The files were still written; look for the board in your router's client list.

Adding a chip type or another board is a one-line change to the `LED_TYPES` or `BOARDS` table at the top of `board_setup/provision.py`. `python3 -I -m unittest discover -s tests` covers the validation and the generated files.

## Per-device effects

A device can carry its own LS Formula effect and start in it. These live in `board_setup/devices/<name>/`: a `.lsf` formula plus an `apply.sh` that uploads it, saves it as a preset and makes that preset the boot preset.

| Device | Effect |
|---|---|
| `mbr-door` | `falling_leaves.lsf`: about 10% of the LEDs are green dots that slowly wander a few pixels; 2-3 pixel autumn-coloured leaves (deep orange through amber) spawn at the middle about once a second and fall to either end in about 4 s |

```sh
board_setup/devices/mbr-door/apply.sh                 # defaults to mbr-door.local
board_setup/devices/mbr-door/apply.sh 192.168.1.50    # or give an address
```

**Run it after `provision`**: provisioning replaces the whole filesystem, which removes formulas and presets. Preview a formula with no hardware first (`build/lsf_cli FILE -n 50 -t 5000 -f 1`; `-t` is the time in ms). The formula is a pure function of time, so it needs no frame memory and costs about 2 ms a frame on 50 LEDs.

## First boot without provisioning

If you flashed only the firmware, WLED has no WiFi credentials and starts its own access point:

1. Join the WiFi network **`WLED-AP`**, password **`wled1234`**.
2. Open `http://4.3.2.1`. Under *Config > WiFi Setup*, enter your home network's name and password and save; the board reboots and joins it. (Find its new address from your router, or try `http://wled-<last digits of the MAC>.local`.)
3. In *Config > LED Preferences*, set the LED type, data pin (default 7) and LED count.
4. Open the effect list: **LS Formula 1** to **LS Formula 4** are the usermod's slots.

## Verify the usermod

```sh
python3 tools/lsfx.py <board-address> info
python3 tools/lsfx.py <board-address> push 0 examples/rainbow_wave.lsf --name "Rainbow Wave" --activate
```

`info` lists the slots, their cost and the speed estimate. See the top-level [README](../README.md#use) for the rest of the tool.

## Updating

- **New usermod code only:** `board_setup/c6-install.sh build` then `flash`. The build picks up this working tree directly, and a firmware flash leaves the configuration alone.
- **Changed WiFi, names or strips:** just rerun `provision`; no rebuild.
- **New WLED:** set `WLED_REF` to the commit or tag. This usermod is only tested against the pinned commit; CI's nightly `main` job is the early warning for API changes.
- Formulas and presets stored on the board survive a firmware flash, but not `provision` or the erase step, which replace the filesystem.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `No module named 'rich_click'` | Ran esptool with the system Python; use `~/.platformio/penv/bin/python` |
| `fatal error: wled_ADCmanager.h` | WLED checkout is inside the usermod tree; see note 2 |
| `Couldn't locate module` | In `custom_usermods`, a URL or `symlink://` entry must be on its own line |
| Upload cannot connect | BOOT/RESET sequence (note 3); try another USB cable (some are charge-only) |
| Boots but no `WLED-AP` network | Wait ~30 s; if still missing, run the erase step and reflash |
