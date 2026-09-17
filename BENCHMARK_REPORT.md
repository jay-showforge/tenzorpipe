# BENCHMARK_REPORT — TenzorPipe 0.2.0, EPYC rerun

Audio-only median improves from **6.650 s to 1.421 s**:
**4.68× faster**, with exact output identity.
At two video workers, full A/V improves from **7.180 s to 4.806 s**.
The recommended **N6 median is 1.992 s**, versus
**5.895 s** for the documented FFmpeg/Python/Arrow baseline:
**2.96× throughput** on this workload/host.
The faster parallel setting uses more CPU cores than that baseline; this is not
an equal-core decoder comparison or proof of universal superiority.

## Five paired unprofiled runs

| Variant | Median wall s | Min–max s | Median peak RSS MiB | CPU (100%=one core) | Media s/s | Epochs/s |
|---|---:|---:|---:|---:|---:|---:|
| old-N2 | 7.180 | 6.985–7.512 | 78.30 | 242.8% | 45.96 | 91.92 |
| N1 | 9.189 | 9.061–9.527 | 61.29 | 135.6% | 35.91 | 71.83 |
| N2 | 4.806 | 4.738–5.014 | 77.50 | 266.3% | 68.67 | 137.34 |
| N3 | 3.444 | 3.321–3.574 | 90.04 | 375.7% | 95.81 | 191.62 |
| N4 | 2.694 | 2.567–2.952 | 102.11 | 489.8% | 122.48 | 244.96 |
| N6 | 1.992 | 1.878–2.631 | 128.16 | 697.1% | 165.67 | 331.34 |
| N8 | 2.275 | 1.935–2.609 | 159.02 | 658.4% | 145.07 | 290.14 |
| baseline | 5.895 | 5.448–6.082 | 253.29 | 146.1% | 55.98 | 111.96 |
| old-audio | 6.650 | 6.534–6.901 | 8.41 | 100.3% | 49.63 | 99.25 |
| audio-inline | 2.248 | 2.138–2.266 | 8.48 | 100.5% | 146.78 | 293.56 |
| audio-thread | 1.421 | 1.364–1.488 | 8.68 | 176.6% | 232.22 | 464.45 |

N1/2/3/4/6/8 are current video-worker counts. old-N2 and old-audio use the pinned
v0.1.9 binary. audio-inline uses `--no-audio-decode-thread`; audio-thread is the
current concurrent default. Each repeated variant has one excluded warm-up and
five measured runs; order reverses on alternate rounds. No profile timer is enabled
in these repeated runs. Profile and short/batch experiments below are separate.

Selection rule: choose the smallest measured worker count within 5% of the fastest
median. This yields **6**. The CLI default remains 1 as a conservative resource
policy; recommended deployment command for this measured workload:

```sh
./bin/tenzor-linux-x86_64 -i input.mp4 -o output.tenzor --video-workers 6
```

## Workload, baseline and attribution

Synthetic 330-second 640×360/30fps H.264 High Profile/B-frame video with 48 kHz stereo
AAC; 224×224 RGB/CHW float32, 50×64 Log-Mel per 0.5 s epoch, batch 32, uncompressed Arrow
IPC. Output has 660 epochs / 21 batches and 399.17 MiB;
baseline output is 387.07 MiB.720p clips are
correctness fixtures; these speeds are not 720p/1080p throughput claims.

Baseline: reference/ffmpeg_arrow_baseline.py, concurrent FFmpeg video/audio pipes,
NumPy STFT/Mel and PyArrow batches. FFmpeg decoder and BLAS thread settings are 1.
This is the complete documented pipeline including process startup, not standalone
FFmpeg or the best possible tuned FFmpeg implementation. The Rust engine uses
independent video decoders, one audio decode worker, one resample/Mel worker and a
collector. More cores explain part of the full-pipeline advantage. The inline-audio
comparison separately demonstrates the benefit of the audio inner-loop changes.

