# BENCHMARK_REPORT — TenzorPipe 0.1.9

For the 330-second input, median wall time is **7.312 s with two video
workers**, **9.506 s with one**, and **5.873 s for the
FFmpeg/Python/Arrow baseline**. Two workers reduce wall time by
**23.1%** versus the current single-video-decoder path,
but remain **24.5% slower than the baseline**.
The supplied v0.1.8 N2 median is 7.231 s; the repair is not presented
as a speed improvement over that scheduler.

## Repeated measurements

Each row below is three serial runs on the same host. Middle-round order is reversed.
Profile runs are separate and excluded from these medians. RSS and CPU columns are
medians of per-run measurements. N1 is the default; N2 is the recommendation for this
host. N3's small advantage does not justify its extra decoder RAM. More workers do
not guarantee more throughput; automatic core-count selection is opt-in.

| Mode | Median wall s | Min–max s | Peak RSS MiB | CPU (100%=one core) | Media s/s | Epochs/s |
|---|---:|---:|---:|---:|---:|---:|
| v016 | 9.411 | 9.199–10.026 | 60.70 | 182.4% | 35.07 | 70.13 |
| v018-N2 | 7.231 | 7.161–7.396 | 81.17 | 248.7% | 45.64 | 91.27 |
| N1 | 9.506 | 9.295–9.527 | 60.67 | 182.4% | 34.71 | 69.43 |
| N2 | 7.312 | 7.233–7.372 | 78.85 | 241.7% | 45.13 | 90.26 |
| N3 | 7.229 | 7.180–7.648 | 95.36 | 242.0% | 45.65 | 91.30 |
| N4 | 7.395 | 7.388–7.421 | 116.07 | 243.6% | 44.63 | 89.25 |
| N8 | 7.520 | 7.287–7.559 | 191.74 | 246.9% | 43.88 | 87.77 |
| baseline | 5.873 | 5.702–6.013 | 259.82 | 147.7% | 56.19 | 112.37 |

## Workload and method

Synthetic 640×360, 30 fps High Profile H.264 with B-frames and 48 kHz stereo AAC;
224×224 RGB/CHW float32, 50×64 Log-Mel per 500 ms epoch, batch32 uncompressed IPC.
720p fixtures establish correctness; these long-input speeds are **not 720p/1080p
benchmarks**. The 330-second output has 660 epochs, 21 batches and
399.17 MiB. The independent baseline output is
387.07 MiB.

Baseline code is reference/ffmpeg_arrow_baseline.py: concurrent FFmpeg video/audio
pipes, NumPy STFT/Mel, then PyArrow batching. Decoder and BLAS thread settings are 1.
This measures the complete pipeline, not standalone FFmpeg or the fastest possible
FFmpeg configuration. The engine uses source-built OpenH264 with NASM assembly,
one audio worker, independent video decoder workers and one collector/writer.

Host: Linux-6.18.44-x86_64-with-glibc2.39, AMD EPYC 9V74 80-Core Processor, 9 allowed
logical CPUs. Wall time includes startup and completed serialization. Process-tree
RSS and physical writes are sampled every 10 ms; wait4 CPU/rusage are also retained.
Sampling can miss brief peaks. Hashing/source comparisons run after the timed process
exits. Shared-host scheduling, cache and filesystem variation remain; three runs are
not a confidence interval. All measured paths use /tmp. No claimed 10× or sub-six-second
long-run target is substituted for measurement.

## Memory across duration

| Mode | Actual media s | Wall s | Sampled peak RSS MiB | Peak anonymous MiB | Peak file-backed MiB | Artifact MiB |
|---|---:|---:|---:|---:|---:|---:|
| N2 | 330.000000 | 7.535 | 78.43 | 70.49 | 8.05 | 399.17 |
| N2 | 660.000000 | 14.173 | 79.90 | 72.13 | 7.89 | 798.34 |
| N2 | 1320.010677 | 27.711 | 83.08 | 74.89 | 8.19 | 1597.28 |
| v016 | 1320.010677 | 38.315 | 64.20 | 56.47 | 8.98 | 1597.28 |

