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

A 2D panel is a strip with `matrix=WxH` instead of `len=` (the LED count is W×H):

| Key | Meaning |
|---|---|
| `matrix` | panel size, e.g. `32x8`. It must be the first `--strip` (WLED puts the panel at LED 0 and plain strips after it); only one per board |
| `vertical` | `yes` (default): LED numbers run down the columns; `no`: along the rows |
| `serpentine` | `yes` (default): the data line zig-zags; `no`: every column or row runs the same way |
| `bottom`, `right` | `yes` if LED 0 is at the bottom, or at the right (both default `no`: top-left) |

To find the right flags, light the logical corners (`i` array on segment 0: LEDs 0, W-1, W*(H-1), W*H-1 in red, green, blue, white). They must show up top-left, top-right, bottom-left, bottom-right.

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

## Per-device setup

**`bar`**: a 32×8 WS2812 panel on GPIO 18 plus a 50-LED strip on GPIO 19. Its panel starts at the bottom-right (checked with the corner test above):

The Bar needs the **`lsf_esp32c6_4MB_longtext`** firmware environment: WLED limits a segment name to 64 characters, the Scrolling Text effect shows the segment name, and the Happy Hour message is 84 characters. That environment raises the limit to 96 (`-D WLED_MAX_SEGNAME_LEN=96`). WLED's web UI still limits a name field to 64 characters, so change these texts through `apply.sh` (or the JSON API), not by retyping the name in the UI.

```sh
ENV_NAME=lsf_esp32c6_4MB_longtext board_setup/c6-install.sh all --name Bar --mdns wled-bar --ap-ssid wled-bar \
    --strip pin=18,matrix=32x8,bottom=yes,right=yes --strip pin=19,len=50
board_setup/devices/bar/apply.sh                            # then the presets below (defaults to wled-bar.local)
```

`apply.sh` saves three presets. None of them stores the brightness, so the slider stays manual:

| Preset | |
|---|---|
| 1 **"Birdie's Bar"** | the boot preset: what the board shows after power-up, and after Happy Hour ends. Has quick-load label `BB`, which makes it a "scene" in the touch controller's preset list |
| 2 "Happy Hour sign" | the same display with the message "Birdie's Bar, Happy Hour specials available now. Ask your server about our specials.", and **red and green added to the sparkles** on both the panel and the strand |
| 3 **"Happy Hour"** | a playlist: runs preset 2 for **4 hours** (`HAPPY_HOUR_SECONDS`, default 14400), then switches to preset 1 |

Start Happy Hour by choosing preset 3 in WLED (or `curl -X POST http://wled-bar.local/json/state -H 'Content-Type: application/json' -d '{"ps":3}'`). If the board loses power during Happy Hour it starts in preset 1 again, and the 4 hours are not resumed. The playlist's timer does not advance while the display is switched off.

All the text scrolls at twice the original speed (Scrolling Text speed 214 for the main text and 235 for the long message: one column every 83 ms and 66 ms).

Each display preset has three segments:

| Segment | What it shows |
|---|---|
| 0, the 32×8 panel (named after its text: "Birdie's Bar") | Scrolling Text in light blue. The text is the segment's name |
| 1, "Sparkles", the same panel | [`bar_sparkles.lsf`](../examples/bar_sparkles.lsf), blended over the text with mode *Add*: about 8 yellow, white or pink sparkles at once. In Happy Hour, custom slider 1 is 255, which adds red and green |
| 2, the strand on GPIO 19 | built-in *Colortwinkles*: many more sparkles than the panel. Normally the palette "Colors Only" (yellow, white, pink). In Happy Hour it uses the custom palette [`happy_hour_palette.json`](devices/bar/happy_hour_palette.json) (yellow, white, pink, red, green), uploaded by `apply.sh` as `/palette0.json` (palette id 200), and spawns sparkles more often |

After a reboot the brightness is the board's default (128) until you move the slider.

**`backyard`**: two 100-LED WS2812 strands, "Star" on GPIO 18 and "Tree" on GPIO 19 (plugged in at the top of each shape). `board_setup/devices/backyard/segments.py` (defaults to `wled-backyard.local`; give the IP address if mDNS is slow) defines 28 segments, saves test presets and playlists, and makes the boot preset (1) keep the layout across reboots. Re-runnable; run it after `provision`. `--dry-run` prints everything, `--step-seconds N` sets the time per test step (default 4), `--ornament-at start` is for a tree strand whose spare LEDs are at the plug end.