Host: Linux-6.18.44-x86_64-with-glibc2.39, AMD EPYC 9V74 80-Core Processor, 9 allowed
logical CPUs. Both Rust versions use 1.98.1 with the same locked runtime dependencies;
OpenH264 is source-built with NASM 2.16.01. Wall time includes startup and finished
serialization. wait4 supplies process-tree CPU and rusage; /proc/psutil samples RSS
and physical writes every 10 ms with PID-namespace mapping. Sampling can miss brief
peaks. Validation runs in a separate process after timing. Tests/compilation were
finished before serial benchmarks; shared-host/cache/filesystem variance remains.
The reported five-run ranges are not statistical confidence intervals.

## Memory across duration

| Version/workers | Actual media s | Wall s | Peak RSS MiB | Peak anonymous MiB | Peak file-backed MiB | Artifact MiB |
|---|---:|---:|---:|---:|---:|---:|
| new / N6 | 330.000000 | 2.751 | 133.65 | 125.39 | 8.84 | 399.17 |
| new / N6 | 660.000000 | 5.192 | 135.85 | 127.36 | 8.68 | 798.34 |
| new / N6 | 1320.010677 | 10.458 | 141.31 | 132.70 | 8.74 | 1597.28 |
| v019 / N2 | 1320.010677 | 28.682 | 83.53 | 75.18 | 8.41 | 1597.28 |

At N6, fourfold duration grows peak RSS from
133.65 to
141.31 MiB, while output grows to
1597.28 MiB. The stream-copy loop is actually
1320.010677 s and includes 2641
epochs, including its partial tail. Every logical column matches v0.1.9 exactly.
Anonymous/file-backed maxima can occur at different instants and are not additive.
This supports bounded tensor streaming with modest metadata growth, not a formal
zero-leak or constant-RSS claim. The old N2 memory row is a reference, not a same-worker
memory comparison; repeated old/new N2 RSS appears in the table above.

The extra decoded-audio queue is 32 chunks: at most 128 KiB queued mono PCM for supported
AAC access units, 512 KiB for WAV, plus in-flight chunks. The contiguous input buffer
releases consumed samples in 32, 768-sample blocks. Video chunk/epoch/batch budgets and
native decoder state remain separate. More video workers increase total RSS.

## Profile diagnostics

| Separate run | Wall s | Sum video decode s | Sum resize s | Audio source s | Sinc s | Mel s | Audio source wait s |
|---|---:|---:|---:|---:|---:|---:|---:|
| N1-profile | 9.042 | 8.478 | 0.285 | 1.431 | 0.581 | 0.385 | 0.016 |
| N2-profile | 4.737 | 8.932 | 0.329 | 1.319 | 0.598 | 0.393 | 0.057 |
| N3-profile | 3.442 | 9.142 | 0.325 | 1.333 | 0.601 | 0.398 | 0.159 |
| N4-profile | 2.835 | 9.412 | 0.336 | 1.523 | 0.616 | 0.403 | 0.408 |
| N6-profile | 2.441 | 9.585 | 0.346 | 1.360 | 0.606 | 0.400 | 0.326 |
| N8-profile | 2.661 | 10.639 | 0.375 | 1.520 | 0.673 | 0.453 | 0.505 |
| audio-thread-profile | 1.304 | 0.000 | 0.000 | 1.224 | 0.565 | 0.378 | 0.340 |
| audio-inline-profile | 2.209 | 0.000 | 0.000 | 1.228 | 0.570 | 0.384 | 0.000 |
| old-audio-profile | 6.490 | 0.000 | 0.000 | 3.757 | 2.326 | 0.385 | 0.000 |
| video-N4 | 2.594 | 9.170 | 0.327 | 0.000 | 0.000 | 0.000 | 0.000 |

