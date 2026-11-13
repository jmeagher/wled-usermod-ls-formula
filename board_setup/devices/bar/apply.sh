#!/usr/bin/env bash
# Install the Bar's preset "Birdie's Bar" and make it the board's start-up preset.
#
#   board_setup/devices/bar/apply.sh [HOST]       # HOST defaults to wled-bar.local
#
# Re-runnable. Run it after `provision`, which replaces the whole filesystem (formulas and presets included).
#
# Three segments, drawn in this order:
#   0  "Birdie's Bar!"  the 32x8 panel: Scrolling Text in light blue. The segment NAME is the text that scrolls.
#   1  "Sparkles"       the same 32x8 panel: the LS Formula in examples/bar_sparkles.lsf (usermod slot 0), blend mode
#                       "Add" so its black background leaves the text alone. About 8 yellow/white/pink sparkles at once.
#   2  light strand     the 50 LEDs on GPIO 19: built-in Colortwinkles from a yellow/white/pink palette, much denser.
#
# The preset does not store the brightness (ib:false), so the brightness slider stays under manual control.
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
root="$(cd "$here/../../.." && pwd)"
host="${1:-wled-bar.local}"
preset=1
text="Birdie's Bar!"
# slot | file | preset name | preset id
patterns=(
  "0|bar_sparkles.lsf|Birdie's Bar|1"
)
post() { curl -fsS -m 10 -X POST "http://$host$1" -H 'Content-Type: application/json' -d "$2" >/dev/null; }

curl -fsS -m 10 "http://$host/json/info" >/dev/null || { echo "$host did not answer; is the board on the network?" >&2; exit 1; }

python3 "$root/tools/lsfx.py" "$host" push 0 "$root/examples/bar_sparkles.lsf" --name "Bar Sparkles"
fx="$(curl -fsS -m 10 "http://$host/json/state" | python3 -c 'import json,sys; print(json.load(sys.stdin)["lsf"]["slots"][0]["fx"])')"

# Effect ids: 122 Scrolling Text, 74 Colortwinkles. Scrolling Text: c2=128 picks the 6x8 font (8 rows tall), ix=128 no
# vertical offset, c3=16 no rotation. Colortwinkles: sx = fade speed, ix = how often new sparkles start; palette 5 is
# "Colors Only" (the segment's three colours), so each sparkle is yellow, white or pink.
state="$(python3 - "$text" "$fx" <<'PY'
import json, sys
text, fx = sys.argv[1], int(sys.argv[2])
panel = {"start": 0, "stop": 32, "startY": 0, "stopY": 8, "on": True, "frz": False, "bri": 255}
segs = [
  {**panel, "id": 0, "n": text, "fx": 122, "sx": 110, "ix": 128, "c1": 0, "c2": 128, "c3": 16, "pal": 0, "bm": 0,
   "col": [[135, 206, 250], [0, 0, 0], [0, 0, 0]]},
  {**panel, "id": 1, "n": "Sparkles", "fx": fx, "sx": 128, "ix": 128, "pal": 0, "bm": 2,
   "col": [[255, 255, 255], [0, 0, 0], [0, 0, 0]]},
  {"id": 2, "start": 256, "stop": 306, "startY": 0, "stopY": 1, "n": "Strip 2 (GPIO 19)", "on": True, "frz": False,
   "bri": 255, "fx": 74, "sx": 100, "ix": 150, "pal": 5, "bm": 0,
   "col": [[255, 200, 0], [255, 255, 255], [255, 105, 180]]},
]
print(json.dumps({"on": True, "seg": segs}))
PY
)"
post /json/state "$state"
sleep 2
# "ib":false = do not store the brightness; "sb":true = store the segment bounds.
post /json/state "{\"on\":true,\"psave\":$preset,\"n\":\"Birdie's Bar\",\"ib\":false,\"sb\":true}"

saved=""
for _ in $(seq 1 20); do
  if curl -fsS -m 10 "http://$host/presets.json" | python3 -c '
import json, sys
p = json.load(sys.stdin).get(sys.argv[1], {})
fx = [s.get("fx") for s in p.get("seg", []) if "fx" in s]
ok = p.get("n") == "Birdie'"'"'s Bar" and "bri" not in p and fx == [122, int(sys.argv[2]), 74]
sys.exit(0 if ok else 1)' "$preset" "$fx"; then saved=yes; break; fi
  sleep 1
done
[ -n "$saved" ] || { echo "preset $preset was not saved as expected (segments 122, $fx, 74; no brightness)" >&2; exit 1; }
post /json/cfg "{\"def\":{\"ps\":$preset}}"
sleep 2
boot="$(curl -fsS -m 10 "http://$host/json/info" | python3 -c 'import json,sys; print(json.load(sys.stdin)["leds"]["bootps"])')"
[ "$boot" = "$preset" ] || { echo "boot preset is $boot, expected $preset" >&2; exit 1; }
echo "done: preset $preset \"Birdie's Bar\" (text + sparkles on the panel, twinkles on the strand) is the boot preset on $host"
