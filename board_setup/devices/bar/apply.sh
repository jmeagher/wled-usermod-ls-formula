#!/usr/bin/env bash
# Install the Bar's presets and make "Birdie's Bar" the board's start-up preset.
#
#   board_setup/devices/bar/apply.sh [HOST]       # HOST defaults to wled-bar.local
#
# Re-runnable. Run it after `provision`, which replaces the whole filesystem (formulas and presets included).
# The "Happy Hour" text is longer than WLED's default 64-character segment-name limit, so the Bar needs the
# `lsf_esp32c6_4MB_longtext` firmware (platformio_override.sample.ini); this script refuses to continue otherwise.
#
# Presets:
#   1  "Birdie's Bar"      the boot preset (what the board shows after power-up, and after Happy Hour ends)
#   2  "Happy Hour sign"   the same display with the Happy Hour message; the playlist below uses it
#   3  "Happy Hour"        a playlist: shows preset 2 for 4 hours, then switches to preset 1
#
# Happy Hour is more colourful: red and green join the sparkles. On the panel that is custom slider 1 (c1 above 200
# switches red/green on in examples/bar_sparkles.lsf); on the strand it is the custom palette in
# happy_hour_palette.json (yellow, white, pink, red, green), uploaded to the board as /palette0.json (palette id 200).
# All the scrolling text scrolls at double its earlier speed (sx 214 for the main text, 235 for the long message).
#
# Each display preset has three segments, drawn in this order:
#   0  the 32x8 panel: Scrolling Text in light blue. The segment NAME is the text that scrolls.
#   1  "Sparkles"      the same panel: the LS Formula in examples/bar_sparkles.lsf (usermod slot 0), blend mode
#                      "Add" so its black background leaves the text alone. About 8 yellow/white/pink sparkles at once.
#   2  light strand    the 50 LEDs on GPIO 19: built-in Colortwinkles from a yellow/white/pink palette, much denser.
#
# The presets do not store the brightness (ib:false), so the brightness slider stays under manual control.
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
root="$(cd "$here/../../.." && pwd)"
host="${1:-wled-bar.local}"
boot_preset=1
happy_sign=2
happy_playlist=3
HAPPY_HOUR_SECONDS="${HAPPY_HOUR_SECONDS:-14400}"      # 4 hours; 14400 s = 144000 tenths of a second
palette_file="$here/happy_hour_palette.json"
palette_id=200                                          # custom palette /palette0.json is palette id 200
text_main="Birdie's Bar"
text_happy="Birdie's Bar, Happy Hour specials available now. Ask your server about our specials."
# slot | file | preset name | preset id
patterns=(
  "0|bar_sparkles.lsf|Birdie's Bar|1"
)
post() { curl -fsS -m 10 -X POST "http://$host$1" -H 'Content-Type: application/json' -d "$2" >/dev/null; }
get()  { curl -fsS -m 10 "http://$host$1"; }

get /json/info >/dev/null || { echo "$host did not answer; is the board on the network?" >&2; exit 1; }

