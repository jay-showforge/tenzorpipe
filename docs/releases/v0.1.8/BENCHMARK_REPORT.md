# BENCHMARK_REPORT — TenzorPipe 0.1.6

The paired 330-second medians improve from **15.940 s (v0.1.5) to 10.402 s
(v0.1.6)**: **34.7% less wall time**, **1.53× throughput**.
The same-session baseline is still faster: **5.938 s**.
No sub-six-second result or general advantage over FFmpeg is claimed.

## Workload and measurement

Generated 640×360, 30 fps High Profile H.264 with B-frames and 48 kHz stereo AAC.
Default output: 224×224 RGB/CHW float32, 50×64 Log-Mel, 0.5 s epochs, uncompressed
Arrow IPC. The resolution336 entry is explicitly a 30-second alternative. 720p
fixtures establish correctness, not these long-file performance numbers.

Baseline: two concurrent FFmpeg pipes for selected frames and resampled audio,
NumPy STFT/Mel/log-power and batched PyArrow IPC. FFmpeg decoder/BLAS thread settings
are 1. Native release uses one audio worker, one video worker with unthreaded
OpenH264, and the writer/collector. No intermediate decoded-media files are written
(0 bytes); final output and small logs account for disk writes. Payloads are equal
in shape but decoder/resampler values differ within the stated fidelity gates.

Runs are serial on the same host/output filesystem, after compilation and other
heavy work finished. Wall time includes startup, processing and final serialization.
Two paired old/new repetitions supply the quoted medians, not a confidence interval.
CPU time / wall time uses 100% per core. Process-tree RSS and physical write counters
are sampled every 10 ms; rusage high-water figures are retained separately. Hashing
and independent validation run afterward in separate processes. Cache/shared-host
variation remains. The initial workspace I/O failures and /tmp override are documented
in BUILD_STATUS.md. All pipelines use the same final output location.

| Run | Wall s | Peak MiB | CPU | Artifact MiB | Sampled writes MiB | Media s/s |
|---|---:|---:|---:|---:|---:|---:|
| tenzor-30-b32-r0 | 0.887 | 39.66 | 173.5% | 36.29 | 36.30 | 33.83 |
| baseline-30-b32-r1 | 0.994 | 223.30 | 133.5% | 35.19 | 35.20 | 30.17 |
| tenzor-330-b32-r2 | 9.855 | 60.58 | 184.0% | 399.17 | 399.18 | 33.49 |
| baseline-330-b32-r3 | 5.938 | 265.44 | 148.4% | 387.07 | 387.08 | 55.58 |
| tenzor-330-b2-r4 | 9.919 | 20.97 | 183.8% | 399.46 | 399.46 | 33.27 |
| tenzor-330-b64-r5 | 10.380 | 64.40 | 174.7% | 399.16 | 399.17 | 31.79 |
| tenzor-30-b32-r6 | 0.987 | 39.63 | 174.2% | 36.29 | 36.30 | 30.41 |
| baseline-30-b32-r7 | 1.033 | 223.29 | 129.8% | 35.19 | 35.20 | 29.03 |
| tenzor-90-b32-r8 | 2.904 | 59.68 | 181.2% | 108.87 | 108.88 | 30.99 |
| tenzor-660-b32-r9 | 20.556 | 61.59 | 183.0% | 798.34 | 798.35 | 32.11 |
| video_only-330-b32-r10 | 9.540 | 57.97 | 111.2% | 399.17 | 399.18 | 34.59 |
| audio_only-330-b32-r11 | 6.854 | 8.13 | 100.3% | 8.33 | 8.34 | 48.15 |
| v016-old-330-0 | 15.721 | 57.80 | 99.9% | 399.17 | 399.18 | 20.99 |
| v016-concurrent-330-1 | 10.289 | 60.57 | 182.9% | 399.17 | 399.18 | 32.07 |
| v016-old-330-2 | 16.159 | 57.48 | 99.9% | 399.17 | 399.18 | 20.42 |
| v016-concurrent-330-3 | 10.515 | 60.52 | 181.7% | 399.17 | 399.18 | 31.38 |
| v016-sequential-profile-330-4 | 16.917 | 74.51 | 99.9% | 399.17 | 399.18 | 19.51 |
| v016-concurrent-profile-330-5 | 10.093 | 60.55 | 182.6% | 399.17 | 399.18 | 32.70 |
| v016-rendezvous-330-6 | 11.394 | 59.42 | 174.0% | 399.17 | 399.18 | 28.96 |
| v016-old-memory-660-7 | 30.583 | 58.23 | 100.0% | 798.34 | 798.35 | 21.58 |
| v016-resolution336-30-8 | 1.182 | 67.34 | 161.2% | 80.70 | 80.71 | 25.37 |

Epoch throughput is media throughput ×2 for these 0.5-second cases and is also
recorded directly in the JSON evidence. All 21 measured artifacts are independently
loaded; main batch-size hashes match. Normal-release rows use binary `0e1a46ca8e67d872ac95d46b6541a283737d34dda6f27aaadaf6d9e6ad93f2d7`.

## Memory

Default batch 32 observed 59.68 MiB at 90 s,
60.58 MiB at 330 s and
61.59 MiB at 660 s. The unchanged v0.1.5 binary
on this host used 58.23 MiB at 660 s.
These measurements test duration dependence; they do not enforce an arbitrary
60 MiB ceiling or prove absence of all leaks. Queue payload is byte-capped, worker
working tensors are bounded and Arrow batches are released. Parser/footer metadata
and allocator high-water behavior remain as documented limitations.

## Stage profiling

Active work is separated from channel waiting; see CONCURRENCY_REPORT.md. Timers
measure elapsed wall time around each stage, not per-thread CPU instructions.
Worker durations overlap and must not be summed into pipeline wall time. Separate
video-only/audio-only runs are full pipelines, not additive stage microbenchmarks.
Profiling and queue bookkeeping have measurable overhead; plain runs determine the
headline speed comparison.

## Reproduce

```sh
cargo build --release --locked
cargo test --locked --all-targets
cargo fmt --check
cargo clippy --all-targets --locked -- -D warnings
cargo install cargo-deny --version 0.20.2 --locked
cargo deny check licenses
python3 -m pip install -r python/requirements-test.txt
python3 -m pip install torch==2.14.0+cpu --index-url https://download.pytorch.org/whl/cpu
python3 scripts/generate_matrix.py --long --extended-memory
# Optional output-filesystem override used for the final recorded runs:
export TENZOR_OUTPUT_ROOT=/tmp/tenzor-artifacts
python3 scripts/test_matrix.py
python3 scripts/test_epoch_selection.py --old-binary /path/to/v0.1.5/tenzor
python3 scripts/test_concurrency.py
python3 scripts/stress_publication.py --output-root /tmp
python3 scripts/benchmark_matrix.py
python3 scripts/verify_benchmarks.py
python3 scripts/benchmark_concurrency.py --old-binary /path/to/v0.1.5/tenzor
```

Host: Linux-6.18.44-x86_64-with-glibc2.39; AMD EPYC 9V74 80-Core Processor; 9 visible CPUs.
Raw commands, hashes, bytes and counters: benchmarks.json and concurrency-benchmarks.json.
