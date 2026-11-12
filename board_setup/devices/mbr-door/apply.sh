#!/usr/bin/env bash
# Install the Falling Leaves formula on MBR-Door and make it the preset the board starts in.
#
#   board_setup/devices/mbr-door/apply.sh [HOST]       # HOST defaults to mbr-door.local
#
# Re-runnable. Run it after `provision`, which replaces the whole filesystem (formulas and presets included).
# Steps: upload the formula into usermod slot 0 and run it on segment 0 -> save that state as preset 2
# -> make preset 2 the boot preset (WLED config def.ps).
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
root="$(cd "$here/../../.." && pwd)"
host="${1:-mbr-door.local}"
preset=2
name="Falling Leaves"
post() { curl -fsS -m 10 -X POST "http://$host$1" -H 'Content-Type: application/json' -d "$2" >/dev/null; }

curl -fsS -m 10 "http://$host/json/info" >/dev/null || { echo "$host did not answer; is the board on the network?" >&2; exit 1; }
python3 "$root/tools/lsfx.py" "$host" push 0 "$here/falling_leaves.lsf" --name "$name" --activate
post /json/state "{\"psave\":$preset,\"n\":\"$name\",\"ib\":true,\"sb\":true}"
post /json/cfg "{\"def\":{\"ps\":$preset}}"
sleep 2
boot="$(curl -fsS -m 10 "http://$host/json/info" | python3 -c 'import json,sys; print(json.load(sys.stdin)["leds"]["bootps"])')"
[ "$boot" = "$preset" ] || { echo "boot preset is $boot, expected $preset" >&2; exit 1; }
echo "done: '$name' is in slot 0, saved as preset $preset and set as the boot preset on $host"
