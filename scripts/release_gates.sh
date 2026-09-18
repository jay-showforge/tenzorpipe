#!/usr/bin/env bash
# v0.3.0 release gates. Linux x86-64 with Rust 1.98.1, NASM, FFmpeg (test media only) and a Python
# environment containing numpy, pyarrow, pillow, psutil and torch (CPU is enough).
#
#   bash scripts/release_gates.sh            # everything
#   QUICK=1 bash scripts/release_gates.sh    # skip the ~15 min byte-identity matrix
#
# Evidence: evidence/v0.3.0/. Large outputs go to $TENZOR_OUTPUT_ROOT (disk, not tmpfs).
set -uo pipefail
cd "$(dirname "$0")/.."
ROOT="$PWD"
EVID="$ROOT/evidence/v0.3.0"
mkdir -p "$EVID"
export TENZOR_OUTPUT_ROOT="${TENZOR_OUTPUT_ROOT:-/tmp/tenzor-artifacts}"
export TENZOR_GEN_FIXTURES="${TENZOR_GEN_FIXTURES:-/tmp/tenzor-skip-fixtures}"
TARGET="${CARGO_TARGET_DIR:-$ROOT/target}"
fail=0

gate() {
  local name=$1; shift
  local log="$EVID/gate-$name.log" start rc
  start=$(date +%s)
  "$@" > "$log" 2>&1; rc=$?
  printf "%-26s %-9s %5ss  %s\n" "$name" "$([ $rc -eq 0 ] && echo PASS || echo "FAIL($rc)")" \
    "$(( $(date +%s) - start ))" "$(grep -v '^\s*$' "$log" | tail -1 | cut -c1-100)"
  [ $rc -eq 0 ] || fail=1
}

gate build        cargo build --release --locked --workspace
# Test scripts expect the engine at target/release/tenzor.
if [ "$TARGET" != "$ROOT/target" ]; then
  mkdir -p target/release && cp "$TARGET/release/tenzor" target/release/tenzor
fi
gate unit-tests   cargo test --locked --workspace --all-targets
gate aac-tests    cargo test --locked --manifest-path vendor/rusty_aac/Cargo.toml --lib
gate aac-tests-no-default cargo test --locked --manifest-path vendor/rusty_aac/Cargo.toml --lib --no-default-features
gate fmt          cargo fmt --all --check
gate clippy       cargo clippy --locked --workspace --all-targets -- -D warnings
gate licenses     cargo deny check licenses
gate skip-fixtures bash scripts/gen_skip_fixtures.sh "$TENZOR_GEN_FIXTURES"
gate tail-fixtures python3 scripts/gen_short_tail_fixtures.py
gate audio-tail   python3 scripts/test_audio_tail_tolerance.py
gate hevc-fixtures bash scripts/gen_hevc_fixtures.sh
gate hevc-fidelity python3 scripts/test_hevc_fidelity.py
if [ -z "${QUICK:-}" ]; then
  gate skip-identity env RESULT="$EVID/skip-identity.json" python3 scripts/test_skip_identity.py
fi
gate regression   bash scripts/run_regression_copy.sh
gate python-wheel bash scripts/build_wheel.sh
echo "RELEASE_GATES $([ $fail -eq 0 ] && echo PASS || echo FAIL)  (logs: $EVID/gate-*.log)"
exit $fail
