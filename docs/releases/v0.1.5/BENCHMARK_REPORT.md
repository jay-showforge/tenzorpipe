# BENCHMARK_REPORT — TenzorPipe 0.1.5

**TenzorPipe does not outperform this baseline in wall time.** On the 330-second
input it takes 15.119 s versus 6.184 s
(2.44× slower), while sampled peak process-tree
RAM is 56.98 MiB versus
265.79 MiB. The delivered engine processes
that input at 21.83 media-seconds/s.

## Workload and baseline

Generated 640×360, 30 fps, High Profile H.264 with B-frames and 48 kHz stereo AAC.
Output is 224×224 RGB/CHW float32 plus 50×64 Log-Mel per 0.5-second epoch,
uncompressed Arrow IPC. Short 720p tests are correctness tests; these numbers
must not be presented as 720p/1080p/4K benchmark measurements.

`reference/ffmpeg_arrow_baseline.py` runs two FFmpeg pipes, selecting every 15th
CFR picture and decoding/downmixing/resampling audio. NumPy performs the same
left-aligned Hann STFT/Mel/log-power stage; PyArrow writes bounded batches with
the same logical tensor shapes and timestamps. It is a streaming baseline, not
an intentionally slow extraction-to-images pipeline. FFmpeg's stock resampler
is different from the engine's 129-tap phase table. Active audio Log-Mel MAE
between the two outputs is 0.000081. Video
fidelity metrics are in TEST_REPORT.md. Outputs are not claimed bit-identical
between decoders/resamplers.

Both pipelines include startup, decode, preprocessing and final serialization.
The baseline has concurrent audio/video processes; decoder threads are set to 1
and BLAS/OpenMP thread variables are 1. TenzorPipe is principally single-threaded.
This is an end-to-end comparison, not an equal-CPU-budget or isolated-codec test.

## Measured runs

MiB = 1,048,576 bytes. CPU is aggregated user+system time divided by wall time,
so 100% is one core. Physical write counters include final artifacts and small
logs; these sampled counters are not a disk bandwidth benchmark.

| Run | Wall s | Peak tree MiB | CPU | Artifact MiB | Sampled writes MiB | Media s/s | Epochs/s |
|---|---:|---:|---:|---:|---:|---:|---:|
| tenzor-30-b32-r0 | 1.360 | 37.24 | 99.8% | 36.29 | 36.30 | 22.05 | 44.10 |
| baseline-30-b32-r1 | 0.986 | 223.89 | 129.3% | 35.19 | 35.20 | 30.44 | 60.88 |
| tenzor-330-b32-r2 | 15.119 | 56.98 | 99.9% | 399.17 | 399.18 | 21.83 | 43.65 |
| baseline-330-b32-r3 | 6.184 | 265.79 | 148.2% | 387.07 | 387.08 | 53.36 | 106.72 |
| tenzor-330-b2-r4 | 15.513 | 19.48 | 99.2% | 399.46 | 399.47 | 21.27 | 42.54 |
| tenzor-330-b64-r5 | 17.227 | 60.15 | 98.6% | 399.16 | 399.20 | 19.16 | 38.31 |
| tenzor-30-b32-r6 | 1.424 | 37.24 | 99.0% | 36.29 | 36.30 | 21.07 | 42.13 |
| baseline-30-b32-r7 | 1.152 | 223.93 | 131.7% | 35.19 | 35.20 | 26.04 | 52.08 |
| tenzor-90-b32-r8 | 4.845 | 55.97 | 99.2% | 108.87 | 108.88 | 18.57 | 37.15 |
| tenzor-660-b32-r9 | 30.732 | 74.98 | 98.6% | 798.34 | 798.34 | 21.48 | 42.95 |
| video_only-330-b32-r10 | 11.701 | 54.67 | 99.5% | 399.17 | 399.18 | 28.20 | 56.40 |
| audio_only-330-b32-r11 | 5.374 | 7.27 | 99.8% | 8.33 | 8.34 | 61.41 | 122.82 |

No intermediate decoded-media files are created: **0 bytes** for both pipelines.
The final artifact itself is the disk output. Rust Arrow writes extra all-valid
bitmap buffers, giving approximately 3% larger artifacts than the PyArrow writer
for these uncompressed arrays. Logical values are checked independently.

Two 30-second runs per pipeline give median times 1.392 s for TenzorPipe
and 1.069 s for the baseline. Long-duration and alternate-batch entries
are individual runs, not averages. No speed confidence interval or broad ranking
is claimed. Raw commands and counters are in `benchmarks.json`.

## Memory behavior

With batch size 32, peak RAM is 55.97 MiB at
90 seconds, 56.98 MiB at 330 seconds and
74.98 MiB at 660 seconds. The 11-minute
artifact is 798.34 MiB; it is never assembled into a whole
video tensor in memory. Batch size 2 uses 19.48 MiB on the
330-second file. Logical outputs across batches 2/32/64 are identical.

An additional 22-minute check in EPOCH_SELECTION_REPORT.md addresses the higher
11-minute RSS high-water mark. These measurements support bounded tensor/audio
buffering, with allocator-dependent peak variation rather than identical RSS.
The short 30-second run does not reach the same allocator high-water mark.
Sample-index metadata remains O(samples) within a 16 MiB cap and IPC footer
metadata remains O(batches). Allocator/page-cache behavior can make batch-size
RSS non-linear. A 10 ms sampler can miss brief peaks; raw `wait4` maximum-RSS values
are included as a separate high-water measure. For example, the audio-only run's
sampled tree peak is 7.27 MiB while its wait4 high-water mark is
18.03 MiB; these measures should not be conflated.

## Bottleneck and repair

The initial compiled decoder's AAC inverse transform used repeated cosine calls
in O(M²) work. A measured 30-second run took 63.061 s. The FFT replacement,
verified against the original formula and independent media, reduces the final
30-second median to 1.392 s (about 45.3× faster). The before-fix
measurement is retained in `evidence/initial/benchmarks-before-aac-fix.json`.
Other hardening changed between those builds, so this ratio describes the observed
release iterations rather than a controlled single-function microbenchmark.

Separate 330-second runs take 11.701 s for video-only and
5.374 s for audio-only. Video processing is the main remaining
cost. Version 0.1.5 now resizes only epoch-selected pictures: 660 of 9,900 in
this workload. The reduction in resize calls is 93.33%, but the rest of decoding,
preprocessing and serialization remains. This does not yield the suggested 10×
overall gain; the measured old/new comparison is in EPOCH_SELECTION_REPORT.md.
The diagnostic times are not additive stage timings of the combined run, and
no detailed profiler attribution is claimed for the remaining video-side cost.

## Reproduce and limits

```sh
cargo build --release --locked
python3 scripts/generate_matrix.py --long --extended-memory
python3 scripts/benchmark_matrix.py
python3 scripts/verify_benchmarks.py
```

Host: Linux-6.18.44-x86_64-with-glibc2.39; AMD EPYC 9V74 80-Core Processor; 9 visible CPUs.
Python: 3.12.14 (main, Aug 25 2026, 14:00:49) [Clang 22.1.3 ]. See `machine.json` for affinity and thread settings.
Runs are serial. Measurements include warm-cache effects and ordinary shared-host
variation. CPU utilization comes from child-tree resource accounting; peak tree RSS
and physical write bytes are sampled via /proc. The harness resolves PID namespaces
explicitly. Synthetic clips and one host do not establish production throughput
across arbitrary media, storage systems, devices or workloads.

Binary SHA-256 for every final measured run: `ad8f8cdf7df93b3be2da5a6c5098196051c41a44f1784aadfdcb65da64dc249d`.
