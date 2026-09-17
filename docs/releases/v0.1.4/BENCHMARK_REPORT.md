# BENCHMARK_REPORT — TenzorPipe 0.1.4

**TenzorPipe does not outperform this baseline in wall time.** On the 330-second
input it takes 19.181 s versus 6.087 s
(3.15× slower), while sampled peak process-tree
RAM is 57.88 MiB versus
265.41 MiB. The delivered engine processes
that input at 17.20 media-seconds/s.

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
| tenzor-30-b32-r0 | 1.954 | 37.80 | 99.6% | 36.29 | 36.30 | 15.35 | 30.70 |
| baseline-30-b32-r1 | 1.030 | 223.25 | 129.3% | 35.19 | 35.20 | 29.13 | 58.27 |
| tenzor-330-b32-r2 | 19.181 | 57.88 | 99.9% | 399.17 | 399.18 | 17.20 | 34.41 |
| baseline-330-b32-r3 | 6.087 | 265.41 | 147.6% | 387.07 | 387.07 | 54.22 | 108.44 |
| tenzor-330-b2-r4 | 19.614 | 20.03 | 99.9% | 399.46 | 399.47 | 16.82 | 33.65 |
| tenzor-330-b64-r5 | 20.048 | 61.51 | 99.9% | 399.16 | 399.18 | 16.46 | 32.92 |
| tenzor-30-b32-r6 | 1.747 | 37.74 | 99.8% | 36.29 | 36.30 | 17.18 | 34.35 |
| baseline-30-b32-r7 | 1.084 | 223.29 | 130.2% | 35.19 | 35.20 | 27.67 | 55.34 |
| tenzor-90-b32-r8 | 6.833 | 56.71 | 99.8% | 108.87 | 108.88 | 13.17 | 26.34 |
| tenzor-660-b32-r9 | 42.429 | 58.41 | 99.9% | 798.34 | 798.35 | 15.56 | 31.11 |
| video_only-330-b32-r10 | 19.436 | 55.37 | 99.6% | 399.17 | 399.18 | 16.98 | 33.96 |
| audio_only-330-b32-r11 | 5.514 | 7.40 | 99.8% | 8.33 | 8.34 | 59.85 | 119.70 |

No intermediate decoded-media files are created: **0 bytes** for both pipelines.
The final artifact itself is the disk output. Rust Arrow writes extra all-valid
bitmap buffers, giving approximately 3% larger artifacts than the PyArrow writer
for these uncompressed arrays. Logical values are checked independently.

Two 30-second runs per pipeline give median times 1.850 s for TenzorPipe
and 1.057 s for the baseline. Long-duration and alternate-batch entries
are individual runs, not averages. No speed confidence interval or broad ranking
is claimed. Raw commands and counters are in `benchmarks.json`.

## Memory behavior

With batch size 32, peak RAM is 56.71 MiB at
90 seconds, 57.88 MiB at 330 seconds and
58.41 MiB at 660 seconds. The 11-minute
artifact is 798.34 MiB; it is never assembled into a whole
video tensor in memory. Batch size 2 uses 20.03 MiB on the
330-second file. Logical outputs across batches 2/32/64 are identical.

This supports bounded tensor/audio buffering and an observed memory plateau.
The short 30-second run does not reach the same allocator high-water mark.
Sample-index metadata remains O(samples) within a 16 MiB cap and IPC footer
metadata remains O(batches). Allocator/page-cache behavior can make batch-size
RSS non-linear. A 10 ms sampler can miss brief peaks; raw `wait4` maximum-RSS values
are included as a separate high-water measure. For example, the audio-only run's
sampled tree peak is 7.40 MiB while its wait4 high-water mark is
17.77 MiB; these measures should not be conflated.

## Bottleneck and repair

The initial compiled decoder's AAC inverse transform used repeated cosine calls
in O(M²) work. A measured 30-second run took 63.061 s. The FFT replacement,
verified against the original formula and independent media, reduces the final
30-second median to 1.850 s (about 34.1× faster). The before-fix
measurement is retained in `evidence/initial/benchmarks-before-aac-fix.json`.
Other hardening changed between those builds, so this ratio describes the observed
release iterations rather than a controlled single-function microbenchmark.

Separate 330-second runs take 19.436 s for video-only and
5.514 s for audio-only. Video processing is the main remaining
cost. Code inspection shows every decoded picture is resized before 2 Hz epoch
selection; resizing only selected pictures is the clearest next optimization.
The diagnostic times are not additive stage timings of the combined run.

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

Binary SHA-256 for every final measured run: `95163cf9b2fd5e0ac2a5431715fe153e53afeff5526024a090523f126eaac0dc`.