Timers overlap across threads and are wall durations, not additive CPU accounting.
`audio_source` includes read/decode/downmix on the producer; `audio_source_wait` is
consumer waiting and is excluded from the resample timer. The source separates
Huffman lookup, contiguous sinc access, FFT buffer reuse and exact formula tables;
this release accepts those changes unchanged from the supplied source.

The smaller-IMDCT experiment remains deferred: it would change numerical results.
No new floating-point tolerance was silently approved. The next performance phase
can evaluate it separately with a concrete waveform/Log-Mel error budget. The current
release retains exact identity and its tested fallback for inline audio decoding.

## Artifact verification and disk writes

80 outputs were checked for all-batch finite values, timestamps, dimensions, counts
and logical hashes outside timing: 11 warm-ups, 55 paired measurements and 14 separate
experiments. 25 retained artifacts pass delayed rereads and full baseline/source
fidelity checks. The other repetitions are deleted only after validation to bound
test-disk use; their hashes and measurements remain in the JSON evidence.

A separate LD_PRELOAD libc writable-open audit checks single/chunked/baseline30-second
pipelines. Its descendant sentinel confirms interception works. Only final output
and /dev/null are observed: 0 bytes of intermediate decoded-media files in the observed
calls. This can miss direct syscalls/static executables and is not an exhaustive
syscall audit. strace was previously blocked by this environment; instrumentation is
excluded from throughput timing. Sampled physical writes below include output/logs
and filesystem effects, not an exact intermediate-write count.

## All non-warm-up measured runs

