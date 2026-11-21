# shellcheck shell=bash
# Shared helpers for board_setup/devices/*/apply.sh. Source it, do not run it:
#
#   . "$(dirname "$0")/../lib.sh"; dev_init "${1:-wled-mantle.local}"
#
# It sets host, root and SECONDS_PER_PATTERN (default 120), and defines the functions below. Callers use `set -euo pipefail`.
# APPLY_SETTLE (default 1, seconds) is how long a segment settles before it is saved as a preset; tests set it to 0.
#
# Fall and Christmas share formulas. A formula takes its colours from the segment's palette (pal()), so the same slot
# runs in both seasons and the preset decides the look: it stores the palette, custom slider 1 and the three colours.
# The two palettes are custom WLED palettes that dev_palettes uploads as /palette0.json and /palette1.json
# (palette ids 200 and 199: WLED numbers custom palettes downwards from 200).
#   christmas  16 slots alternating red and green. Slot 0 is red, slot 15 is green.
#   fall       16 slots of rust, orange, amber, gold and brown. Slot 0 is rust, slot 15 is amber.
# The formulas that use two colours (fill, chase, garland) take slot 0 and slot 15; the ones that pick random colours
# (twinkle, drops) pick from all 16 slots.

here_lib="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
root="$(cd "$here_lib/../.." && pwd)"
SECONDS_PER_PATTERN="${SECONDS_PER_PATTERN:-120}"

post() { curl -fsS -m 10 -X POST "http://$host$1" -H 'Content-Type: application/json' -d "$2" >/dev/null; }
get()  { curl -fsS -m 10 "http://$host$1"; }
join() { local IFS=,; echo "$*"; }

dev_init() {   # $1 = host
  host="$1"
  get /json/info >/dev/null || { echo "$host did not answer; is the board on the network?" >&2; exit 1; }
}

palette_id() {   # $1 = christmas | fall
  case "$1" in christmas) echo 200 ;; fall) echo 199 ;; *) echo "unknown palette '$1'" >&2; return 1 ;; esac
}

# Refuse to overwrite a preset that somebody else made under another name. Arguments: ID=NAME ...
# NAME may list alternatives separated by "|" (ID=Fall Colors|Boot), for a slot that is allowed to hold a freshly
# provisioned device's default preset as well as ours.
check_presets() {
  get /presets.json | python3 -c '
import json, sys
have = json.load(sys.stdin)
for arg in sys.argv[1:]:
    pid, name = arg.split("=", 1)
    if pid in have and have[pid].get("n") not in name.split("|"):
        sys.exit("preset %s on the board is \"%s\", not \"%s\"; not overwriting it" % (pid, have[pid].get("n"), name.split("|")[0]))' "$@"
}

# Upload the two custom palettes. Refuses to replace a different palette that is already there (one someone made).
dev_palettes() {
  local n=0 name file code
  for name in christmas fall; do
    file="$root/board_setup/devices/palettes/$name.json"
    code="$(curl -sS -m 10 -o /dev/null -w '%{http_code}' "http://$host/palette$n.json" || true)"
    if [ "$code" = "200" ] && ! get "/palette$n.json" | python3 -c '
import json, sys
sys.exit(0 if json.load(sys.stdin) == json.load(open(sys.argv[1])) else 1)' "$file"; then
      echo "/palette$n.json on $host is a different palette; not overwriting it (remove it first)" >&2
      exit 1
    fi
    curl -fsS -m 20 -F "data=@$file;filename=/palette$n.json" "http://$host/upload" >/dev/null
    n=$((n + 1))
  done
  sleep "$(( ${APPLY_SETTLE:-1} * 2 ))"   # WLED reloads its palettes when the upload ends
  n=0
  for name in christmas fall; do
    get "/palette$n.json" | python3 -c '
import json, sys
sys.exit(0 if json.load(sys.stdin) == json.load(open(sys.argv[1])) else 1)' "$root/board_setup/devices/palettes/$name.json" \
      || { echo "the $name palette did not arrive intact" >&2; exit 1; }
    n=$((n + 1))
  done
}

# Put a formula in a slot, unless the slot already holds exactly that source under that name (every push is a flash write).
push_slot() {   # $1 = slot, $2 = file in examples/, $3 = effect name
  if get /json/state | python3 -c '
import json, sys
s = json.load(sys.stdin)["lsf"]["slots"][int(sys.argv[1])]
sys.exit(0 if s.get("ok") and s["name"] == sys.argv[2] and s["src"] == open(sys.argv[3]).read() else 1)' "$1" "$3" "$root/examples/$2"; then
    echo "slot $1 already holds $2"
  else
    python3 "$root/tools/lsfx.py" "$host" push "$1" "$root/examples/$2" --name "$3"
  fi
}

slot_fx() {   # $1 = slot: the WLED effect id of that slot
  get /json/state | python3 -c 'import json,sys; print(json.load(sys.stdin)["lsf"]["slots"][int(sys.argv[1])]["fx"])' "$1"
}

