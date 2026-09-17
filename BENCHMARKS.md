# BENCHMARKS — TenzorPipe vs DALI vs TorchCodec vs FFmpeg (1080p ingestion)

Generated 2026-09-16 19:18 by `bench/gpu_bench.py` — 5 warm-up + **50 measured iterations** per contender, each contender in a fresh process.

## Task (identical for every contender)

1080p30 H.264 MP4 → source frame nearest each 0.5 s epoch start → 224×224 nearest-neighbour (aligned corners) → float32 RGB CHW in [-1, 1]. In the **A/V** scenario, contenders that can decode AAC also produce the 64-band Log-Mel (16 kHz mono, 400-sample Hann, 160 hop, 50 rows/epoch). An iteration ends when every output tensor has been checksummed (forces CUDA sync). TenzorPipe's iteration = run the CLI to `/dev/shm` **and** load the Arrow file into PyTorch tensors.

| Clip | Resolution | Frames | Bitrate | Profile | Epochs |
|---|---|---:|---:|---|---:|
| `synthetic-1080p30-h264-20s-video-only.mp4` (video) | 1920×1080 @ 30/1 | 600 | 7940 kb/s | High | 40 |
| `synthetic-1080p30-h264-aac-20s.mp4` (av) | 1920×1080 @ 30/1 | 600 | 8138 kb/s | High | 40 |

## Scenario 1 — video only (all contenders)

| Contender | Decode | Audio | Latency ms (median) | p5–p95 ms | Source FPS | Epochs/s | Peak RSS MiB | RSS growth MiB | Child RSS MiB | VRAM torch MiB | VRAM NVML Δ MiB | CPU cores | Setup+cold s | Video MAE | Mel MAE |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| **tenzorpipe-w1** | CPU (OpenH264) | n/a | 5022.5 | 4963.2–5187.4 | 119 | 7.9 | 674 | 110 | 87 | 0 | 0 (no CUDA) | 1.11 | 6.21 | ref | — |
| **tenzorpipe-w6** | CPU (OpenH264) | n/a | 1468.8 | 1416.2–1559.9 | 406 | 27.1 | 889 | 324 | 301 | 0 | 0 (no CUDA) | 4.92 | 3.06 | 0.0000 | — |
| **tenzorpipe-auto** | CPU (OpenH264) | n/a | 1031.5 | 1011.1–1079.2 | 580 | 38.6 | 1084 | 520 | 497 | 0 | 0 (no CUDA) | 9.20 | 2.35 | 0.0000 | — |
| dali-nvdec | GPU (NVDEC) | n/a | 441.7 | 440.2–445.6 | 1364 | 90.9 | 1074 | 0 | — | 46 | 822 | 0.42 | 2.23 | 0.0799 | — |
| torchcodec-cuda | GPU (NVDEC) | n/a | 276.0 | 267.7–286.7 | 2173 | 144.8 | 1128 | 0 | — | 256 | 650 | 0.47 | 2.63 | 0.0047 | — |
| torchcodec-cpu | CPU (libavcodec) | n/a | 2216.3 | 2190.1–2261.6 | 270 | 18.0 | 1005 | 281 | — | 0 | 0 (no CUDA) | 1.31 | 4.30 | 0.0070 | — |
| torchvision-read_video | *skipped:* ImportError: cannot import name 'read_video' from 'torchvision.io' (removed in torchvision 0.29) | | | | | | | | | | | | | | |
| ffmpeg-pipe | CPU (libavcodec) | n/a | 542.4 | 525.3–585.1 | 1095 | 73.0 | 707 | 172 | 167 | 0 | 0 (no CUDA) | 9.01 | 1.83 | 0.0466 | — |

## Scenario 2 — video + AAC audio

