# TEST_REPORT — TenzorPipe 0.2.0, EPYC rerun

**PASS for the tested scope.** Binary SHA-256: `c914ddbd5663c9837c501020e58f98b1cdb150708ae0b37fcbc5df8374858b77`.

## Source-media fidelity and independent loading

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

Absent-modality zero cells are not applicable. FFmpeg decodes the source independently;
NumPy computes the documented resampling/STFT/Mel and color/resize reference.
Image MAE remains <3 intensity levels; energetic Log-Mel MAE <0.08 and p99 <0.5.
Visual tensors were reconstructed and inspected; source colors, pattern and motion
remain recognizable. Baseline/high-profile H.264 includes B frames; AVCC SPS/PPS,
decode order and presentation-frame selection retain the previously verified path.

The matrix covers 44.1/48 kHz, mono/stereo/six channels, AAC/WAV, audio-only,
video-only, tiny tails, non-divisible durations and 160/224/336 output. The flash/tone
aligns at 1, 000 ms. 330 ms epochs contain 33 audio hops. Rust DSP tests retain sinc
anti-alias and streaming-continuity gates. PyArrow visits all batches, and PyTorch
checks alias pointers, full iteration, dynamic shapes, indexing and writable copies.
The loader section was executed here; it is not inferred from earlier output identity.

Independent30-second baseline: worst image MAE
0.295717/255, minimum PSNR
37.440 dB, energetic Log-Mel MAE
0.000081. Every output is finite with the
expected row count, timestamps and shape metadata. Selected long-file frames also
pass the source oracle.

## Audio change verification

- 30 engine unit tests pass, including source error/panic, early consumer exit, consumer panic, cancellation and exact threaded/inline output.
- 94 AAC tests pass with default features and again with engine-matching no-default-features; 4 explicitly ignored tests are not counted as passes. Huffman table/scan, bit reads, dequantization and gain tables are exercised. Existing inverse-transform correctness tests remain enabled.
- 14 generated files ×4 settings ×3 execution variants =168 successful version-normalized byte comparisons against v0.1.9. Content includes music-like tones/transients, deterministic-seed white noise, pink noise and silence; 8/22.05/44.1/48/96 kHz; mono/stereo/5.1; 24–384 kbps; float32 and 24-bit WAV; H.264+AAC. PNS/TNS/intensity/M-S encoder tools were requested; flags alone are not proof that every tool was active in every packet.
- The repository matrix uses 33 unique files ×12 settings/variants =396 checks: 336 successful artifact comparisons and 60 matching expected errors. The supplied 432-row matrix repeated three generated video fixtures twice. Deduplication is not a dropped unique test case.
- Total audio/repository matrix: 504 successful artifacts and 60 expected-error agreements. Only the two expected schema-version strings are normalized; numerical values, metadata apart from version, and serialized tensor bytes must match.
- 92 deterministic corruption/truncation cases agree with v0.1.9 in outcome and top-level error text, without hangs, Rust panics or leftover failed outputs. This is not complete error-chain equality for that fuzz script.
- 12 paired RLIMIT_FSIZE cases (3 media ×4 modes) hit EFBIG, compare the full error diagnostic, and clean partial outputs. This exercises write-failure cancellation; it does not claim the physical disk was filled or ENOSPC was separately injected.

## Retained video/concurrency and long-file gates

41 epoch-selection comparisons, 38 pipeline/queue/failure checks and 30 chunk-boundary
checks pass. Clean-IDR, off-grid, sparse, VFR, open-GOP fallback, audio tails and real
write failures remain covered. B-frame chunk-boundary images match the source oracle.

All 80 benchmark artifacts are checked outside their timed process. 25 retained outputs
are checked again, with exact full-video logical-column identity across worker counts,
batch sizes and v0.1.9. Audio-only variants also match exactly. Four memory artifacts
are checked; the 22-minute old/new file has 2641 epochs
with exact column hashes. 20 publications pass immediate/delayed rereads. Dynamic-libc
open instrumentation finds no intermediate decoded-media file on the three audited
30-second pipelines; direct syscalls can bypass that instrumentation.

The production Rust engine and audio math are unchanged from the supplied v0.2.0.
The release was rebuilt with pinned Rust 1.98.1 and NASM. Repairs were to verification
and packaging: update the root-only license exception from =0.1.9 to =0.2.0; make
identity/fuzz failures return nonzero; require generated valid media to actually
decode; stream artifact hashing; test real write-limit failure; restore the full
PyTorch gate; replace stale reports and packaging assumptions with current evidence.

The initial generated noise fixture lacked a moov box. Both engines rejected it,
which was agreement but not decode success. It was regenerated through /tmp,
FFprobe-checked and all 168 generated-media comparisons were rerun successfully.
The first EFBIG limit was above the small WAV artifact size; it was lowered so all
chosen files actually encounter the error. These failed attempts are retained under
evidence/v020-review/. No production validation or numerical tolerance was relaxed.


## Reproduce

```sh
# Linux x86-64: extract the complete ZIP (or both split ZIPs), then cd tenzorpipe-v0.2.0.
# Install a C/C++ compiler, NASM, FFmpeg, Python 3.12 and Rustup.
rustup toolchain install 1.98.1 --profile minimal --component rustfmt,clippy
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r python/requirements-test.txt
python -m pip install torch==2.14.0+cpu --index-url https://download.pytorch.org/whl/cpu
cargo install cargo-deny --version 0.20.2 --locked
bash scripts/reproduce.sh
```

The script contains every exact build, fixture, correctness, benchmark and audit
command in execution order. It uses CARGO_BUILD_JOBS=2 and /tmp output by default.
Allow about 15 GiB free for generated media, retained outputs and build caches.
Reference binaries are included in the binaries archive. Large audio fixtures and
long video are regenerated. The normal engine has no FFmpeg runtime dependency.


Current evidence: verification-summary.json, matrix.json, python-loader.json,
epoch-selection-regression.json, concurrency-tests.json, chunk-boundary-tests.json,
v0.2.0/audio-identity-generated-media.json, v0.2.0/identity-repo-fixtures.json,
v0.2.0/corruption-fuzz.json, audio-write-failure.json, epyc-benchmarks-v020.json,
epyc-benchmark-verification-v020.json and memory-release.json.
Suites overlap; these counts are not additive proof of distinct independent cases.
This is fixture-based verification, not codec conformance certification, a security
audit or formal proof of leak freedom. BUILD_STATUS lists the remaining limits.
