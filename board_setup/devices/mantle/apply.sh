#!/usr/bin/env bash
# Install the Mantle's Fall and Christmas presets, plus "Fall Colors" (preset 1). The boot preset is NOT changed.
#
#   board_setup/devices/mantle/apply.sh [HOST]       # HOST defaults to wled-mantle.local
#
# Re-runnable. Run it after `provision`, which replaces the whole filesystem (formulas, palettes and presets included).
#
# Both seasons are installed at once. The four usermod slots hold formulas that take their colours from the palette
# (see ../lib.sh), so each season is a set of presets that pick a palette; no season needs a slot of its own:
#   slot 0  palette_twinkle.lsf  every LED slowly swells and fades in a palette colour
#   slot 1  palette_chase.lsf    6 px bands of the first and last palette colour marching along
#   slot 2  palette_garland.lsf  slow waves of the first and last palette colour, like a swag
#   slot 3  palette_fill.lsf     the first palette colour rises from both ends and drains, then the last colour
#
#   Christmas (palette "christmas"), playlist 22
#     20 Christmas Twinkle  21 Christmas Chase  23 Garland Sway  24 Christmas Fill   22 "Christmas": a playlist of the four
#   Fall (palette "fall"), playlist 32
#     30 Autumn Twinkle     31 Autumn Chase     33 Autumn Garland  34 Autumn Fill     32 "Fall": a playlist of the four
#   Fall Colors (preset 1): WLED's built-in Plasma effect in the built-in Orangery palette. It is the Mantle's boot
#     preset, and it has the quick-load label FC: the touch controller lists playlists and labelled presets first, as
#     "scenes". The playlists count on their own, a plain preset needs the label.
# Each playlist shows a pattern for SECONDS_PER_PATTERN (default 120) and repeats. The script leaves the Fall playlist
# running. Start either by choosing it in WLED, or: curl -X POST http://wled-mantle.local/json/state -d '{"ps":22}'
#
# The formulas run along a strand of any length. The presets do not store the brightness (ib:false), so the
# brightness slider stays under manual control.
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
. "$here/../lib.sh"
dev_init "${1:-wled-mantle.local}"

# Presets 20-24 and 30-34 are ours, but refuse to overwrite one that somebody else made under a different name.
check_presets "1=Fall Colors|Boot" "20=Christmas Twinkle" "21=Christmas Chase" "22=Christmas" "23=Garland Sway" "24=Christmas Fill" \
  "30=Autumn Twinkle" "31=Autumn Chase" "32=Fall" "33=Autumn Garland" "34=Autumn Fill" || exit 1

dev_palettes
push_slot 0 palette_twinkle.lsf "Palette Twinkle"
push_slot 1 palette_chase.lsf "Palette Chase"
push_slot 2 palette_garland.lsf "Palette Garland"
push_slot 3 palette_fill.lsf "Palette Fill"

# Plasma (effect 97), Orangery (palette 47), orange. Provision's default "Boot" preset is replaced; anything else is not.
save_effect_preset 1 "Fall Colors" 97 47 70 177 128 128 16 "255,160,0" FC

# Twinkle's slider 1 sets the pace: 0 = 4-9 s cycles (Christmas), 255 = 6-12 s (Fall). The others ignore it.
save_preset 20 "Christmas Twinkle" 0 0 christmas
save_preset 21 "Christmas Chase" 1 128 christmas
save_preset 23 "Garland Sway" 2 128 christmas
save_preset 24 "Christmas Fill" 3 128 christmas
save_playlist 22 "Christmas" 20 21 23 24

save_preset 30 "Autumn Twinkle" 0 255 fall
save_preset 31 "Autumn Chase" 1 128 fall
save_preset 33 "Autumn Garland" 2 128 fall
save_preset 34 "Autumn Fill" 3 128 fall
save_playlist 32 "Fall" 30 31 33 34
echo "done on $host: Christmas presets 20 21 23 24 (playlist 22), Fall presets 30 31 33 34 (playlist 32, running, ${SECONDS_PER_PATTERN} s each). The boot preset is unchanged."
