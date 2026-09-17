#!/usr/bin/env bash
# Run the existing regression scripts against target/release/tenzor on a scratch copy of
# the repository, because several scripts regenerate committed fixtures with the local FFmpeg.
set -uo pipefail
SRC="$(cd "$(dirname "$0")/.." && pwd)"
COPY="${REGRESSION_COPY:-$HOME/.tenzor-build/repo-copy}"
GEN="${TENZOR_GEN_FIXTURES:-/tmp/tenzor-skip-fixtures}"
export TENZOR_OUTPUT_ROOT="${TENZOR_OUTPUT_ROOT:-$HOME/.tenzor-build/out}"
rm -rf "$COPY" && mkdir -p "$COPY" "$TENZOR_OUTPUT_ROOT"
tar -C "$SRC" --exclude=./target --exclude=./.git --exclude=./vendor -cf - . | tar -C "$COPY" -xf -
mkdir -p "$COPY/target/release" "$COPY/evidence/v0.3.0"
cp "$SRC/target/release/tenzor" "$COPY/target/release/tenzor"
for f in long-30.mp4 gen-720-g45.mp4 gen-baseline-g12.mp4 gen-open-gop.mp4; do cp "$GEN/$f" "$COPY/fixtures/$f"; done
cd "$COPY"
fail=0
gate() {
  local name=$1; shift
  local log="$COPY/evidence/gate-$name.log"
  local start=$(date +%s)
  "$@" > "$log" 2>&1; local rc=$?
  printf "%-28s %s  (%ss)  %s\n" "$name" "$([ $rc -eq 0 ] && echo PASS || echo "FAIL rc=$rc")" "$(( $(date +%s) - start ))" "$(tail -1 "$log" | cut -c1-110)"
  [ $rc -eq 0 ] || fail=1
}
gate matrix-default          python3 scripts/test_matrix.py
gate matrix-w4               python3 scripts/test_matrix.py --video-workers 4
gate epoch-selection-w1      python3 scripts/test_epoch_selection.py --old-binary reference/bin/tenzor-v0.1.6-linux-x86_64 --video-workers 1
gate epoch-selection-w4      python3 scripts/test_epoch_selection.py --old-binary reference/bin/tenzor-v0.1.6-linux-x86_64 --video-workers 4
gate concurrency             python3 scripts/test_concurrency.py
gate chunk-boundaries        python3 scripts/test_chunk_boundaries.py --old-binary reference/bin/tenzor-v0.1.6-linux-x86_64
gate audio-identity-repo     env RESULT=evidence/v0.3.0/identity-repo-fixtures.json python3 scripts/test_audio_identity.py gen-720-g45.mp4,gen-baseline-g12.mp4,gen-open-gop.mp4,aac-6ch.mp4,audio-only.mp4,baseline720.mp4,color709.mp4,corrupt-nal.mp4,corrupt.mp4,dual-audio-tail.mp4,dual-idr.mp4,dual-off-grid.mp4,dual-open-gop.mp4,dual-sparse.mp4,dual-vfr.mp4,empty.mp4,fixture-h264-aac.mp4,fixture.wav,fullrange.mp4,high-bframes.mp4,long-30.mp4,selection-sparse.mp4,selection-vfr.mp4,short.mp4,silent-video.mp4,sync-pulse.mp4,tiny.wav,unsupported-mpeg4.mp4,unsupported.mp3,wav-16000-1ch.wav,wav-44100-2ch.wav,wav-48000-2ch.wav,wav-48000-6ch.wav
gate audio-write-failure     python3 scripts/test_audio_write_failure.py
gate chunked-corruption-fuzz env TENZOR_OLD_BINARY=$COPY/reference/bin/tenzor-v0.1.9-linux-x86_64 python3 scripts/fuzz_chunked_corruption.py
echo "ALL_GATES $([ $fail -eq 0 ] && echo PASS || echo FAIL)  logs: $COPY/evidence/gate-*.log"
exit $fail