| Contender | Decode | Audio | Latency ms (median) | p5–p95 ms | Source FPS | Epochs/s | Peak RSS MiB | RSS growth MiB | Child RSS MiB | VRAM torch MiB | VRAM NVML Δ MiB | CPU cores | Setup+cold s | Video MAE | Mel MAE |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| **tenzorpipe-w1** | CPU (OpenH264) | Log-Mel | 5120.7 | 5006.5–5293.9 | 117 | 7.8 | 679 | 115 | 92 | 0 | 0 (no CUDA) | 1.14 | 6.27 | ref | ref |
| **tenzorpipe-w6** | CPU (OpenH264) | Log-Mel | 1483.9 | 1424.5–1546.7 | 405 | 27.0 | 892 | 327 | 304 | 0 | 0 (no CUDA) | 4.98 | 2.71 | 0.0000 | 0.000 |
| **tenzorpipe-auto** | CPU (OpenH264) | Log-Mel | 1073.2 | 1043.7–1101.6 | 558 | 37.2 | 1087 | 523 | 500 | 0 | 0 (no CUDA) | 8.90 | 2.36 | 0.0000 | 0.000 |
| dali-nvdec | GPU (NVDEC) | none (unsupported) | 442.2 | 440.6–444.2 | 1367 | 91.2 | 1074 | 0 | — | 46 | 823 | 0.41 | 2.17 | 0.0799 | — |
| torchcodec-cuda | GPU (NVDEC) | Log-Mel | 312.3 | 307.7–322.7 | 1912 | 127.5 | 1398 | 0 | — | 288 | 665 | 0.54 | 2.92 | 0.0047 | 0.014 |
| torchcodec-cpu | CPU (libavcodec) | Log-Mel | 2318.0 | 2223.8–2372.6 | 260 | 17.3 | 1041 | 285 | — | 0 | 0 (no CUDA) | 1.33 | 4.42 | 0.0070 | 0.014 |
| torchvision-read_video | *skipped:* ImportError: cannot import name 'read_video' from 'torchvision.io' (removed in torchvision 0.29) | | | | | | | | | | | | | | |
| ffmpeg-pipe | CPU (libavcodec) | Log-Mel | 592.0 | 572.0–624.0 | 1012 | 67.5 | 787 | 191 | 168 | 0 | 0 (no CUDA) | 10.90 | 1.72 | 0.0466 | 0.014 |

### Column definitions

- **Latency** — completion-to-completion time per clip in the measured loop (p5–p95 alongside).
- **Source FPS** / **Epochs/s** — measured-loop totals (iterations × frames ÷ loop wall), robust to DALI's bimodal async latencies. All decoders must decode every frame (P/B-frame dependencies) even though only one per 0.5 s is kept. **Epochs/s** counts output rows.
- **Peak RSS** — sampled every 5 ms over the measured loop: worker Python process + child processes (tenzor / ffmpeg). Includes each library's resident baseline (torch, DALI, CUDA context). **RSS growth** = peak minus RSS after imports/pipeline setup (the per-clip working set). **Child RSS** is the sampled engine subprocess alone (children's `ru_maxrss` is unusable: it keeps the forking parent's size across exec).
- **VRAM torch** — `torch.cuda.max_memory_allocated()` (PyTorch caching allocator only; DALI's and NVDEC's own pools are invisible to it). **VRAM NVML Δ** — peak device memory minus the idle reading before the worker imported anything; it captures CUDA context + NVDEC surfaces + DALI pools, but is device-wide, so Windows desktop activity adds noise (up to ~100 MiB observed), so CPU-only contenders, which never create a CUDA context, are shown as 0. WSL2 does not expose per-process NVML accounting.
- **CPU cores** — (user+sys CPU of worker and children) ÷ loop wall time.
- **Setup+cold** — imports, decoder/pipeline construction and the first (cold) iteration.
- **Video/Mel MAE** — mean absolute difference from `tenzorpipe-w1` tensors (video in [-1,1] units; Mel in log10 units). Small values confirm contenders did equivalent work; `bench/check_dali_alignment.py` confirms DALI returns exactly the expected frame for every epoch; differences come from colour-matrix rounding, resize sampling, and each library's 48→16 kHz resampler.

## TenzorPipe internals (for bottleneck analysis)

- `tenzorpipe-auto` / av: engine CLI 1065.6 ms + Arrow→torch load 2.9 ms (medians)
- `tenzorpipe-w1` / av: engine CLI 5114.2 ms + Arrow→torch load 2.9 ms (medians)
- `tenzorpipe-w6` / av: engine CLI 1476.3 ms + Arrow→torch load 2.8 ms (medians)
- `tenzorpipe-auto` / video: engine CLI 1023.2 ms + Arrow→torch load 2.7 ms (medians)
- `tenzorpipe-w1` / video: engine CLI 5014.8 ms + Arrow→torch load 2.8 ms (medians)
- `tenzorpipe-w6` / video: engine CLI 1460.2 ms + Arrow→torch load 2.9 ms (medians)