| Segments | LEDs | Notes |
|---|---|---|
| 0 `Star`, 1 `Tree` | 0-99, 100-199 | the whole strands |
| `Star Top-R`, `Lower-R`, `Upper-L`, `Upper-R`, `Lower-L`, `Top-L` | 8 or 16 each | the star's **outline**: the outer legs of the five lines. A point is the two legs that meet at a corner; the top point wraps round the strand's ends, so it is two segments |
| `Star Inner 1`-`5` | 4 each | the inner pentagon: the middle of each line (the interior crossing pieces) |
| `Star Line 1`-`5` | 20 each | the five lines in hanging order (1 = top to lower right) |
| `Tree Body` / `Tree Ornament` | 100-180 / 181-199 | the 81 LEDs of the tree shape and the 19 spare ones bundled at the top |
| `Tree Tier 1 R`, `Tier 1 L`, `Tier 2 R`, `Tier 2 L` | 9-13 each | a tier's two halves are not contiguous on the strand, so each is two segments: switch both on |
| `Tree Tier 3`, `Tree Base` | 38 and 18 | tier 3 includes the base; the base is the bottom edge only |
| `Tree Right`, `Tree Left` | 32 and 31 | top to the right (or left) tier 3 tip; `Right` + `Base` + `Left` = the body |

The boundaries are the `STAR_LEG` and `TREE_CORNERS` numbers at the top of the script (3.05 LEDs per foot of the tree pattern), meant to be fine-tuned: change a number, run it again, watch the test playlist. Colours show where each piece is: the outline test gives each point its own colour, `outline+inner` lights every LED in two colours (a dark LED is a gap).

Test presets 10-14 (star), 20-27 (tree) and 29 (both strands), and playlists **40** `Backyard test: star`, **41** `Backyard test: tree` and **42** `Backyard test: all`, which the script leaves running. Start one with `curl -X POST http://HOST/json/state -H 'Content-Type: application/json' -d '{"ps":41}'`.

**Overlapping segments.** WLED draws every segment in id order and an *off* segment still paints black, which would blank an overlapping segment that is on. So every segment except the two whole strands uses blend mode Add (`"bm":2`; black adds nothing). Keep that when you add segments. Presets for these segments should be saved as API calls (`"o":true`, as the test presets are) so that loading one never rewrites the layout: a preset saved from the live state with `"sb":true` stores the whole layout and deletes any segment it does not list. Only the boot preset is saved that way. Segment limit on a C6 is 64.

## Per-device effects

A device can carry its own LS Formula effects and start in them. These live in `board_setup/devices/<name>/`: an `apply.sh` that uploads the formulas, saves presets and playlists, and sets the boot preset where the device has one.

**Fall and Christmas are both installed on the mantle and on the MBR door, at the same time.** The usermod has only 4 formula slots, so a season does not get slots of its own. Instead the formulas take their colours from the segment's palette (`pal()`), and a preset picks the palette:

| Palette | Colours | Used by |
|---|---|---|
| `christmas` (palette id 200) | red and green, alternating over the 16 palette slots | the Christmas presets |
| `fall` (palette id 199) | rust, orange, amber, gold and brown | the Fall presets |

