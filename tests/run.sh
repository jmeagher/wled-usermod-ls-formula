#!/usr/bin/env bash
# Build and run the host tests with warnings as errors and sanitizers. Also builds the CLI.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
out="${LSF_BUILD_DIR:-$here/../build}"
mkdir -p "$out"
cxx="${CXX:-c++}"
flags=(-std=c++11 -O1 -g -Wall -Wextra -Werror -fsanitize=address,undefined -fno-sanitize-recover=undefined)
"$cxx" "${flags[@]}" -DLSF_EXAMPLES_DIR="\"$here/../examples\"" \
  "$here/test_lsf.cpp" "$here/../lsf.cpp" -o "$out/test_lsf"
"$out/test_lsf"
"$cxx" "${flags[@]}" "$here/test_button.cpp" -o "$out/test_button"
"$out/test_button"
"$cxx" "${flags[@]}" "$here/../tools/lsf_cli.cpp" "$here/../lsf.cpp" -o "$out/lsf_cli"
echo "cli: $out/lsf_cli"
