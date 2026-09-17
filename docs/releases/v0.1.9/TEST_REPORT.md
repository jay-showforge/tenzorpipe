# TEST_REPORT — TenzorPipe 0.1.9

**PASS for the tested scope.** Binary SHA-256: `675c64e599131174ac57d13e9561fdce3ecebbe31056219a932d47bd14cfe2c3`.
The results below were executed on this release; the supplied v0.1.8 report is
preserved under docs/releases as historical material.

## Independent source verification

| Fixture | Resolution | Epochs | Batches | Worst image MAE (0–255) | Active Log-Mel MAE |
|---|---:|---:|---:|---:|---:|
| baseline720.mp4 | 224 | 7 | 4 | 0.000000 | 0.001843 |
| high-bframes.mp4 | 160 | 7 | 4 | 0.152480 | 0.001843 |
| high-bframes.mp4 | 224 | 7 | 4 | 0.156934 | 0.001843 |
| high-bframes.mp4 | 336 | 7 | 4 | 0.167806 | 0.001843 |
| short.mp4 | 224 | 1 | 1 | 0.000000 | 0.004248 |
| silent-video.mp4 | 224 | 3 | 2 | 0.000000 | 0.000000 |
| wav-44100-2ch.wav | 224 | 3 | 2 | 0.000000 | 0.000003 |
| wav-48000-2ch.wav | 224 | 3 | 2 | 0.000000 | 0.000004 |
| wav-16000-1ch.wav | 224 | 3 | 2 | 0.000000 | 0.000011 |
| wav-48000-6ch.wav | 224 | 3 | 2 | 0.000000 | 0.000001 |
| audio-only.mp4 | 224 | 7 | 4 | 0.000000 | 0.001843 |
| color709.mp4 | 224 | 3 | 2 | 0.039039 | 0.000941 |
| fullrange.mp4 | 224 | 3 | 2 | 0.049027 | 0.000941 |
| sync-pulse.mp4 | 224 | 5 | 3 | 0.000000 | 0.000016 |
| aac-6ch.mp4 | 224 | 3 | 2 | 0.000000 | 0.001409 |
| tiny.wav | 224 | 1 | 1 | 0.000000 | 0.000001 |

A zero in an absent-modality table cell means not applicable. FFmpeg independently
decodes the source. NumPy computes the documented color/resize and sinc/STFT/64-band
triangular Mel/log-power reference. The image gate is MAE <3 intensity levels;
energetic audio bins use MAE <0.08 and p99 <0.5. Raw per-frame/bin statistics are
retained in matrix.json. Selected tensor reconstructions were visually inspected:
source patterns, color blocks and motion are recognizable and match the oracle.
See evidence/high-bframes-224-visual.png and evidence/dual-idr-160-visual.png.

AAC and WAV cover 44.1/48 kHz, mono/stereo/six-channel downmix, audio-only, video-only,
non-divisible durations and very short tails. The flash/tone aligns at 1,000 ms;
330 ms epochs contain 33 hops. Rust tests cover sinc anti-alias rejection (>60 dB)
and continuity across streamed resampling input. All tensor arrays are finite and
non-silent media produces nonzero features. Silence is represented by Log-Mel -10.

All 60 pictures in the independent 30-second baseline comparison pass: worst
image MAE **0.295717/255**, minimum PSNR
**37.440 dB**; energetic Log-Mel MAE
**0.000081**. These are fidelity checks,
not assertions that two different decoder/resampler implementations are identical.

## Exact identity and malformed-input coverage

- 26 Rust unit tests pass, including two regression tests first demonstrated failing on the supplied v0.1.8. The live-payload test uses drop accounting; it checks actual simultaneous chunk ownership, not just a calculated log value.
- 41 comparisons against v0.1.6 check every logical column at 160/224/336 and 50/330/500 ms, including sparse/VFR/B-frame media. The current tests request four workers, exercising chunking when eligible and fallback otherwise.
- 38 sequential/concurrent, queue-budget, audio-tail and failure checks pass. Corrupted later AAC/video and real RLIMIT_FSIZE write failures terminate and remove partial outputs.
- 30 chunk-boundary checks cover 18 comparisons across six generated IDR/off-grid/open-GOP/VFR/sparse/audio-tail fixtures, one all-frame oracle and 11 failure/argument checks. Open-GOP fallback is asserted. The 83-frame oracle checks every frame around and away from the cut; sample K−1/K/K+1 MAEs are 0.188793/0/0, matching v0.1.6 exactly.
- The expanded matrix has **600 successful version-normalized Arrow byte comparisons + 160 expected-error agreements = 760**. It spans N1/2/4/8, 160/224/336, multiple windows, small chunks and a 1 MiB budget. Version normalization changes only the two expected schema-version strings. Errors are not counted as tensor comparisons. 177 executions actually use chunking, and their reported buffer bounds fit the requested budgets.
- 24 deterministic corruptions at different positions fail without timeout, panic or leftover output in both old and new engines.
- The original 12 invalid-input tests cover empty/corrupt media, unsupported codecs/container and invalid resolution/window arguments. Ordinary audio-only and video-only inputs are supported, not classified as malformed.
- PyArrow reads every RecordBatch and validates timestamps/shapes/metadata. PyTorch pointer-alias checks, full iteration, indexing and copy mode pass at dynamic resolution. This is CPU zero-copy-compatible loading, not end-to-end zero-copy preprocessing.
- All 35 benchmark artifacts are finite, have the expected rows/batches and are reread independently; long hashes are identical across worker counts, versions and batch sizes. The 22-minute comparison contains 2641 epochs and matches all logical columns.
- 20 output files pass immediate and delayed rereads in /tmp. Three dynamic-libc writable-open audits observe no decoded-media intermediates.

## Reproduce and evidence

```sh
# Linux x86-64; install C/C++ compiler, NASM, FFmpeg and strace first.
rustup toolchain install 1.98.1 --profile minimal --component rustfmt,clippy
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r python/requirements-test.txt
python -m pip install torch==2.14.0+cpu --index-url https://download.pytorch.org/whl/cpu
cargo install cargo-deny --version 0.20.2 --locked
bash scripts/reproduce.sh
```

`reproduce.sh` lists all build, test, fixture, benchmark and independent verification
commands in execution order. It sets CARGO_BUILD_JOBS=2 and uses /tmp outputs by
default; allow about 20 GiB free. The two reference binaries are bundled for the
exact old/new checks. Generated short fixtures are included; long media is regenerated.
Individual benchmark commands:

```sh
export TENZOR_OUTPUT_ROOT=/tmp/tenzor-artifacts
python3 scripts/benchmark_release.py --old-binary reference/bin/tenzor-v0.1.6-linux-x86_64 --v018-binary reference/bin/tenzor-v0.1.8-linux-x86_64
python3 scripts/memory_release.py --old-binary reference/bin/tenzor-v0.1.6-linux-x86_64
python3 scripts/verify_benchmarks.py --records release-benchmarks.json --output release-benchmark-verification.json
python3 scripts/audit_disk_writes.py
```

Evidence: unit-v019.log, verification-summary.json, matrix.json,
epoch-selection-regression.json, concurrency-tests.json, chunk-boundary-tests.json,
v0.1.9-identity-matrix.json, chunk-budget-checks.json, v0.1.9-corruption-fuzz.json,
release-benchmark-verification.json, memory-release.json and publication-stress.json.
The source scripts contain assertions and fail on violated gates. Suites overlap;
counts should not be added into a claim of distinct independent tests. This is
fixture-based verification, not exhaustive codec conformance, formal leak proof or
a security audit. Known limits are in BUILD_STATUS.md.