`apply.sh` uploads both ([`devices/palettes/`](devices/palettes/)) as `/palette0.json` and `/palette1.json`, and refuses to replace a different palette that is already there. The shared logic (uploads, saving presets, playlists, waiting until WLED has really written them) is in [`devices/lib.sh`](devices/lib.sh). The formulas are in [`examples/`](../examples/README.md#the-palette-set-fall-and-christmas).

**`mbr-door`** (`board_setup/devices/mbr-door/apply.sh`, defaults to `mbr-door.local`). The boot preset (2) is the **Fall** playlist; the Christmas playlist (8) is started by choosing it in WLED. Slots: 0 `palette_drops.lsf`, 1 `palette_twinkle.lsf`, 2 `end_flames.lsf`, 3 `palette_fill.lsf`.

| Preset | Look | Formula, palette, P1 |
|---|---|---|
| 3 **Falling Leaves** | about 10% of the LEDs are green dots that slowly wander a few pixels; 2-3 pixel autumn leaves spawn at the middle about once a second and fall to either end in about 4 s, passing behind the green dots | drops, fall, 128 |
| 4 **Autumn Twinkle** | every LED swells and fades on its own 6-12 s cycle in a fall colour | twinkle, fall, 255 |
| 5 **End Flames** | a crackling fire. The main fire starts at both ends and drifts toward the centre (about 3 px/s); thin wisps peel off it and drift all the way to the centre (the top); a few small bright pops near the ends | `end_flames.lsf` (no palette) |
| 9 **Autumn Fill** | rust rises from both ends to the middle, drains, then amber does the same | fill, fall |
| 2 **"Fall patterns"** | playlist of 3, 4, 5 and 9, 2 minutes each. The boot preset | |
| 6 **Christmas Fall** | red and green drops fall from the middle to both ends over a faint red/green glow | drops, christmas, 0 |
| 7 **Christmas Twinkle** | every LED swells and fades on its own 4-9 s cycle in red or green | twinkle, christmas, 0 |
| 10 **Christmas Fill** | red rises from both ends to the middle, drains, then green does the same | fill, christmas |
| 8 **"Christmas"** | playlist of 6, 7 and 10, 2 minutes each | |

```sh
board_setup/devices/mbr-door/apply.sh                       # defaults to mbr-door.local
board_setup/devices/mbr-door/apply.sh 192.168.1.50          # or give an address
SECONDS_PER_PATTERN=5 board_setup/devices/mbr-door/apply.sh    # a fast rotation, to see every pattern
```

To settle on one pattern, make its preset the boot preset (for example 3 for the leaves) in the WLED UI under *Config > LED Preferences > Boot preset*, or with `curl -X POST http://HOST/json/cfg -d '{"def":{"ps":3}}'`.

**`mantle`** (`board_setup/devices/mantle/apply.sh`, defaults to `wled-mantle.local`; or give an address). It works on a strand of any length and refuses to overwrite a preset it does not own (the ids below, with another name). The boot preset is **not** changed. Slots: 0 `palette_twinkle.lsf`, 1 `palette_chase.lsf`, 2 `palette_garland.lsf`, 3 `palette_fill.lsf`.

| Christmas | Fall | Look |
|---|---|---|
| 20 Christmas Twinkle | 30 Autumn Twinkle | every LED swells and fades, in red or green / a fall colour |
| 21 Christmas Chase | 31 Autumn Chase | 6 px bands of two colours marching along the strand |
| 23 Garland Sway | 33 Autumn Garland | slow waves of two colours, like a swag of lights |
| 24 Christmas Fill | 34 Autumn Fill | the first colour rises from both ends to the middle, drains, then the second colour |
| 22 **"Christmas"** | 32 **"Fall"** | a playlist of the four, 2 minutes each (`SECONDS_PER_PATTERN`) |

Preset 1 **"Fall Colors"** is also installed: WLED's built-in Plasma effect (speed 70, intensity 177) in the built-in Orangery palette, on the mantle's boot preset. It is not a formula and not in a playlist. It carries the quick-load label `FC`, which lists it as a "scene" in the touch controller's preset picker (playlists count as scenes on their own; a plain preset needs the label). It replaces provision's default "Boot" preset in that slot, but not a preset someone else made there.

The script leaves the Fall playlist running. Start either with `curl -X POST http://HOST/json/state -H 'Content-Type: application/json' -d '{"ps":22}'` (22 Christmas, 32 Fall on the mantle; 8 Christmas on the door).

**Why not WLED's built-in effects?** Some of these could be built-ins with a palette (Twinklefox for the twinkle, *Palette* or *Colorwaves* for the garland, *Fire 2012* with Mirror for the flames), and that would free slots. They were left as formulas: the formulas keep the exact look the boards already had, and the slot budget is no longer a limit because the seasons share slots. A built-in can be added as one more preset with no change to the scripts.

**Run `apply.sh` after `provision`**: provisioning replaces the whole filesystem, which removes formulas and presets. Preview a formula with no hardware first (`build/lsf_cli FILE -n 50 -t 5000 -f 1`; `-t` is the time in ms). The drops (989 bytes) and flames (1009 bytes) formulas are close to the 1024-byte source limit, and comments count.

**Saving a playlist through the API.** WLED only writes a playlist to `presets.json` when the request is shaped exactly like the one its own web UI sends: `{"playlist":{...,"r":false,...},"on":true,"o":true,"psave":N,"n":"name"}`. Leaving out `on` and `r` made WLED start the playlist but never save it, with no error.

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
