#!/usr/bin/env bash
# Prove the vendored OpenH264 builds for aarch64 with its NEON assembly, and that the
# NEON build decodes bit-identically to the x86-64 build.
#
# The H.264 decoder is the one native-code dependency in the engine, and build.rs feeds
# it a different set of assembly files per architecture (NASM on x86, GAS .S on ARM), so
# this is the part of an ARM port that can silently go wrong. Everything else in the
# graph is portable Rust.
#
# Runs on an x86-64 machine — no ARM hardware needed:
#   sudo apt-get install -y g++-aarch64-linux-gnu qemu-user-static ffmpeg
#   bash scripts/check_arm64_openh264.sh
#
# On an ARM host, CROSS=native builds with the system compiler and skips the emulator.
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$PWD"
SRC="$ROOT/vendor/openh264-sys2/upstream"
WORK="${TENZOR_ARM_WORK:-${TMPDIR:-/tmp}/tenzor-arm64-check}"
FIXTURES=("${@:-fixtures/baseline720.mp4 fixtures/high-bframes.mp4 fixtures/fixture-h264-aac.mp4}")
CROSS_CXX="${CROSS_CXX:-aarch64-linux-gnu-g++}"
QEMU="${QEMU:-qemu-aarch64-static -L /usr/aarch64-linux-gnu}"
if [ "${CROSS:-}" = "native" ]; then
  CROSS_CXX="${CXX:-g++}"
  QEMU=""
fi
mkdir -p "$WORK"

INCLUDES="-I$SRC/codec/api/wels -I$SRC/codec/common/inc -I$SRC/codec/decoder/core/inc -I$SRC/codec/decoder/plus/inc"
SOURCES=$(ls "$SRC"/codec/common/src/*.cpp "$SRC"/codec/decoder/core/src/*.cpp "$SRC"/codec/decoder/plus/src/*.cpp | grep -v DllEntry)
ARM_ASM=$(ls "$SRC"/codec/common/arm64/*.S "$SRC"/codec/decoder/core/arm64/*.S | grep -v common_macro)

build() { # build <dir> <compiler> [defines...]
  local out="$1" cxx="$2"; shift 2
  local extra=("$@")
  local asm=()
  case " ${extra[*]} " in *HAVE_NEON_AARCH64*) asm=($ARM_ASM);; esac
  rm -rf "$out"; mkdir -p "$out"
  ( cd "$out" && $cxx -O3 -w -fPIC -fno-strict-aliasing -fno-common $INCLUDES \
      -I"$SRC/codec/common/arm64" "${extra[@]}" -c $SOURCES "${asm[@]}" )
  $cxx -O3 -w -fno-strict-aliasing $INCLUDES "${extra[@]}" \
      -o "$out/decode_h264" "$ROOT/scripts/arm64_decode_probe.cpp" "$out"/*.o
  echo "built $(basename "$out"): $(echo "$SOURCES" | wc -l) sources, ${#asm[@]} assembly files"
}

echo "== building the vendored OpenH264 decoder"
build "$WORK/arm64-neon" "$CROSS_CXX" -DHAVE_NEON_AARCH64
build "$WORK/host" "${CXX:-g++}"

status=0
for fixture in ${FIXTURES[*]}; do
  name=$(basename "$fixture" .mp4)
  stream="$WORK/$name.h264"
  ffmpeg -hide_banner -loglevel error -y -i "$ROOT/$fixture" -an -c:v copy \
    -bsf:v h264_mp4toannexb -f h264 "$stream"
  host_out=$("$WORK/host/decode_h264" "$stream")
  arm_out=$($QEMU "$WORK/arm64-neon/decode_h264" "$stream")
  host_digest=$(echo "$host_out" | grep -o 'digest=[0-9a-f]*')
  arm_digest=$(echo "$arm_out" | grep -o 'digest=[0-9a-f]*')
  if [ "$host_digest" = "$arm_digest" ] && [ -n "$arm_digest" ]; then
    echo "PASS $name: aarch64+NEON matches the host decoder ($arm_digest, $(echo "$arm_out" | grep -o 'frames=[0-9]*'))"
  else
    echo "FAIL $name: host $host_digest vs aarch64 $arm_digest"
    status=1
  fi
done
[ $status -eq 0 ] && echo "aarch64 OpenH264 build and NEON decode verified"
exit $status
