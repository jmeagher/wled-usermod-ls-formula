#!/usr/bin/env bash
# Install MBR-Door's fall patterns and make them the board's start-up preset.
#
#   board_setup/devices/mbr-door/apply.sh [HOST]       # HOST defaults to mbr-door.local
#
# Re-runnable. Run it after `provision`, which replaces the whole filesystem (formulas and presets included).
#
# The formulas live in examples/. Each goes into its own usermod slot and is saved as its own preset (3, 4, 5).
# Preset 2, "Fall patterns", is a playlist that steps through them, 2 minutes each by default
# (SECONDS_PER_PATTERN=5 is handy to see all of them quickly). To settle on one pattern, make its preset the
# boot preset instead (see Readme: Per-device effects).
#
# Christmas (red and green) is added next to them, and does NOT change the boot preset: it stays "Fall patterns".
# The usermod has 4 slots and the fall set uses 3, so Christmas is ONE formula (slot 3, christmas_door.lsf) in two
# looks that differ in custom slider 1 (c1): preset 6 "Christmas Fall" (c1 0: red and green drops fall from the
# middle) and preset 7 "Christmas Twinkle" (c1 255: a slow red/green twinkle). Preset 8, "Christmas", is a playlist
# of the two, started by choosing it in WLED (or `curl -X POST http://HOST/json/state -d '{"ps":8}'`).
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
root="$(cd "$here/../../.." && pwd)"
host="${1:-mbr-door.local}"
SECONDS_PER_PATTERN="${SECONDS_PER_PATTERN:-120}"
playlist=2
christmas_playlist=8
# slot | file | preset name | preset id | custom slider 1 (c1)
patterns=(
  "0|falling_leaves_autumn.lsf|Falling Leaves|3|128"
  "1|autumn_twinkle.lsf|Autumn Twinkle|4|128"
  "2|end_flames.lsf|End Flames|5|128"
)
christmas=(
  "3|christmas_door.lsf|Christmas Fall|6|0"
  "3|christmas_door.lsf|Christmas Twinkle|7|255"
)
post() { curl -fsS -m 10 -X POST "http://$host$1" -H 'Content-Type: application/json' -d "$2" >/dev/null; }

curl -fsS -m 10 "http://$host/json/info" >/dev/null || { echo "$host did not answer; is the board on the network?" >&2; exit 1; }

join() { local IFS=,; echo "$*"; }

# Upload one formula, set the slider and save the segment as a preset. Appends to ids/durs/trans for the playlist.
save_pattern() {   # $1 = "slot|file|name|id|c1"
  local slot file name id c1
  IFS='|' read -r slot file name id c1 <<<"$1"
  python3 "$root/tools/lsfx.py" "$host" push "$slot" "$root/examples/$file" --name "$name" --activate
  post /json/state "{\"seg\":[{\"id\":0,\"c1\":$c1}]}"
  # "ib":false ("include brightness") leaves the master brightness out of the preset, so choosing a preset, or the
  # playlist stepping to the next one, never moves the brightness slider: that stays under manual control.
  # "sb":true saves the segment bounds. (Do not swap them: with ib:true every preset resets the brightness.)
  sleep 1   # let the segment settle on the new effect before its state is saved
  post /json/state "{\"on\":true,\"psave\":$id,\"n\":\"$name\",\"ib\":false,\"sb\":true}"
  sleep 3   # WLED writes the preset asynchronously; a request that arrives before then can drop the write
  ids+=("$id"); durs+=("$((SECONDS_PER_PATTERN * 10))"); trans+=(7)
}

# Save a playlist of ids/durs/trans as preset $1 named $2, and wait until presets.json holds exactly that playlist.
# This is the exact payload WLED's own web UI sends to save a playlist. "o":true marks it as a playlist; in testing,
# leaving out "on" and "r" (as an earlier version did) made WLED run the playlist but never write it to presets.json.
# WLED writes the preset a moment later, and starting a preset before then makes it run (and then re-save) the
# OLD playlist. Checking only that "a playlist exists" is not enough either: on a re-run the old one already does.
save_playlist() {   # $1 = preset id, $2 = name
  post /json/state "{\"playlist\":{\"ps\":[$(join "${ids[@]}")],\"dur\":[$(join "${durs[@]}")],\"transition\":[$(join "${trans[@]}")],\"repeat\":0,\"r\":false,\"end\":0},\"on\":true,\"o\":true,\"psave\":$1,\"n\":\"$2\"}"
  local saved=""
  for _ in $(seq 1 20); do
    if curl -fsS -m 10 "http://$host/presets.json" | python3 -c '
import json, sys
want_ps, want_dur = [int(x) for x in sys.argv[2].split(",")], [int(x) for x in sys.argv[3].split(",")]
pl = json.load(sys.stdin).get(sys.argv[1], {}).get("playlist", {})
sys.exit(0 if pl.get("ps") == want_ps and pl.get("dur") == want_dur else 1)' "$1" "$(join "${ids[@]}")" "$(join "${durs[@]}")"; then saved=yes; break; fi
    sleep 1
  done
  [ -n "$saved" ] || { echo "the new playlist was not saved as preset $1" >&2; exit 1; }
}

ids=(); durs=(); trans=()
for p in "${christmas[@]}"; do save_pattern "$p"; done
save_playlist "$christmas_playlist" "Christmas"

# The fall patterns go last, so the segment is left on them and their playlist is the one that is started below.
ids=(); durs=(); trans=()
for p in "${patterns[@]}"; do save_pattern "$p"; done
save_playlist "$playlist" "Fall patterns"
post /json/cfg "{\"def\":{\"ps\":$playlist}}"
post /json/state "{\"ps\":$playlist}"   # start it now
sleep 2
boot="$(curl -fsS -m 10 "http://$host/json/info" | python3 -c 'import json,sys; print(json.load(sys.stdin)["leds"]["bootps"])')"
[ "$boot" = "$playlist" ] || { echo "boot preset is $boot, expected $playlist" >&2; exit 1; }
echo "done: ${#patterns[@]} fall patterns saved as presets ${ids[*]}; playlist 'Fall patterns' (preset $playlist, ${SECONDS_PER_PATTERN} s each) is the boot preset on $host"
echo "      Christmas: presets 6 and 7, playlist 'Christmas' is preset $christmas_playlist (not the boot preset)"
