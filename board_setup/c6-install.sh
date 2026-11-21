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
# Environment: PORT, WLED_REF (commit, tag or branch), WLED_DIR, ENV_NAME.
# `provision --dry-run` and `provision --out DIR` only print or write the configuration (nothing is flashed).
#
# Re-runnable. The WLED checkout lives OUTSIDE this repository on purpose: PlatformIO treats the usermod
# (symlink://) as a library and scans everything below it, so a WLED tree nested inside it hides WLED's
# own lib/ libraries and the build fails with "wled_ADCmanager.h: No such file or directory".
set -euo pipefail

here="$(cd "$(dirname "$0")/.." && pwd -P)"
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
# Compare real paths: "..", symlinks and a relative WLED_DIR must not get past the check (the directory may not exist yet).
dir_real="$(python3 -c 'import os, sys; print(os.path.realpath(sys.argv[1]))' "$dir")"
case "$dir_real/" in "$here"/*) echo "WLED_DIR ($dir) must not be inside $here (see header comment)" >&2; exit 2 ;; esac

# --out writes the passwords to a directory and --dry-run writes nothing: neither ever flashes, so they are not
# allowed with `all` (which would build and flash first) and `provision` runs them once instead of validating first.
dry=""
for arg in "$@"; do
  case "$arg" in --out|--out=*|--dry-run) dry=1 ;; esac
done
if [ -n "$dry" ] && [ "$action" = all ]; then
  echo "--out and --dry-run only write or print the configuration and never flash; use '$0 provision ...' for them" >&2
  exit 2
fi

for tool in git node npm pio; do
  command -v "$tool" >/dev/null || { echo "missing required tool: $tool" >&2; exit 1; }
done

# The commit $ref names. A full SHA is used as it is; a branch or tag is looked up on the remote, so that an
# up-to-date checkout of it is not fetched again on every run (if the lookup fails, the caller fetches).
resolve_ref() {   # $1 = ref; prints a 40-hex SHA, or nothing
  if [[ "$1" =~ ^[0-9a-f]{40}$ ]]; then
    echo "$1"
  else
    git -C "$dir" ls-remote origin "$1" "$1^{}" 2>/dev/null | awk '$2 ~ /\^\{\}$/ { peeled = $1 } !first { first = $1 } END { print peeled ? peeled : first }'
  fi

}

# Check out $ref in $dir, fetching only when HEAD is not already that commit.
checkout_ref() {
  local want
  want="$(resolve_ref "$ref")"
  if [ -z "$want" ] || [ "$(git -C "$dir" rev-parse HEAD 2>/dev/null || true)" != "$want" ]; then
    git -C "$dir" fetch -q --depth 1 origin "$ref"
    git -C "$dir" checkout -q FETCH_HEAD
  fi
}

# $here as a sed replacement string: backslash, ampersand and the delimiter are escaped.
sed_escape() { printf '%s' "$1" | sed -e 's/[\\&#]/\\&/g'; }

build() {
  if [ ! -d "$dir/.git" ]; then
    mkdir -p "$dir"
    git -C "$dir" init -q
    git -C "$dir" remote add origin https://github.com/wled/WLED.git
  fi
  checkout_ref
  echo "WLED at $(git -C "$dir" log -1 --format='%h %ad %s' --date=short)"
  sed "s#__USERMOD_PATH__#$(sed_escape "$here")#g" "$here/platformio_override.sample.ini" > "$dir/platformio_override.ini"
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
  provision) [ -n "$dry" ] || validate "$@"; provision "$@" ;;
  all)       validate "$@"; build; flash; provision "$@" ;;
esac