# The long sign text needs a segment name of at least ${#text_happy} characters. A firmware with WLED's default limit
# would silently cut it off, so test that by setting the name and reading it back.
need=${#text_happy}
post /json/state "$(python3 -c 'import json,sys; print(json.dumps({"seg":[{"id":0,"n":sys.argv[1]}]}))' "$text_happy")"
got="$(get /json/state | python3 -c 'import json,sys; print(len(json.load(sys.stdin)["seg"][0].get("n","")))')"
[ "$got" -ge "$need" ] || {
  echo "this firmware keeps only $got characters of a segment name, the Happy Hour text needs $need." >&2
  echo "Flash or OTA the lsf_esp32c6_4MB_longtext environment first (see platformio_override.sample.ini)." >&2
  exit 1
}

python3 "$root/tools/lsfx.py" "$host" push 0 "$root/examples/bar_sparkles.lsf" --name "Bar Sparkles"
fx="$(get /json/state | python3 -c 'import json,sys; print(json.load(sys.stdin)["lsf"]["slots"][0]["fx"])')"

# The custom palette for the Happy Hour strand. Refuse to overwrite a different /palette0.json (a palette someone made).
existing="$(curl -sS -m 10 -o /dev/null -w '%{http_code}' "http://$host/palette0.json" || true)"
if [ "$existing" = "200" ] && ! get /palette0.json | python3 -c '
import json, sys
sys.exit(0 if json.load(sys.stdin) == json.load(open(sys.argv[1])) else 1)' "$palette_file"; then
  echo "/palette0.json on $host is a different palette; not overwriting it (remove it first, or change palette_id)" >&2
  exit 1
fi
curl -fsS -m 20 -F "data=@$palette_file;filename=/palette0.json" "http://$host/upload" >/dev/null
sleep 2
get /palette0.json | python3 -c '
import json, sys
sys.exit(0 if json.load(sys.stdin) == json.load(open(sys.argv[1])) else 1)' "$palette_file" || { echo "the palette upload did not arrive intact" >&2; exit 1; }

# The live state for one display preset. Effect ids: 122 Scrolling Text, 74 Colortwinkles.
# Scrolling Text: c2=128 picks the 6x8 font (8 rows tall), ix=128 no vertical offset, c3=16 no rotation, sx = scroll speed.
# Colortwinkles: sx = fade speed, ix = how often new sparkles start; palette 5 is "Colors Only" (the segment's three
# colours), so each sparkle is yellow, white or pink.
state_for() {   # $1 = scrolling text, $2 = scroll speed, $3 = 1 for the colourful (Happy Hour) sparkles
  python3 - "$1" "$2" "$fx" "$3" "$palette_id" <<'PY'
import json, sys
text, speed, fx, colourful, pal = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), sys.argv[4] == "1", int(sys.argv[5])
panel = {"start": 0, "stop": 32, "startY": 0, "stopY": 8, "on": True, "frz": False, "bri": 255}
segs = [
  {**panel, "id": 0, "n": text, "fx": 122, "sx": speed, "ix": 128, "c1": 0, "c2": 128, "c3": 16, "pal": 0, "bm": 0,
   "col": [[135, 206, 250], [0, 0, 0], [0, 0, 0]]},
  # c1 255 switches red and green on in the sparkle formula; 128 (WLED's default) leaves yellow/white/pink only
  {**panel, "id": 1, "n": "Sparkles", "fx": fx, "sx": 128, "ix": 128, "c1": 255 if colourful else 128, "pal": 0, "bm": 2,
   "col": [[255, 255, 255], [0, 0, 0], [0, 0, 0]]},
  # the strand: palette 5 = the three segment colours; Happy Hour uses the 5-colour custom palette and spawns more
  {"id": 2, "start": 256, "stop": 306, "startY": 0, "stopY": 1, "n": "Strip 2 (GPIO 19)", "on": True, "frz": False,
   "bri": 255, "fx": 74, "sx": 100, "ix": 200 if colourful else 150, "pal": pal if colourful else 5, "bm": 0,
   "col": [[255, 200, 0], [255, 255, 255], [255, 105, 180]]},
]
print(json.dumps({"on": True, "seg": segs}))
PY
}