Fourfold duration increases N2 sampled peak RSS from
78.43 to
83.08 MiB, while output grows to
1597.28 MiB. Anonymous/file peaks occur at different
instants and should not be summed. The final input is 1320.010677 seconds after
stream-copy looping; its partial final epoch is included, not dropped. Old/new
22-minute logical columns match exactly. This supports bounded tensor streaming
with modest metadata growth, not a formal zero-leak or constant-RSS claim.

Batch2/32/64 and multiple worker counts are recorded below. Tensor queue, chunk
window and Arrow batch budgets are independent. In particular, the chunk budget
includes the emitted chunk; it excludes native decoder state and allocator overhead.
Planning is capped before allocation and mapped source pages are released during
indexing. Source input remains immutable throughout.

## Bottleneck after chunking

Separate 330-second runs: **video-only N2 4.726 s**;
**audio-only 6.585 s**. Audio now limits further
video-parallel speed gains on this workload. Within profiled audio work, source
reading/decoding/downmix is the largest timer, followed by windowed-sinc resampling;
Mel computation is smaller. Timers overlap and are not an additive latency breakdown.
No audio algorithm or SIMD change was included in this release. The next measured
experiment should separate AAC decoding from downmix, then optimize the dominant
inner loops while preserving the independent audio fidelity/continuity gates.

| Profile run | Wall s | Sum video decode s | Sum resize s | Audio source s | Sinc s | Mel s | Sum window wait s |
|---|---:|---:|---:|---:|---:|---:|---:|
| N1-profile | 10.356 | 9.690 | 0.296 | 4.406 | 2.424 | 0.413 | 0.000 |
| N2-profile | 8.253 | 10.801 | 0.363 | 4.818 | 2.483 | 0.426 | 4.908 |
| N4-profile | 7.581 | 9.566 | 0.335 | 4.185 | 2.391 | 0.402 | 19.181 |
| N8-profile | 7.524 | 9.685 | 0.342 | 4.118 | 2.408 | 0.406 | 46.176 |

Summed worker waits grow with worker count and can exceed wall time. They describe
backpressure, not wasted CPU time. Profile runs have overhead and are not substituted
for repeated unprofiled measurements.

## Disk writes and all measured runs

A separate LD_PRELOAD libc writable-open audit on 30 seconds observes only the final
artifact (and /dev/null) opened for writing by single/chunked/baseline pipelines:
**0 bytes of observed intermediate decoded-media files**. A deliberately created
intermediate in a child process is detected by its self-test. strace was attempted
but blocked by the environment (PTRACE_TRACEME not permitted); its failure log is
included. Dynamic-libc interception can miss direct syscalls or static executables,
so this is not an exhaustive syscall audit. Code inspection also finds no intermediate
media writer. Instrumented runs are excluded from timings.
Sampled physical writes include final output, logs and filesystem effects; they are
not an exact intermediate-write count. All 35 artifacts are independently loaded
and checked; version/mode/batch-size engine columns match.

