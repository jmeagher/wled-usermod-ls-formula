#!/usr/bin/env bash
# Install MBR-Door's Fall and Christmas presets and make the Fall playlist the board's start-up preset.
#
#   board_setup/devices/mbr-door/apply.sh [HOST]       # HOST defaults to mbr-door.local
#
# Re-runnable. Run it after `provision`, which replaces the whole filesystem (formulas, palettes and presets included).
#
# Both seasons are installed at once. The four usermod slots hold formulas that take their colours from the palette
# (see ../lib.sh), so each season is a set of presets that pick a palette; no season needs a slot of its own:
#   slot 0  palette_drops.lsf    leaves / drops fall from the middle (the top of the door) to both ends
#   slot 1  palette_twinkle.lsf  every LED slowly swells and fades in a palette colour
#   slot 2  end_flames.lsf       crackling fire from both ends (orange fire, no palette)
#   slot 3  palette_fill.lsf     the first palette colour rises from both ends and drains, then the last colour
#
#   Fall (palette "fall"), preset 2 is the playlist and the boot preset
#     3 Falling Leaves   drops, P1 128: deep orange to amber leaves, with a green dot every 10 px drifting in front
#     4 Autumn Twinkle   twinkle, P1 255: slow 6-12 s cycles
#     5 End Flames       flames
#     9 Autumn Fill      fill: rust rises, drains, then amber
#     2 "Fall patterns"  a playlist of 3, 4, 5 and 9, 2 minutes each (SECONDS_PER_PATTERN=5 is handy to see all of them quickly)
#   Christmas (palette "christmas"), preset 8 is the playlist; start it by choosing it in WLED
#     6 Christmas Fall     drops, P1 0: red and green drops over a faint red/green glow
#     7 Christmas Twinkle  twinkle, P1 0: 4-9 s cycles, red or green
#    10 Christmas Fill     fill: red rises, drains, then green
#     8 "Christmas"        a playlist of 6, 7 and 10
# To settle on one pattern, make its preset the boot preset instead (see Readme: Per-device effects).
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
. "$here/../lib.sh"
dev_init "${1:-mbr-door.local}"
fall_playlist=2
christmas_playlist=8

check_presets "2=Fall patterns" "3=Falling Leaves" "4=Autumn Twinkle" "5=End Flames" "6=Christmas Fall" "7=Christmas Twinkle" \
  "8=Christmas" "9=Autumn Fill" "10=Christmas Fill" || exit 1

dev_palettes
push_slot 0 palette_drops.lsf "Palette Drops"
push_slot 1 palette_twinkle.lsf "Palette Twinkle"
push_slot 2 end_flames.lsf "End Flames"
push_slot 3 palette_fill.lsf "Palette Fill"

# Christmas first, so the segment is left on the Fall presets and their playlist is the one that is started below.
save_preset 6 "Christmas Fall" 0 0 christmas
save_preset 7 "Christmas Twinkle" 1 0 christmas
save_preset 10 "Christmas Fill" 3 128 christmas
save_playlist "$christmas_playlist" "Christmas" 6 7 10

save_preset 3 "Falling Leaves" 0 128 fall
save_preset 4 "Autumn Twinkle" 1 255 fall
save_preset 5 "End Flames" 2 128 fall
save_preset 9 "Autumn Fill" 3 128 fall
save_playlist "$fall_playlist" "Fall patterns" 3 4 5 9
post /json/cfg "{\"def\":{\"ps\":$fall_playlist}}"
post /json/state "{\"ps\":$fall_playlist}"   # start it now
sleep "$(( ${APPLY_SETTLE:-1} * 2 ))"
boot="$(get /json/info | python3 -c 'import json,sys; print(json.load(sys.stdin)["leds"]["bootps"])')"
[ "$boot" = "$fall_playlist" ] || { echo "boot preset is $boot, expected $fall_playlist" >&2; exit 1; }
echo "done on $host: Fall presets 3 4 5 9, playlist 'Fall patterns' is preset $fall_playlist (${SECONDS_PER_PATTERN} s each) and the boot preset"
echo "      Christmas presets 6 7 10, playlist 'Christmas' is preset $christmas_playlist (not the boot preset)"