# Save the display as preset $1 named $2. "ib":false = do not store the brightness; "sb":true = store the segment bounds.
# Then wait until presets.json holds it with the full text, because WLED writes the file a moment later.
# Optional $6 is a quick-load label ("ql"): it marks the preset as a "scene" for the touch controller's preset picker,
# which lists scenes first. Playlists count as scenes without it; plain presets need it.
save_display() {   # $1 id, $2 preset name, $3 text, $4 speed, $5 1 = colourful, $6 quick-load label (optional)
  post /json/state "$(state_for "$3" "$4" "$5")"
  sleep 2
  post /json/state "$(python3 -c 'import json,sys; d = {"on":True,"psave":int(sys.argv[1]),"n":sys.argv[2],"ib":False,"sb":True}
if sys.argv[3]: d["ql"] = sys.argv[3]
print(json.dumps(d))' "$1" "$2" "${6:-}")"
  local saved=""
  for _ in $(seq 1 20); do
    if get /presets.json | python3 -c '
import json, sys
p = json.load(sys.stdin).get(sys.argv[1], {})
fx = [s.get("fx") for s in p.get("seg", []) if "fx" in s]
txt = [s.get("n") for s in p.get("seg", []) if s.get("fx") == 122]
c1 = [s.get("c1") for s in p.get("seg", []) if s.get("fx") == int(sys.argv[3])]
pal = [s.get("pal") for s in p.get("seg", []) if s.get("fx") == 74]
colourful = sys.argv[5] == "1"
ok = (p.get("n") == sys.argv[2] and "bri" not in p and fx == [122, int(sys.argv[3]), 74] and txt == [sys.argv[4]]
      and c1 == [255 if colourful else 128] and pal == [int(sys.argv[6]) if colourful else 5]
      and p.get("ql", "") == sys.argv[7])
sys.exit(0 if ok else 1)' "$1" "$2" "$fx" "$3" "$5" "$palette_id" "${6:-}"; then saved=yes; break; fi
    sleep 1
  done
  [ -n "$saved" ] || { echo "preset $1 \"$2\" was not saved as expected (segments 122, $fx, 74; full text; no brightness)" >&2; exit 1; }
  sleep 3   # WLED writes presets asynchronously; a request that arrives too soon can drop the write
}

# Scroll speed (sx) 214 and 235 are twice as fast as the earlier 110 and 150: Scrolling Text moves one column every
# 250 - sx*200/255 ms, which is 165 ms -> 83 ms for the main text and 133 ms -> 66 ms for the long Happy Hour message.
# Birdie's Bar is a scene but not a playlist, so it needs the quick-load label; "Happy Hour sign" is only a helper.
save_display "$boot_preset" "Birdie's Bar" "$text_main" 214 0 BB
save_display "$happy_sign" "Happy Hour sign" "$text_happy" 235 1

# The playlist: preset $happy_sign once, for $HAPPY_HOUR_SECONDS, then the end preset ("end") $boot_preset.
# This is the exact payload WLED's web UI sends to save a playlist ("o":true marks it as one; see mbr-door/apply.sh).
dur=$((HAPPY_HOUR_SECONDS * 10))
post /json/state "{\"playlist\":{\"ps\":[$happy_sign],\"dur\":[$dur],\"transition\":[7],\"repeat\":1,\"r\":false,\"end\":$boot_preset},\"on\":true,\"o\":true,\"psave\":$happy_playlist,\"n\":\"Happy Hour\"}"
saved=""
for _ in $(seq 1 20); do
  if get /presets.json | python3 -c '
import json, sys
pl = json.load(sys.stdin).get(sys.argv[1], {}).get("playlist", {})
ok = pl.get("ps") == [int(sys.argv[2])] and pl.get("dur") == [int(sys.argv[3])] and pl.get("repeat") == 1 and pl.get("end") == int(sys.argv[4])
sys.exit(0 if ok else 1)' "$happy_playlist" "$happy_sign" "$dur" "$boot_preset"; then saved=yes; break; fi
  sleep 1
done
[ -n "$saved" ] || { echo "the Happy Hour playlist was not saved as preset $happy_playlist" >&2; exit 1; }

# Back to the normal display, and make it the boot preset.
post /json/cfg "{\"def\":{\"ps\":$boot_preset}}"
post /json/state "{\"ps\":$boot_preset}"
sleep 2
boot="$(get /json/info | python3 -c 'import json,sys; print(json.load(sys.stdin)["leds"]["bootps"])')"
[ "$boot" = "$boot_preset" ] || { echo "boot preset is $boot, expected $boot_preset" >&2; exit 1; }
echo "done on $host: preset $boot_preset \"Birdie's Bar\" is the boot preset; preset $happy_playlist \"Happy Hour\" shows preset $happy_sign for $((HAPPY_HOUR_SECONDS / 60)) minutes, then returns to preset $boot_preset"
