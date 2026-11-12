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
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
root="$(cd "$here/../../.." && pwd)"
host="${1:-mbr-door.local}"
SECONDS_PER_PATTERN="${SECONDS_PER_PATTERN:-120}"
playlist=2
# slot | file | preset name | preset id
patterns=(
  "0|falling_leaves_autumn.lsf|Falling Leaves|3"
  "1|autumn_twinkle.lsf|Autumn Twinkle|4"
  "2|end_flames.lsf|End Flames|5"
)
post() { curl -fsS -m 10 -X POST "http://$host$1" -H 'Content-Type: application/json' -d "$2" >/dev/null; }

curl -fsS -m 10 "http://$host/json/info" >/dev/null || { echo "$host did not answer; is the board on the network?" >&2; exit 1; }

ids=(); durs=(); trans=()
for p in "${patterns[@]}"; do
  IFS='|' read -r slot file name id <<<"$p"
  python3 "$root/tools/lsfx.py" "$host" push "$slot" "$root/examples/$file" --name "$name" --activate
  sleep 1   # let the segment settle on the new effect before its state is saved
  post /json/state "{\"on\":true,\"psave\":$id,\"n\":\"$name\",\"ib\":true,\"sb\":true}"
  sleep 3   # WLED writes the preset asynchronously; a request that arrives before then can drop the write
  ids+=("$id"); durs+=("$((SECONDS_PER_PATTERN * 10))"); trans+=(7)
done

join() { local IFS=,; echo "$*"; }
# This is the exact payload WLED's own web UI sends to save a playlist. "o":true marks it as a playlist; in testing,
# leaving out "on" and "r" (as an earlier version did) made WLED run the playlist but never write it to presets.json.
post /json/state "{\"playlist\":{\"ps\":[$(join "${ids[@]}")],\"dur\":[$(join "${durs[@]}")],\"transition\":[$(join "${trans[@]}")],\"repeat\":0,\"r\":false,\"end\":0},\"on\":true,\"o\":true,\"psave\":$playlist,\"n\":\"Fall patterns\"}"

# WLED writes the preset a moment later, and starting preset $playlist before then makes it run (and then re-save) the
# OLD playlist, so wait until presets.json holds exactly the playlist just sent. Checking only that "a playlist
# exists" is not enough: on a re-run the old one already does.
saved=""
for _ in $(seq 1 20); do
  if curl -fsS -m 10 "http://$host/presets.json" | python3 -c '
import json, sys
want_ps, want_dur = [int(x) for x in sys.argv[2].split(",")], [int(x) for x in sys.argv[3].split(",")]
pl = json.load(sys.stdin).get(sys.argv[1], {}).get("playlist", {})
sys.exit(0 if pl.get("ps") == want_ps and pl.get("dur") == want_dur else 1)' "$playlist" "$(join "${ids[@]}")" "$(join "${durs[@]}")"; then saved=yes; break; fi
  sleep 1
done
[ -n "$saved" ] || { echo "the new playlist was not saved as preset $playlist" >&2; exit 1; }
post /json/cfg "{\"def\":{\"ps\":$playlist}}"
post /json/state "{\"ps\":$playlist}"   # start it now
sleep 2
boot="$(curl -fsS -m 10 "http://$host/json/info" | python3 -c 'import json,sys; print(json.load(sys.stdin)["leds"]["bootps"])')"
[ "$boot" = "$playlist" ] || { echo "boot preset is $boot, expected $playlist" >&2; exit 1; }
echo "done: ${#patterns[@]} patterns saved as presets ${ids[*]}; playlist 'Fall patterns' (preset $playlist, ${SECONDS_PER_PATTERN} s each) is the boot preset on $host"
