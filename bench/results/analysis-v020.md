# Archived analysis: v0.2.0 release binary (2026-09-16, before the skip/default changes)

The tables this analysis refers to are in git history (the first BENCHMARKS.md commit) and
`bench/results/latest.json` of that run. Current results: BENCHMARKS.md.

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
report measured about 14 MiB per worker at 360p (N1 61 MiB → N8 159 MiB), so this scales with frame area: it is OpenH264
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