| Run | Wall s | Peak RSS MiB | CPU | Artifact MiB | Sampled writes MiB | Media s/s |
|---|---:|---:|---:|---:|---:|---:|
| v016-330-b32-v016-r0 | 9.199 | 60.76 | 184.6% | 399.17 | 399.18 | 35.87 |
| v018-N2-330-b32-v018-N2-r1 | 7.161 | 81.65 | 254.3% | 399.17 | 399.18 | 46.08 |
| tenzor-330-b32-N1-r2 | 9.527 | 60.61 | 182.1% | 399.17 | 399.18 | 34.64 |
| tenzor-330-b32-N2-r3 | 7.312 | 79.11 | 251.4% | 399.17 | 399.18 | 45.13 |
| tenzor-330-b32-N3-r4 | 7.229 | 95.63 | 242.0% | 399.17 | 399.19 | 45.65 |
| tenzor-330-b32-N4-r5 | 7.388 | 116.07 | 241.7% | 399.17 | 399.20 | 44.67 |
| tenzor-330-b32-N8-r6 | 7.520 | 193.80 | 248.4% | 399.17 | 399.19 | 43.88 |
| baseline-330-b32-baseline-r7 | 6.013 | 259.82 | 149.5% | 387.07 | 387.07 | 54.88 |
| baseline-330-b32-baseline-r8 | 5.702 | 259.74 | 147.7% | 387.07 | 387.07 | 57.88 |
| tenzor-330-b32-N8-r9 | 7.559 | 191.74 | 246.9% | 399.17 | 399.18 | 43.66 |
| tenzor-330-b32-N4-r10 | 7.421 | 115.49 | 243.6% | 399.17 | 399.18 | 44.47 |
| tenzor-330-b32-N3-r11 | 7.648 | 95.34 | 248.3% | 399.17 | 399.18 | 43.15 |
| tenzor-330-b32-N2-r12 | 7.372 | 78.85 | 241.7% | 399.17 | 399.18 | 44.76 |
| tenzor-330-b32-N1-r13 | 9.295 | 60.68 | 182.5% | 399.17 | 399.18 | 35.50 |
| v018-N2-330-b32-v018-N2-r14 | 7.396 | 81.17 | 245.2% | 399.17 | 399.18 | 44.62 |
| v016-330-b32-v016-r15 | 9.411 | 60.70 | 182.4% | 399.17 | 399.18 | 35.07 |
| v016-330-b32-v016-r16 | 10.026 | 60.70 | 181.9% | 399.17 | 399.18 | 32.91 |
| v018-N2-330-b32-v018-N2-r17 | 7.231 | 81.01 | 248.7% | 399.17 | 399.18 | 45.64 |
| tenzor-330-b32-N1-r18 | 9.506 | 60.67 | 182.4% | 399.17 | 399.18 | 34.71 |
| tenzor-330-b32-N2-r19 | 7.233 | 78.85 | 240.3% | 399.17 | 399.18 | 45.63 |
| tenzor-330-b32-N3-r20 | 7.180 | 95.36 | 241.5% | 399.17 | 399.19 | 45.96 |
| tenzor-330-b32-N4-r21 | 7.395 | 117.06 | 243.7% | 399.17 | 399.18 | 44.63 |
| tenzor-330-b32-N8-r22 | 7.287 | 185.70 | 241.6% | 399.17 | 399.18 | 45.29 |
| baseline-330-b32-baseline-r23 | 5.873 | 261.12 | 147.7% | 387.07 | 387.07 | 56.19 |
| tenzor-330-b32-N1-profile-r24 | 10.356 | 60.62 | 178.9% | 399.17 | 399.18 | 31.86 |
| tenzor-330-b32-N2-profile-r25 | 8.253 | 78.65 | 247.0% | 399.17 | 399.18 | 39.98 |
| tenzor-330-b32-N4-profile-r26 | 7.581 | 115.32 | 241.9% | 399.17 | 399.19 | 43.53 |
| tenzor-330-b32-N8-profile-r27 | 7.524 | 189.07 | 242.2% | 399.17 | 399.18 | 43.86 |
| tenzor-660-b32-N2-memory-r28 | 14.722 | 79.77 | 244.1% | 798.34 | 798.35 | 44.83 |
| tenzor-330-b2-N2-batch2-r29 | 7.036 | 39.33 | 257.2% | 399.46 | 399.48 | 46.90 |
| tenzor-330-b64-N2-batch64-r30 | 7.664 | 81.88 | 234.3% | 399.16 | 399.17 | 43.06 |
| video_only-330-b32-video_only-r31 | 4.726 | 74.03 | 223.5% | 399.17 | 399.18 | 69.83 |
| audio_only-330-b32-audio_only-r32 | 6.585 | 8.22 | 100.4% | 8.33 | 8.34 | 50.11 |
| tenzor-30-b32-N2-r33 | 0.717 | 55.62 | 219.7% | 36.29 | 36.30 | 41.86 |
| baseline-30-b32-baseline-r34 | 0.988 | 218.51 | 129.6% | 35.19 | 35.20 | 30.37 |

## Reproduce

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

Raw records: release-benchmarks.json, memory-release.json, machine-v019.json,
release-benchmark-verification.json, disk-write-audit.json. Normal release binary:
`675c64e599131174ac57d13e9561fdce3ecebbe31056219a932d47bd14cfe2c3`. Old-version measurements carry their own hashes; baseline rows carry no
engine hash. The supplied report's other-host timings are not mixed into these results.