# Wait until presets.json holds preset $1 with this name, effect, custom slider 1 and palette. WLED writes presets a
# moment after the request, and a request that arrives before then can drop the write.
# $6 is the quick-load label the preset must carry; empty (the default) means it must have none.
wait_preset() {   # $1 = id, $2 = name, $3 = fx, $4 = c1, $5 = palette id, $6 = ql (optional)
  local _
  for _ in $(seq 1 20); do
    if get /presets.json | python3 -c '
import json, sys
p = json.load(sys.stdin).get(sys.argv[1], {})
seg = (p.get("seg") or [{}])[0]
ql = sys.argv[6] if len(sys.argv) > 6 else ""
sys.exit(0 if p.get("n") == sys.argv[2] and [seg.get("fx"), seg.get("c1"), seg.get("pal")] == [int(a) for a in sys.argv[3:6]]
         and p.get("ql", "") == ql else 1)' "$@"; then
      return 0
    fi
    sleep 1
  done
  echo "preset $1 ($2) was not saved as expected" >&2
  return 1
}

# Save segment 0 as a preset: the formula in $3, custom slider 1 = $4, palette $5 (christmas | fall).
# "ib":false ("include brightness") leaves the master brightness out of the preset, so choosing a preset, or a playlist
# stepping to the next one, never moves the brightness slider: that stays under manual control. "sb":true saves the
# segment bounds. (Do not swap them: with ib:true every preset resets the brightness.)
# The colours: Color 2 is the green of the door's leaf dots; the formulas that read the palette ignore the others.
save_preset() {   # $1 = id, $2 = name, $3 = slot, $4 = c1, $5 = palette name
  local id="$1" name="$2" fx pid
  fx="$(slot_fx "$3")"
  pid="$(palette_id "$5")"
  post /json/state "{\"on\":true,\"seg\":[{\"id\":0,\"fx\":$fx,\"c1\":$4,\"pal\":$pid,\"col\":[[255,160,0],[40,255,0],[0,0,0]]}]}"
  sleep "${APPLY_SETTLE:-1}"   # let the segment settle before its state is saved
  post /json/state "{\"on\":true,\"psave\":$id,\"n\":\"$name\",\"ib\":false,\"sb\":true}"
  wait_preset "$id" "$name" "$fx" "$4" "$pid"
}

# Save a built-in WLED effect (not a formula slot) as a preset. Every effect setting is set explicitly, so the preset
# does not depend on whatever the segment last held. "ql" is the quick-load label that makes it a scene (see below).
#   $1 id  $2 name  $3 effect id  $4 palette id (WLED's built-in numbering)  $5 speed  $6 intensity
#   $7 c1  $8 c2  $9 c3  ${10} first colour as R,G,B  ${11} quick-load label
# Same "ib":false / "sb":true as save_preset.
save_effect_preset() {
  local id="$1" name="$2" fx="$3" pal="$4" sx="$5" ix="$6" c1="$7" c2="$8" c3="$9" col="${10}" ql="${11}"
  post /json/state "{\"on\":true,\"seg\":[{\"id\":0,\"fx\":$fx,\"sx\":$sx,\"ix\":$ix,\"c1\":$c1,\"c2\":$c2,\"c3\":$c3,\"pal\":$pal,\"col\":[[$col],[0,0,0],[0,0,0]]}]}"
  sleep "${APPLY_SETTLE:-1}"   # let the segment settle before its state is saved
  post /json/state "{\"on\":true,\"psave\":$id,\"n\":\"$name\",\"ql\":\"$ql\",\"ib\":false,\"sb\":true}"
  wait_preset "$id" "$name" "$fx" "$c1" "$pal" "$ql"
}

# Save a playlist of the presets in $3... as preset $1 named $2, SECONDS_PER_PATTERN each, and wait until presets.json
# holds exactly that playlist. This is the payload WLED's own web UI sends. "o":true marks it as a playlist; leaving
# out "on" and "r" made WLED run the playlist but never write it to presets.json. WLED writes the preset a moment later,
# and starting a preset before then makes it run (and then re-save) the OLD playlist, so checking that "a playlist
# exists" is not enough either: on a re-run the old one already does.
save_playlist() {   # $1 = preset id, $2 = name, $3... = preset ids
  local pl="$1" name="$2" durs=() trans=() _
  shift 2
  for _ in "$@"; do durs+=("$((SECONDS_PER_PATTERN * 10))"); trans+=(7); done
  post /json/state "{\"playlist\":{\"ps\":[$(join "$@")],\"dur\":[$(join "${durs[@]}")],\"transition\":[$(join "${trans[@]}")],\"repeat\":0,\"r\":false,\"end\":0},\"on\":true,\"o\":true,\"psave\":$pl,\"n\":\"$name\"}"
  for _ in $(seq 1 20); do
    if get /presets.json | python3 -c '
import json, sys
want_ps, want_dur = [int(x) for x in sys.argv[2].split(",")], [int(x) for x in sys.argv[3].split(",")]
pl = json.load(sys.stdin).get(sys.argv[1], {}).get("playlist", {})
sys.exit(0 if pl.get("ps") == want_ps and pl.get("dur") == want_dur else 1)' "$pl" "$(join "$@")" "$(join "${durs[@]}")"; then
      return 0
    fi
    sleep 1
  done
  echo "the playlist was not saved as preset $pl" >&2
  return 1
}
