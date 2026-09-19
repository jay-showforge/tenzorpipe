#!/usr/bin/env bash
# Prove the vendored SIMD 8x8 inverse transform is bit-identical to the scalar one.
#
# Upstream OpenH264 ships no x86 kernel for the 8x8 transform, and the decoder spends
# 4.3% of its instructions in the C version on High-profile streams. TenzorPipe adds
# SSE2 and AVX2 clones with runtime CPUID dispatch. Because the engine's whole promise
# is byte-identical tensors, "faster" is only acceptable if it is also exact, and the
# artifact digests alone would not exercise the SSE2 clone on an AVX2 machine.
#
# So this compares both clones against the real upstream C function — the same
# translation unit the decoder builds, not a copy — over random, sparse, DC-only and
# saturation-corner residual blocks at varying strides.
#
#   bash scripts/test_idct8x8_simd.sh [iterations]      # default 1,000,000 blocks
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$PWD"
SRC="$ROOT/vendor/openh264-sys2/upstream"
CORE="$SRC/codec/decoder/core"
WORK="${TENZOR_IDCT_WORK:-${TMPDIR:-/tmp}/tenzor-idct8x8}"
ITERATIONS="${1:-1000000}"

case "$(uname -m)" in
  x86_64 | amd64 | i?86) ;;
  *) echo "skip: the SIMD 8x8 kernels are x86-only (this is $(uname -m))"; exit 0 ;;
esac

mkdir -p "$WORK"
CXX="${CXX:-g++}"
# X86_ASM is what gates the kernels in the real build; keep it identical here.
"$CXX" -O2 -w -fno-strict-aliasing -DX86_ASM \
  -I"$SRC/codec/api/wels" -I"$SRC/codec/common/inc" -I"$CORE/inc" -I"$CORE/src" \
  -o "$WORK/idct8x8_diff_test" \
  "$ROOT/scripts/idct8x8_diff_test.cpp" "$CORE/src/decode_mb_aux.cpp" "$CORE/src/decoder_data_tables.cpp"

"$WORK/idct8x8_diff_test" "$ITERATIONS"