`--profile` from one warm run per variant — wall-clock stage timers overlap across threads, so they are not additive; `*_wait` rows are idle time.

| Stage (s) | tenzorpipe-auto / av | tenzorpipe-w1 / av | tenzorpipe-w6 / av | tenzorpipe-auto / video | tenzorpipe-w1 / video | tenzorpipe-w6 / video |
|---|---:|---:|---:|---:|---:|---:|
| video_decode | 9.035 | 4.941 | 6.744 | 9.130 | 4.962 | 6.949 |
| collector_receive_wait | 1.039 | 4.952 | 1.379 | 0.955 | 4.975 | 1.457 |
| worker_send_wait | 1.040 | 4.718 | 1.358 | 0.015 | 0.001 | 0.003 |
| video_reorder_wait | 0.981 | 0.000 | 1.358 | 0.953 | 0.000 | 1.462 |
| video_window_wait | 0.001 | 0.000 | 0.109 | 0.001 | 0.000 | 0.244 |
| audio_source | 0.084 | 0.076 | 0.081 | 0.000 | 0.000 | 0.000 |
| audio_source_wait | 0.049 | 0.006 | 0.024 | 0.000 | 0.000 | 0.000 |
| video_resize | 0.034 | 0.017 | 0.030 | 0.043 | 0.017 | 0.033 |
| audio_resample | 0.021 | 0.019 | 0.028 | 0.000 | 0.000 | 0.000 |
| video_parse | 0.021 | 0.010 | 0.017 | 0.023 | 0.013 | 0.018 |
| audio_mel | 0.014 | 0.012 | 0.018 | 0.000 | 0.000 | 0.000 |
| arrow_write | 0.009 | 0.014 | 0.010 | 0.010 | 0.013 | 0.010 |
| arrow_pack | 0.009 | 0.012 | 0.013 | 0.011 | 0.014 | 0.012 |
| setup | 0.008 | 0.008 | 0.013 | 0.005 | 0.003 | 0.003 |
| **wall** | **1.067** | **4.987** | **1.418** | **0.984** | **5.006** | **1.485** |

## Environment

```json
{
  "os": "Linux-6.6.87.2-microsoft-standard-WSL2-x86_64-with-glibc2.39",
  "python": "3.12.3",
  "cpu": "Intel(R) Core(TM) i5-14400F",
  "logical_cpus": 16,
  "gpu": "NVIDIA GeForce RTX 5060, 610.62, 8151 MiB",
  "ram_gib": 15.5,
  "torch": "2.14.0+cu130",
  "torchvision": "0.29.0+cu130",
  "torchcodec": "0.16.0+cu130",
  "nvidia.dali": "2.3.0",
  "numpy": "2.5.2",
  "pyarrow": "25.0.1",
  "ffmpeg": "ffmpeg version n8.1.2-53-g1005b294ff-20260916 Copyright (c) 2000-2026 the FFmpeg developers",
  "torch_cuda": "13.0",
  "tenzor_sha256": "c914ddbd5663c9837c501020e58f98b1cdb150708ae0b37fcbc5df8374858b77"
}
```

Raw per-iteration latencies: `bench/results/latest.json`. Reproduce inside WSL2/Linux:

```sh
bash bench/setup_wsl.sh && . ~/.tenzor-bench/env.sh
python bench/gpu_bench.py --iters 50 --warmup 5 --seconds 20
```

<!-- ANALYSIS -->
## Analysis — where TenzorPipe wins, where it loses, what to fix before release

*Written from the 2026-09-16 run above (i5-14400F, RTX 5060 8 GB, WSL2). The follow-up measurements in
this section come from `bench/bottleneck_probe.sh` on the same video-only clip. The script regenerates
the tables but keeps this section; update it by hand if the numbers move.*

### Headline

On raw ingestion speed of 1080p H.264, **TenzorPipe v0.2.0 is not yet competitive.** With every CPU
core it takes 1,032 ms per 20 s clip. That makes it **3.7× slower than TorchCodec-CUDA** (276 ms),
**2.3× slower than DALI** (442 ms) and **1.9× slower than a plain FFmpeg pipe on the same CPU** (542 ms).
At the CLI default (`--video-workers 1`) it takes 5,023 ms, which is slower than every contender,
including single-threaded TorchCodec-CPU (2,216 ms). TenzorPipe's real advantages are GPU memory,
host memory, determinism and re-read cost, not latency.