| Run | Wall s | Peak RSS MiB | CPU | Artifact MiB | Sampled writes MiB | Media s/s |
|---|---:|---:|---:|---:|---:|---:|
| tenzor-330-b32-audio-thread-r1 | 1.488 | 8.68 | 175.3% | 8.33 | 8.34 | 221.80 |
| tenzor-330-b32-audio-inline-r1 | 2.248 | 8.44 | 100.5% | 8.33 | 8.34 | 146.78 |
| tenzor-330-b32-old-audio-r1 | 6.633 | 8.26 | 100.4% | 8.33 | 8.34 | 49.75 |
| baseline-330-b32-baseline-r1 | 5.744 | 251.61 | 146.7% | 387.07 | 387.07 | 57.45 |
| tenzor-330-b32-N8-r1 | 2.609 | 164.14 | 568.5% | 399.17 | 399.18 | 126.47 |
| tenzor-330-b32-N6-r1 | 2.631 | 133.32 | 563.2% | 399.17 | 399.18 | 125.43 |
| tenzor-330-b32-N4-r1 | 2.952 | 103.86 | 463.0% | 399.17 | 399.18 | 111.79 |
| tenzor-330-b32-N3-r1 | 3.531 | 91.15 | 379.2% | 399.17 | 399.18 | 93.45 |
| tenzor-330-b32-N2-r1 | 4.744 | 77.58 | 269.8% | 399.17 | 399.20 | 69.55 |
| tenzor-330-b32-N1-r1 | 9.189 | 61.28 | 135.6% | 399.17 | 399.19 | 35.91 |
| tenzor-330-b32-old-N2-r1 | 7.225 | 79.34 | 246.3% | 399.17 | 399.18 | 45.68 |
| tenzor-330-b32-old-N2-r2 | 7.180 | 78.61 | 240.3% | 399.17 | 399.18 | 45.96 |
| tenzor-330-b32-N1-r2 | 9.527 | 61.29 | 136.4% | 399.17 | 399.18 | 34.64 |
| tenzor-330-b32-N2-r2 | 4.806 | 77.54 | 265.9% | 399.17 | 399.18 | 68.67 |
| tenzor-330-b32-N3-r2 | 3.444 | 89.67 | 375.5% | 399.17 | 399.18 | 95.81 |
| tenzor-330-b32-N4-r2 | 2.694 | 102.11 | 489.8% | 399.17 | 399.18 | 122.48 |
| tenzor-330-b32-N6-r2 | 1.878 | 128.16 | 700.3% | 399.17 | 399.18 | 175.75 |
| tenzor-330-b32-N8-r2 | 1.957 | 158.07 | 702.9% | 399.17 | 399.18 | 168.62 |
| baseline-330-b32-baseline-r2 | 5.953 | 253.32 | 141.8% | 387.07 | 387.07 | 55.43 |
| tenzor-330-b32-old-audio-r2 | 6.901 | 8.39 | 100.3% | 8.33 | 8.34 | 47.82 |
| tenzor-330-b32-audio-inline-r2 | 2.266 | 8.48 | 100.0% | 8.33 | 8.34 | 145.65 |
| tenzor-330-b32-audio-thread-r2 | 1.431 | 8.63 | 176.5% | 8.33 | 8.34 | 230.55 |
| tenzor-330-b32-audio-thread-r3 | 1.421 | 8.75 | 176.6% | 8.33 | 8.34 | 232.22 |
| tenzor-330-b32-audio-inline-r3 | 2.259 | 8.48 | 101.0% | 8.33 | 8.34 | 146.09 |
| tenzor-330-b32-old-audio-r3 | 6.650 | 8.45 | 100.0% | 8.33 | 8.34 | 49.63 |
| baseline-330-b32-baseline-r3 | 5.895 | 253.27 | 146.1% | 387.07 | 387.07 | 55.98 |
| tenzor-330-b32-N8-r3 | 2.342 | 158.15 | 658.4% | 399.17 | 399.18 | 140.90 |
| tenzor-330-b32-N6-r3 | 1.992 | 126.26 | 697.1% | 399.17 | 399.18 | 165.67 |
| tenzor-330-b32-N4-r3 | 2.752 | 101.78 | 490.3% | 399.17 | 399.18 | 119.91 |
| tenzor-330-b32-N3-r3 | 3.574 | 90.49 | 375.4% | 399.17 | 399.18 | 92.34 |
| tenzor-330-b32-N2-r3 | 4.968 | 76.45 | 266.4% | 399.17 | 399.18 | 66.42 |
| tenzor-330-b32-N1-r3 | 9.460 | 61.52 | 134.6% | 399.17 | 399.18 | 34.88 |
| tenzor-330-b32-old-N2-r3 | 7.512 | 78.17 | 243.5% | 399.17 | 399.18 | 43.93 |
| tenzor-330-b32-old-N2-r4 | 7.024 | 78.19 | 242.8% | 399.17 | 399.18 | 46.98 |
| tenzor-330-b32-N1-r4 | 9.156 | 61.25 | 135.9% | 399.17 | 399.18 | 36.04 |
| tenzor-330-b32-N2-r4 | 5.014 | 77.50 | 266.3% | 399.17 | 399.18 | 65.81 |
| tenzor-330-b32-N3-r4 | 3.403 | 90.04 | 375.7% | 399.17 | 399.18 | 96.97 |
| tenzor-330-b32-N4-r4 | 2.567 | 100.96 | 490.1% | 399.17 | 399.18 | 128.54 |
| tenzor-330-b32-N6-r4 | 1.880 | 126.40 | 702.1% | 399.17 | 399.18 | 175.56 |
| tenzor-330-b32-N8-r4 | 1.935 | 159.02 | 707.1% | 399.17 | 399.18 | 170.57 |
| baseline-330-b32-baseline-r4 | 6.082 | 253.29 | 144.1% | 387.07 | 387.07 | 54.25 |
| tenzor-330-b32-old-audio-r4 | 6.802 | 8.45 | 100.3% | 8.33 | 8.34 | 48.52 |
| tenzor-330-b32-audio-inline-r4 | 2.238 | 8.50 | 100.6% | 8.33 | 8.34 | 147.43 |
| tenzor-330-b32-audio-thread-r4 | 1.390 | 8.70 | 177.9% | 8.33 | 8.34 | 237.49 |
| tenzor-330-b32-audio-thread-r5 | 1.364 | 8.55 | 177.9% | 8.33 | 8.34 | 241.87 |
| tenzor-330-b32-audio-inline-r5 | 2.138 | 8.42 | 100.4% | 8.33 | 8.34 | 154.35 |
| tenzor-330-b32-old-audio-r5 | 6.534 | 8.41 | 100.1% | 8.33 | 8.34 | 50.51 |
| baseline-330-b32-baseline-r5 | 5.448 | 253.35 | 147.8% | 387.07 | 387.07 | 60.58 |
| tenzor-330-b32-N8-r5 | 2.275 | 160.93 | 617.8% | 399.17 | 399.18 | 145.07 |
| tenzor-330-b32-N6-r5 | 2.024 | 128.52 | 690.3% | 399.17 | 399.18 | 163.03 |
| tenzor-330-b32-N4-r5 | 2.605 | 102.84 | 488.2% | 399.17 | 399.18 | 126.68 |
| tenzor-330-b32-N3-r5 | 3.321 | 88.77 | 380.5% | 399.17 | 399.18 | 99.36 |
| tenzor-330-b32-N2-r5 | 4.738 | 77.32 | 263.9% | 399.17 | 399.18 | 69.65 |
| tenzor-330-b32-N1-r5 | 9.061 | 61.32 | 134.5% | 399.17 | 399.18 | 36.42 |
| tenzor-330-b32-old-N2-r5 | 6.985 | 78.30 | 241.8% | 399.17 | 399.18 | 47.25 |
| tenzor-330-b32-N1-profile-r6 | 9.042 | 61.25 | 135.9% | 399.17 | 399.18 | 36.50 |
| tenzor-330-b32-N2-profile-r6 | 4.737 | 76.96 | 270.2% | 399.17 | 399.18 | 69.67 |
| tenzor-330-b32-N3-profile-r6 | 3.442 | 90.42 | 380.0% | 399.17 | 399.18 | 95.88 |
| tenzor-330-b32-N4-profile-r6 | 2.835 | 103.05 | 479.3% | 399.17 | 399.18 | 116.39 |
| tenzor-330-b32-N6-profile-r6 | 2.441 | 134.76 | 552.7% | 399.17 | 399.18 | 135.18 |
| tenzor-330-b32-N8-profile-r6 | 2.661 | 167.17 | 523.4% | 399.17 | 399.20 | 124.03 |
| tenzor-330-b32-audio-thread-profile-r6 | 1.304 | 8.67 | 179.1% | 8.33 | 8.35 | 253.00 |
| tenzor-330-b32-audio-inline-profile-r6 | 2.209 | 8.49 | 100.0% | 8.33 | 8.34 | 149.37 |
| tenzor-330-b32-old-audio-profile-r6 | 6.490 | 8.18 | 100.2% | 8.33 | 8.34 | 50.85 |
| tenzor-330-b2-N4-batch 2-r7 | 2.618 | 62.77 | 514.3% | 399.46 | 399.46 | 126.03 |
| tenzor-330-b64-N4-batch 64-r7 | 3.281 | 105.11 | 422.4% | 399.16 | 399.17 | 100.58 |
| tenzor-330-b32-video-N4-r8 | 2.594 | 99.45 | 418.5% | 399.17 | 399.18 | 127.20 |
| tenzor-30-b32-N4-short-r9 | 0.335 | 77.37 | 384.8% | 36.29 | 36.30 | 89.45 |
| baseline-30-b32-baseline-short-r9 | 0.955 | 212.06 | 131.7% | 35.19 | 35.20 | 31.42 |

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


Current evidence: epyc-benchmarks-v020.json, epyc-retained-v020.json,
epyc-benchmark-verification-v020.json, machine-v020.json, memory-release.json and
disk-write-audit.json. Binary SHA-256:`c914ddbd5663c9837c501020e58f98b1cdb150708ae0b37fcbc5df8374858b77`. Historical two-core Claude numbers
are archived separately and not mixed into the EPYC measurements.
