#!/usr/bin/env bash
set -euo pipefail
# Historical: reproduces the v0.2.0 EPYC evidence (write_reports.py asserts version 0.2.0).
# v0.3.0 verification is scripts/release_gates.sh.
cd "$(dirname "$0")/.."
export CARGO_BUILD_JOBS="${CARGO_BUILD_JOBS:-2}"
export TENZOR_OUTPUT_ROOT="${TENZOR_OUTPUT_ROOT:-/tmp/tenzor-artifacts}"
mkdir -p "$TENZOR_OUTPUT_ROOT" evidence/v0.2.0
cargo build --release --locked 2>&1 | tee evidence/build-v020.log
cargo test --locked --all-targets 2>&1 | tee evidence/unit-v020.log
cargo test --locked --manifest-path vendor/rusty_aac/Cargo.toml --lib 2>&1 | tee evidence/aac-unit-v020.log
cargo test --locked --manifest-path vendor/rusty_aac/Cargo.toml --lib --no-default-features 2>&1 | tee evidence/aac-unit-no-default-v020.log
cargo fmt --check 2>&1 | tee evidence/fmt-v020.log
cargo clippy --locked --all-targets -- -D warnings 2>&1 | tee evidence/clippy-v020.log
cargo deny check licenses 2>&1 | tee evidence/licenses-v020.log
python3 scripts/generate_matrix.py --long --extended-memory
bash scripts/gen_v018_fixtures.sh
bash scripts/gen_v020_audio_fixtures.sh
python3 scripts/test_matrix.py --video-workers 4
python3 scripts/test_epoch_selection.py --old-binary reference/bin/tenzor-v0.1.6-linux-x86_64 --video-workers 4
python3 scripts/test_concurrency.py
python3 scripts/test_chunk_boundaries.py --old-binary reference/bin/tenzor-v0.1.6-linux-x86_64
REQUIRE_SUCCESS=1 RESULT=evidence/v0.2.0/audio-identity-generated-media.json python3 - <<'PY'
import pathlib,subprocess,sys
files=sorted(pathlib.Path('fixtures/audio-v020').resolve().glob('*'))
subprocess.run([sys.executable,'scripts/test_audio_identity.py',','.join(map(str,files))],check=True)
PY
RESULT=evidence/v0.2.0/identity-repo-fixtures.json python3 scripts/test_audio_identity.py gen-720-g45.mp4,gen-baseline-g12.mp4,gen-open-gop.mp4,aac-6ch.mp4,audio-only.mp4,baseline720.mp4,color709.mp4,corrupt-nal.mp4,corrupt.mp4,dual-audio-tail.mp4,dual-idr.mp4,dual-off-grid.mp4,dual-open-gop.mp4,dual-sparse.mp4,dual-vfr.mp4,empty.mp4,fixture-h264-aac.mp4,fixture.wav,fullrange.mp4,high-bframes.mp4,long-30.mp4,selection-sparse.mp4,selection-vfr.mp4,short.mp4,silent-video.mp4,sync-pulse.mp4,tiny.wav,unsupported-mpeg4.mp4,unsupported.mp3,wav-16000-1ch.wav,wav-44100-2ch.wav,wav-48000-2ch.wav,wav-48000-6ch.wav
python3 scripts/test_audio_write_failure.py
python3 scripts/fuzz_audio_corruption.py fixtures/long-330-audio_only.mp4 fixtures/audio-v020/m48-tools.mp4 fixtures/audio-v020/pink-51.mp4 fixtures/audio-v020/av-music.mp4
# No other heavy work during these serial timings. Each timed output is checked afterward.
python3 scripts/benchmark_epyc_v020.py
workers="$(python3 scripts/select_video_workers.py)"
python3 scripts/memory_release.py --workers "$workers"
python3 scripts/verify_benchmarks.py --records epyc-retained-v020.json --output epyc-benchmark-verification-v020.json
python3 scripts/audit_disk_writes.py
python3 scripts/stress_publication.py --output-root "$TENZOR_OUTPUT_ROOT"
python3 scripts/write_reports.py