### Where TenzorPipe outperforms

| Advantage | Evidence |
|---|---|
| **Zero GPU memory** | DALI holds 822 MiB of device memory and TorchCodec-CUDA 650–665 MiB. On this 8 GB card, with 2.7 GB already used by the desktop, that is 12–15% of the free VRAM taken from model and batch. TenzorPipe uses none. |
| **Lowest total footprint (1 worker)** | 674–679 MiB peak for the whole process tree, the lowest of any contender, including FFmpeg (707–787). The engine itself peaks at **87–92 MiB** at 1080p. Per-clip RSS growth is 110–115 MiB, against 172–191 (FFmpeg) and 281–285 (TorchCodec-CPU). |
| **Re-read cost ≈ 3 ms** | Loading the finished `.tenzor` into PyTorch tensors takes a median **2.8 ms** per clip. Epochs 2…N of training pay 2.8 ms instead of re-decoding: 276 ms even for the fastest GPU path, about **100×**. This is the structural win, but it only holds when a dataset is read more than once, and competitors can add a cache too. |
| **Bit-exact determinism** | 6 and auto workers produce **identical tensors** to 1 worker (video and Mel MAE 0.0000). The other libraries disagree with each other by 0.005–0.08 (colour conversion and resize sampling). `check_dali_alignment.py` shows DALI's difference is in pixel values, not a frame offset. |
| **Audio + video in one pass** | DALI cannot decode AAC in MP4 at all. TenzorPipe's audio adds only 40–100 ms per clip (profile: AAC decode 76–84 ms, resample ~20 ms, Mel ~13 ms), comparable to TorchCodec-CUDA (+36 ms) and FFmpeg (+50 ms). |
| **Cold start** | With auto workers, setup plus the first clip takes 2.35 s: on par with DALI (2.2 s), better than TorchCodec-CUDA (2.6–2.9 s) and TorchCodec-CPU (4.3 s). FFmpeg is fastest (1.7–1.8 s). |

### Bottleneck 1 — the H.264 decoder is ~99% of wall time

`--profile` with 1 worker shows `video_decode` taking 4.96 s of 5.01 s. Everything else is small:
resize 17–43 ms, audio ~130 ms in total, Arrow pack + write ~25 ms, and loading into PyTorch 2.8 ms.
**Do not spend pre-release time on resize, Mel, Arrow or the loader: together they are under 5%.**

The decoder itself is slow per core:

| Decoder, 600 frames 1080p High profile, 1 thread | Wall | Frames/s |
|---|---:|---:|
| OpenH264 (TenzorPipe `--video-workers 1`) | 5.05 s | 119 |
| libavcodec (`ffmpeg -threads 1 -f null`) | 2.58 s | 233 |
| libavcodec, non-reference frames skipped | 1.55 s | 387 |

So OpenH264 decodes about **2× slower per core** than libavcodec on this CABAC/B-frame stream. That
gap is why FFmpeg on the same cores (1,095 fps at 9.0 cores) beats TenzorPipe auto (580 fps at 9.2
cores).

### Bottleneck 2 — decoding frames nobody uses

Only 40 of the 600 frames become output rows. Of the 600, **281 (47%) are non-reference B-frames**,
which no other frame depends on (`ffprobe -skip_frame noref` leaves 319). libavcodec decodes the clip
**40% faster** when it skips them. TenzorPipe decodes every access unit.

**Fix (highest value, no new dependency):** before feeding an access unit to OpenH264, check whether
all its VCL NAL units have `nal_ref_idc == 0`. If so, and the epoch selection (already computed from
container timing) does not pick that picture, skip it. The expected result is roughly 1.4–1.6× at
every worker count. That estimate comes from the libavcodec measurement and has not been measured in
TenzorPipe. It must pass the existing exact-identity matrix, because skipped pictures change what the
PTS reorder queue sees.

### Bottleneck 3 — parallelism is capped by IDR chunk count

Worker sweep (3 runs each; the clip has 2 s GOPs, so exactly 10 chunks):

| Workers | 1 | 2 | 4 | 6 | 8 | 10 | 12 | 14 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Wall s | 5.06 | 2.95 | 1.90 | 1.42 | 1.47 | 1.03 | 1.02 | 1.02 |
| Engine peak RSS MiB | 86 | 138 | 225 | 299 | 398 | 495 | 495 | 496 |
| Effective workers | 1 | 2 | 4 | 6 | 8 | 10 | 10 | 10 |

