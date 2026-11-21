#!/usr/bin/env bash
# Install the Mantle's Christmas presets (reds and greens). The boot preset is NOT changed.
#
#   board_setup/devices/mantle/apply.sh [HOST]       # HOST defaults to wled-mantle.local
#
# Re-runnable. Run it after `provision`, which replaces the whole filesystem (formulas and presets included).
#
# Presets (ids are high on purpose, so they never collide with the boot preset the board was provisioned with):
#   20  "Christmas Twinkle"  examples/christmas_twinkle.lsf (usermod slot 0): every LED swells and fades, red or green
#   21  "Christmas Chase"    examples/christmas_chase.lsf   (usermod slot 1): 6 px red and green bands marching along
#   22  "Christmas"          a playlist of 20 and 21, SECONDS_PER_PATTERN each (default 120), repeating
# Start the show by choosing preset 22 in WLED, or: curl -X POST http://wled-mantle.local/json/state -d '{"ps":22}'
#
# Both formulas run along the strand whatever its length. The presets do not store the brightness (ib:false), so the
# brightness slider stays under manual control.
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
root="$(cd "$here/../../.." && pwd)"
host="${1:-wled-mantle.local}"
SECONDS_PER_PATTERN="${SECONDS_PER_PATTERN:-120}"
playlist=22
# slot | file | preset name | preset id
patterns=(
  "0|christmas_twinkle.lsf|Christmas Twinkle|20"
  "1|christmas_chase.lsf|Christmas Chase|21"
)
post() { curl -fsS -m 10 -X POST "http://$host$1" -H 'Content-Type: application/json' -d "$2" >/dev/null; }
join() { local IFS=,; echo "$*"; }

curl -fsS -m 10 "http://$host/json/info" >/dev/null || { echo "$host did not answer; is the board on the network?" >&2; exit 1; }

# Presets 20-22 are ours, but refuse to overwrite one that somebody else made under a different name.
curl -fsS -m 10 "http://$host/presets.json" | python3 -c '
import json, sys
have = json.load(sys.stdin)
mine = {"20": "Christmas Twinkle", "21": "Christmas Chase", "22": "Christmas"}
for pid, name in mine.items():
    if pid in have and have[pid].get("n") != name:
        sys.exit("preset %s on the board is \"%s\", not \"%s\"; not overwriting it" % (pid, have[pid].get("n"), name))' \
  || exit 1

ids=(); durs=(); trans=()
for p in "${patterns[@]}"; do
  IFS='|' read -r slot file name id <<<"$p"
  python3 "$root/tools/lsfx.py" "$host" push "$slot" "$root/examples/$file" --name "$name" --activate
  sleep 1   # let the segment settle on the new effect before its state is saved
  post /json/state "{\"on\":true,\"psave\":$id,\"n\":\"$name\",\"ib\":false,\"sb\":true}"
  sleep 3   # WLED writes the preset asynchronously; a request that arrives before then can drop the write
  ids+=("$id"); durs+=("$((SECONDS_PER_PATTERN * 10))"); trans+=(7)
done

# The payload WLED's own web UI sends to save a playlist (see mbr-door/apply.sh for why every field matters).
post /json/state "{\"playlist\":{\"ps\":[$(join "${ids[@]}")],\"dur\":[$(join "${durs[@]}")],\"transition\":[$(join "${trans[@]}")],\"repeat\":0,\"r\":false,\"end\":0},\"on\":true,\"o\":true,\"psave\":$playlist,\"n\":\"Christmas\"}"
saved=""
for _ in $(seq 1 20); do
  if curl -fsS -m 10 "http://$host/presets.json" | python3 -c '
import json, sys
want_ps, want_dur = [int(x) for x in sys.argv[2].split(",")], [int(x) for x in sys.argv[3].split(",")]
pl = json.load(sys.stdin).get(sys.argv[1], {}).get("playlist", {})
sys.exit(0 if pl.get("ps") == want_ps and pl.get("dur") == want_dur else 1)' "$playlist" "$(join "${ids[@]}")" "$(join "${durs[@]}")"; then saved=yes; break; fi
  sleep 1
done
[ -n "$saved" ] || { echo "the playlist was not saved as preset $playlist" >&2; exit 1; }
echo "done on $host: presets ${ids[*]} saved; playlist 'Christmas' is preset $playlist (${SECONDS_PER_PATTERN} s each). The boot preset is unchanged."
