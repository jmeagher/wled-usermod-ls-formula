#!/usr/bin/env bash
# Build WLED (pinned commit) with the LS Formula usermod for a 4 MB ESP32-C6 and flash it.
# See board_setup/Readme.md for the why and for troubleshooting.
#
#   board_setup/c6-install.sh build                      # build the firmware only
#   board_setup/c6-install.sh flash                      # flash the firmware only (port auto-detected)
#   board_setup/c6-install.sh provision OPTIONS...       # WiFi, names and LED strips (board_setup/provision.py)
#   board_setup/c6-install.sh all OPTIONS...             # build + flash + provision
#
#   board_setup/c6-install.sh all --name Desk --mdns desk --strip type=ws2812,order=grb,pin=7,len=50
#
# OPTIONS are passed to provision.py (see its --help); WiFi credentials come from .wled.secrets.
# Environment: PORT, WLED_REF, WLED_DIR, ENV_NAME.
#
# Re-runnable. The WLED checkout lives OUTSIDE this repository on purpose: PlatformIO treats the usermod
# (symlink://) as a library and scans everything below it, so a WLED tree nested inside it hides WLED's
# own lib/ libraries and the build fails with "wled_ADCmanager.h: No such file or directory".
set -euo pipefail

here="$(cd "$(dirname "$0")/.." && pwd)"
action="${1:-}"
shift || true
ref="${WLED_REF:-fffa05a765d41d7126b66ab5a3d1ce1f41e7d11e}"   # same commit CI and dev-setup.sh pin
env_name="${ENV_NAME:-lsf_esp32c6_4MB}"
dir="${WLED_DIR:-${XDG_CACHE_HOME:-$HOME/.cache}/wled-ls-formula/WLED}"

usage() { echo "usage: $0 build|flash|provision|all [provision options...]   (see header of this script)" >&2; exit 2; }
case "$action" in
  build|flash) [ "$#" -eq 0 ] || { echo "$action takes no options" >&2; usage; } ;;
  provision|all) [ "$#" -gt 0 ] || { echo "$action needs provision options (at least --name, --mdns, --strip)" >&2; usage; } ;;
  *) usage ;;
esac
case "$dir/" in "$here"/*) echo "WLED_DIR ($dir) must not be inside $here (see header comment)" >&2; exit 2 ;; esac

for tool in git node npm pio; do
  command -v "$tool" >/dev/null || { echo "missing required tool: $tool" >&2; exit 1; }
done

build() {
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
  sed "s#__USERMOD_PATH__#$here#g" "$here/platformio_override.sample.ini" > "$dir/platformio_override.ini"
  (cd "$dir" && npm ci --silent && npm run build --silent && pio run -e "$env_name")
}

flash() {
  local port="${PORT:-}"
  if [ -z "$port" ]; then
    local ports=(/dev/cu.usbmodem* /dev/ttyACM*)
    local found=()
    for p in "${ports[@]}"; do [ -e "$p" ] && found+=("$p"); done
    if [ "${#found[@]}" -ne 1 ]; then
      echo "expected exactly one serial port, found ${#found[@]}: ${found[*]:-none}. Set PORT=..." >&2
      exit 1
    fi
    port="${found[0]}"
  fi
  echo "flashing $env_name to $port"
  (cd "$dir" && pio run -e "$env_name" -t upload --upload-port "$port")
}

provision_args() { python3 "$here/board_setup/provision.py" "$@"; }

validate() {
  # --dry-run checks the options and the secrets file and touches nothing, so mistakes fail in a second.
  provision_args "$@" --dry-run >/dev/null
}

provision() {
  provision_args --env "$env_name" --wled-dir "$dir" ${PORT:+--port "$PORT"} "$@"
}

case "$action" in
  build)     build ;;
  flash)     flash ;;
  provision) validate "$@"; provision "$@" ;;
  all)       validate "$@"; build; flash; provision "$@" ;;
esac
