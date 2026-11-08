#!/usr/bin/env bash
# Fetch a pinned WLED checkout into ./.wled and point it at this working tree, ready to build.
#
#   scripts/dev-setup.sh                     # then: cd .wled && npm ci && npm run build && pio run -e lsf_esp32c6
#   WLED_REF=<sha-or-tag> scripts/dev-setup.sh
#
# Re-runnable. Touches only ./.wled (gitignored).
set -euo pipefail
here="$(cd "$(dirname "$0")/.." && pwd)"
# The commit this usermod is tested against (WLED main, 2026-11-06; includes ESP32-C6 support).
ref="${WLED_REF:-fffa05a765d41d7126b66ab5a3d1ce1f41e7d11e}"
dir="$here/.wled"
if [ ! -d "$dir/.git" ]; then
  mkdir -p "$dir"
  git -C "$dir" init -q
  git -C "$dir" remote add origin https://github.com/wled/WLED.git
fi
if [ "$(git -C "$dir" rev-parse HEAD 2>/dev/null || true)" != "$ref" ]; then
  git -C "$dir" fetch -q --depth 1 origin "$ref"
  git -C "$dir" checkout -q FETCH_HEAD
fi
echo "WLED at $(git -C "$dir" log -1 --format='%h %ad %s' --date=short)"
# PlatformIO resolves symlink:// to this working tree, so edits here are picked up by the next build.
sed "s#__USERMOD_PATH__#$here#g" "$here/platformio_override.sample.ini" > "$dir/platformio_override.ini"
echo "wrote $dir/platformio_override.ini (environments: lsf_esp32dev, lsf_esp32c6, lsf_esp32s3)"
echo "next:  cd .wled && npm ci && npm run build && pio run -e lsf_esp32c6"