- Wall time follows **⌈chunks ÷ workers⌉ rounds**: 6 and 8 workers both need 2 rounds (1.42 vs
  1.47 s), and anything from 10 up needs 1. Workers beyond the chunk count are clamped to 10, so the
  auto setting's "cores − 2" (14) is really 10 here.
- **This matters most for training data:** a 10 s clip with 2 s GOPs has only 5 chunks, so it can
  never use more than 5 decoders. For datasets of short clips, running several files at once is the
  lever that scales. Intra-file chunking cannot.
- Per-frame efficiency drops as workers are added. Summed decode time is 4.96 s with 1 worker, 6.95 s
  with 6 and 9.13 s with 10, so 10 workers achieve a 4.9× speedup (49% efficiency). The i5-14400F has
  6 P-cores and 4 E-cores with 16 threads, and each round waits for its slowest chunk, which can land on an E-core or a
  sibling hyper-thread (plausible but not isolated in this run).

**Fixes:** (a) cut chunks so their count is a multiple of the worker count, and balance chunk sizes
across P- and E-cores; (b) document or provide multi-file concurrency for clip datasets; (c) have
auto pick min(chunks, physical cores, memory budget), not cores − 2.

### Bottleneck 4 — about 48 MiB of RAM per worker at 1080p

Engine RSS grows by about **48 MiB per extra worker** (86 MiB → 495 MiB for 10). The v0.2.0 EPYC
report measured about 11 MiB per worker at 360p, so this scales with frame area: it is OpenH264
picture buffers. `MALLOC_ARENA_MAX=2` changed nothing (503 → 507 MiB with auto workers), which rules
out glibc per-thread arenas. With auto workers at 1080p the engine uses 3× the memory of the FFmpeg
pipe (497 vs 167 MiB).

**Fixes:** size the decoded picture buffer from the SPS (`max_num_ref_frames`, level) rather than the
maximum, and make auto respect a RAM budget. `--video-buffer-mib` only bounds output tensors, not
decoder state.

### Bottleneck 5 — the CLI default

At 1080p, `--video-workers 1` is **3.4× slower than 6 workers and 4.9× slower than auto** on this
machine. The conservative default made sense at 360p, but it is the headline number most users will
see.

**Fix:** choose the default from resolution, chunk count and a memory budget, or at least print a
hint when a single decoder runs on a multi-core host.

### Larger strategic options (these need a policy decision)

- **Optional NVDEC backend** (feature-gated, CPU path stays default): the GPU contenders here reach
  1,364–2,173 fps, 2.3–3.7× TenzorPipe's best, at 650–820 MiB of VRAM and under 0.5 CPU cores. It
  would give up the zero-VRAM advantage for users who enable it.
- **libavcodec's H.264 decoder** is 2× faster per core, but it is LGPL/GPL.
  `DEPENDENCY_POLICY.md` and the "no FFmpeg" design goal currently rule it out.
- First confirm the release binary's OpenH264 was built with its assembly routines enabled.
  `BUILD_STATUS.md` says NASM was present, but this benchmark did not check the binary itself.

### Caveats

- **One synthetic clip:** `testsrc2` + temporal noise, ~8 Mb/s, High profile, 3 B-frames, 2 s GOP,
  on one machine under WSL2. Real footage, other GOP structures and native Linux will shift the
  absolute numbers. The decoder ratios and scaling pattern should hold.
- **DALI's setup:** `fn.readers.video` indexes the file once at pipeline build, and that cost is in
  "Setup+cold", not in per-clip latency. For datasets of many files, DALI's per-file cost would be
  somewhat higher than shown.
- **Where tensors land:** GPU contenders leave tensors on the GPU, CPU contenders in host memory. A
  CPU path feeding GPU training still pays a host-to-device copy (about 24 MiB per clip here),
  which is not timed.
- **VRAM readings:** NVML Δ is device-wide, because WSL2 has no per-process accounting.
- **RSS sampling:** RSS is sampled every 5 ms and can miss very short peaks; the sampled engine peaks
  agree with `/usr/bin/time` within 2%.
- **`torchvision.io.read_video`** was removed in torchvision 0.29, so it could not be measured.

