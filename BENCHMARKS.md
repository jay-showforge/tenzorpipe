# BENCHMARKS — TenzorPipe vs DALI vs TorchCodec vs FFmpeg (1080p ingestion)

Generated 2026-09-16 21:49 by `bench/gpu_bench.py` — 5 warm-up + **50 measured iterations** per contender, each contender in a fresh process.

## Task (identical for every contender)

1080p30 H.264 MP4 → source frame nearest each 0.5 s epoch start → 224×224 nearest-neighbour (aligned corners) → float32 RGB CHW in [-1, 1]. In the **A/V** scenario, contenders that can decode AAC also produce the 64-band Log-Mel (16 kHz mono, 400-sample Hann, 160 hop, 50 rows/epoch). An iteration ends when every output tensor has been checksummed (forces CUDA sync). TenzorPipe's iteration = run the CLI to `/dev/shm` **and** load the Arrow file into PyTorch tensors.

| Clip | Resolution | Frames | Bitrate | Profile | Epochs |
|---|---|---:|---:|---|---:|
| `synthetic-1080p30-h264-20s-video-only.mp4` (video) | 1920×1080 @ 30/1 | 600 | 7940 kb/s | High | 40 |
| `synthetic-1080p30-h264-aac-20s.mp4` (av) | 1920×1080 @ 30/1 | 600 | 8138 kb/s | High | 40 |

## Scenario 1 — video only (all contenders)

| Contender | Decode | Audio | Latency ms (median) | p5–p95 ms | Source FPS | Epochs/s | Peak RSS MiB | RSS growth MiB | Child RSS MiB | VRAM torch MiB | VRAM NVML Δ MiB | CPU cores | Setup+cold s | Video MAE | Mel MAE |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| **tenzorpipe-default** | CPU (OpenH264) | n/a | 645.5 | 624.2–665.4 | 931 | 62.1 | 1098 | 520 | 497 | 0 | 0 (no CUDA) | 8.74 | 3.27 | 0.0000 | — |
| **tenzorpipe-w1** | CPU (OpenH264) | n/a | 2990.6 | 2950.0–3136.1 | 199 | 13.3 | 668 | 110 | 87 | 0 | 0 (no CUDA) | 1.13 | 4.22 | ref | — |
| **tenzorpipe-w6** | CPU (OpenH264) | n/a | 888.2 | 857.4–934.3 | 674 | 44.9 | 881 | 323 | 300 | 0 | 0 (no CUDA) | 4.83 | 2.14 | 0.0000 | — |
| **tenzorpipe-auto** | CPU (OpenH264) | n/a | 631.0 | 611.1–653.9 | 950 | 63.3 | 1078 | 520 | 497 | 0 | 0 (no CUDA) | 8.75 | 1.86 | 0.0000 | — |
| **tenzorpipe-v020-auto** | CPU (OpenH264) | n/a | 1030.9 | 1015.6–1060.2 | 580 | 38.7 | 1077 | 520 | 497 | 0 | 0 (no CUDA) | 9.15 | 2.33 | 0.0000 | — |
| dali-nvdec | GPU (NVDEC) | n/a | 441.7 | 440.2–445.6 | 1364 | 90.9 | 1074 | 0 | — | 46 | 822 | 0.42 | 2.23 | 0.0799 | — |
| torchcodec-cuda | GPU (NVDEC) | n/a | 276.0 | 267.7–286.7 | 2173 | 144.8 | 1128 | 0 | — | 256 | 650 | 0.47 | 2.63 | 0.0047 | — |
| torchcodec-cpu | CPU (libavcodec) | n/a | 2216.3 | 2190.1–2261.6 | 270 | 18.0 | 1005 | 281 | — | 0 | 0 (no CUDA) | 1.31 | 4.30 | 0.0070 | — |
| torchvision-read_video | *skipped:* ImportError: cannot import name 'read_video' from 'torchvision.io' | | | | | | | | | | | | | | |
| ffmpeg-pipe | CPU (libavcodec) | n/a | 554.3 | 528.8–581.1 | 1087 | 72.5 | 701 | 172 | 167 | 0 | 0 (no CUDA) | 8.92 | 1.60 | 0.0466 | — |

## Scenario 2 — video + AAC audio

| Contender | Decode | Audio | Latency ms (median) | p5–p95 ms | Source FPS | Epochs/s | Peak RSS MiB | RSS growth MiB | Child RSS MiB | VRAM torch MiB | VRAM NVML Δ MiB | CPU cores | Setup+cold s | Video MAE | Mel MAE |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| **tenzorpipe-default** | CPU (OpenH264) | Log-Mel | 708.8 | 689.0–734.9 | 843 | 56.2 | 1081 | 523 | 500 | 0 | 0 (no CUDA) | 8.37 | 2.02 | 0.0000 | 0.000 |
| **tenzorpipe-w1** | CPU (OpenH264) | Log-Mel | 3010.0 | 2962.2–3152.5 | 197 | 13.1 | 674 | 115 | 92 | 0 | 0 (no CUDA) | 1.17 | 4.28 | ref | ref |
| **tenzorpipe-w6** | CPU (OpenH264) | Log-Mel | 884.8 | 846.0–936.7 | 675 | 45.0 | 885 | 327 | 303 | 0 | 0 (no CUDA) | 4.98 | 2.10 | 0.0000 | 0.000 |
| **tenzorpipe-auto** | CPU (OpenH264) | Log-Mel | 683.5 | 660.3–715.7 | 877 | 58.4 | 1081 | 523 | 499 | 0 | 0 (no CUDA) | 8.30 | 1.89 | 0.0000 | 0.000 |
| **tenzorpipe-v020-auto** | CPU (OpenH264) | Log-Mel | 1086.7 | 1052.4–1121.4 | 552 | 36.8 | 1081 | 523 | 500 | 0 | 0 (no CUDA) | 8.89 | 2.28 | 0.0000 | 0.000 |
| dali-nvdec | GPU (NVDEC) | none (unsupported) | 442.2 | 440.6–444.2 | 1367 | 91.2 | 1074 | 0 | — | 46 | 823 | 0.41 | 2.17 | 0.0799 | — |
| torchcodec-cuda | GPU (NVDEC) | Log-Mel | 312.3 | 307.7–322.7 | 1912 | 127.5 | 1398 | 0 | — | 288 | 665 | 0.54 | 2.92 | 0.0047 | 0.014 |
| torchcodec-cpu | CPU (libavcodec) | Log-Mel | 2318.0 | 2223.8–2372.6 | 260 | 17.3 | 1041 | 285 | — | 0 | 0 (no CUDA) | 1.33 | 4.42 | 0.0070 | 0.014 |
| torchvision-read_video | *skipped:* ImportError: cannot import name 'read_video' from 'torchvision.io' | | | | | | | | | | | | | | |
| ffmpeg-pipe | CPU (libavcodec) | Log-Mel | 631.5 | 600.0–648.1 | 954 | 63.6 | 782 | 191 | 168 | 0 | 0 (no CUDA) | 10.40 | 1.81 | 0.0466 | 0.014 |

### Column definitions

- **Latency** — completion-to-completion time per clip in the measured loop (p5–p95 alongside).
- **Source FPS** / **Epochs/s** — measured-loop totals (iterations × frames ÷ loop wall), robust to DALI's bimodal async latencies. All decoders must decode every frame (P/B-frame dependencies) even though only one per 0.5 s is kept. **Epochs/s** counts output rows.
- **Peak RSS** — sampled every 5 ms over the measured loop: worker Python process + child processes (tenzor / ffmpeg). Includes each library's resident baseline (torch, DALI, CUDA context). **RSS growth** = peak minus RSS after imports/pipeline setup (the per-clip working set). **Child RSS** is the sampled engine subprocess alone (children's `ru_maxrss` is unusable: it keeps the forking parent's size across exec).
- **VRAM torch** — `torch.cuda.max_memory_allocated()` (PyTorch caching allocator only; DALI's and NVDEC's own pools are invisible to it). **VRAM NVML Δ** — peak device memory minus the idle reading before the worker imported anything; it captures CUDA context + NVDEC surfaces + DALI pools, but is device-wide, so Windows desktop activity adds noise (up to ~100 MiB observed), so CPU-only contenders, which never create a CUDA context, are shown as 0. WSL2 does not expose per-process NVML accounting.
- **CPU cores** — (user+sys CPU of worker and children) ÷ loop wall time.
- **Setup+cold** — imports, decoder/pipeline construction and the first (cold) iteration.
- **Video/Mel MAE** — mean absolute difference from `tenzorpipe-w1` tensors (video in [-1,1] units; Mel in log10 units). Small values confirm contenders did equivalent work; `bench/check_dali_alignment.py` confirms DALI returns exactly the expected frame for every epoch; differences come from colour-matrix rounding, resize sampling, and each library's 48→16 kHz resampler.

## TenzorPipe internals (for bottleneck analysis)

- `tenzorpipe-auto` / av: engine CLI 675.6 ms + Arrow→torch load 2.9 ms (medians)
- `tenzorpipe-default` / av: engine CLI 701.0 ms + Arrow→torch load 2.8 ms (medians)
- `tenzorpipe-v020-auto` / av: engine CLI 1079.5 ms + Arrow→torch load 2.9 ms (medians)
- `tenzorpipe-w1` / av: engine CLI 3001.7 ms + Arrow→torch load 2.9 ms (medians)
- `tenzorpipe-w6` / av: engine CLI 877.3 ms + Arrow→torch load 2.8 ms (medians)
- `tenzorpipe-auto` / video: engine CLI 623.7 ms + Arrow→torch load 2.8 ms (medians)
- `tenzorpipe-default` / video: engine CLI 637.1 ms + Arrow→torch load 2.8 ms (medians)
- `tenzorpipe-v020-auto` / video: engine CLI 1023.1 ms + Arrow→torch load 2.8 ms (medians)
- `tenzorpipe-w1` / video: engine CLI 2983.0 ms + Arrow→torch load 2.8 ms (medians)
- `tenzorpipe-w6` / video: engine CLI 880.4 ms + Arrow→torch load 2.7 ms (medians)

`--profile` from one warm run per variant — wall-clock stage timers overlap across threads, so they are not additive; `*_wait` rows are idle time.

| Stage (s) | tenzorpipe-auto / av | tenzorpipe-default / av | tenzorpipe-v020-auto / av | tenzorpipe-w1 / av | tenzorpipe-w6 / av | tenzorpipe-auto / video | tenzorpipe-default / video | tenzorpipe-v020-auto / video | tenzorpipe-w1 / video | tenzorpipe-w6 / video |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| video_decode | 5.477 | 5.540 | 9.121 | 2.936 | 3.883 | 5.300 | 5.379 | 9.062 | 2.890 | 3.881 |
| collector_receive_wait | 0.655 | 0.646 | 1.013 | 2.953 | 0.825 | 0.568 | 0.562 | 0.973 | 2.905 | 0.799 |
| worker_send_wait | 0.652 | 0.648 | 1.025 | 2.816 | 0.806 | 0.019 | 0.014 | 0.013 | 0.001 | 0.013 |
| video_reorder_wait | 0.595 | 0.588 | 0.954 | 0.000 | 0.796 | 0.562 | 0.557 | 0.972 | 0.000 | 0.793 |
| video_window_wait | 0.000 | 0.000 | 0.001 | 0.000 | 0.202 | 0.001 | 0.000 | 0.001 | 0.000 | 0.021 |
| audio_source | 0.100 | 0.084 | 0.084 | 0.080 | 0.085 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 |
| audio_source_wait | 0.049 | 0.044 | 0.047 | 0.010 | 0.035 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 |
| video_resize | 0.042 | 0.040 | 0.037 | 0.017 | 0.028 | 0.042 | 0.044 | 0.040 | 0.017 | 0.031 |
| audio_resample | 0.028 | 0.021 | 0.022 | 0.020 | 0.024 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 |
| video_parse | 0.018 | 0.017 | 0.023 | 0.007 | 0.011 | 0.016 | 0.018 | 0.023 | 0.009 | 0.012 |
| audio_mel | 0.017 | 0.015 | 0.014 | 0.013 | 0.016 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 |
| setup | 0.015 | 0.008 | 0.006 | 0.012 | 0.009 | 0.003 | 0.004 | 0.003 | 0.011 | 0.004 |
| arrow_write | 0.012 | 0.009 | 0.014 | 0.010 | 0.010 | 0.012 | 0.009 | 0.009 | 0.013 | 0.010 |
| arrow_pack | 0.012 | 0.010 | 0.011 | 0.013 | 0.012 | 0.012 | 0.010 | 0.011 | 0.013 | 0.011 |
| **wall** | **0.696** | **0.675** | **1.046** | **2.991** | **0.859** | **0.597** | **0.588** | **0.998** | **2.944** | **0.826** |

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
  "tenzor_bin": "/mnt/c/Users/ftmon/TenzorPipe/tenzorpipe-v0.2.0/target/release/tenzor",
  "tenzor_sha256": "451c1f5d14b608ccdb93eac5ebba9ab1884d6ab2f548ce207ad6c686d81ecc39",
  "release_sha256": "c914ddbd5663c9837c501020e58f98b1cdb150708ae0b37fcbc5df8374858b77"
}
```

Raw per-iteration latencies: `bench/results/latest.json`. Reproduce inside WSL2/Linux:

```sh
bash bench/setup_wsl.sh && . ~/.tenzor-bench/env.sh
cargo build --release --locked && mkdir -p target/release  # CARGO_TARGET_DIR may differ
python bench/gpu_bench.py --iters 50 --warmup 5 --seconds 20 --tenzor-bin target/release/tenzor
```

<!-- ANALYSIS -->

## Analysis — v0.3.0 (skip non-reference pictures, AVX2 build, auto default)

*Written 2026-09-16 from the tables above. The `tenzorpipe-default` and `ffmpeg-pipe` rows are the
final v0.3.0 binary (after the version bump and library split) and FFmpeg, re-measured together
in a later session. Rows named `tenzorpipe-w1/-w6/-auto` are the v0.3.0 engine built from this
tree before the version bump (it still embedded "0.2.0"; the
engine code is unchanged by the bump and later packaging, which were re-verified). `tenzorpipe-v020-auto` is the untouched
v0.2.0 release binary, run in that same pre-bump session.
The DALI and TorchCodec rows come from the earlier v0.2.0 run on the same machine and clips; nothing
they depend on changed. The analysis of the v0.2.0 release itself is archived in
`bench/results/analysis-v020.md`.*

### Headline

| Video-only, 1080p 20 s clip | v0.2.0 | v0.3.0 | Speedup |
|---|---:|---:|---:|
| CLI default, no flags | 5,023 ms (1 worker) | **625 ms** (auto)² | **8.0×** |
| `--video-workers 1` | 5,023 ms | 2,991 ms | 1.68× |
| `--video-workers 6` | 1,469 ms | 888 ms | 1.65× |
| `--video-workers 0` (auto) | 1,031 ms¹ | 631 ms | 1.63× |

¹ Same-session release row. ² Pre-bump run; the final 0.3.0 binary measured **646 ms** in a later
session where FFmpeg also slowed from 531 ms to 554 ms (ratio 1.18× → 1.16×), i.e. host drift,
not a regression. A same-session interleaved A/B of the final binary against v0.2.0 is in
`evidence/v0.3.0/ab-final-binary.txt` (5.16 s → 0.66 s, 7.9×).

**Against FFmpeg, the default is now 1.16–1.18× slower, down from 1.94×:** 625 vs 531 ms in the
pre-bump session, 646 vs 554 ms for the final binary (tables above). With audio, the final binary
takes 709 ms to FFmpeg's 632 ms (1.12×). That puts TenzorPipe 1.41× behind DALI
(442 ms) and 2.26× behind TorchCodec-CUDA (276 ms), still with zero VRAM. Every output tensor stayed
byte-identical to v0.2.0.

### Where the speedup came from (interleaved A/B, 5 runs each, `evidence/v0.3.0/ab-attribution.txt`)

| Build | 1 worker | auto workers | Output |
|---|---:|---:|---|
| v0.2.0 release | 5.34 s | 1.02 s | reference |
| C-only OpenH264 (`OPENH264_NO_ASM=1`), no skipping | 6.59 s (0.81×) | 1.18 s (0.86×) | identical |
| + NASM with `HAVE_AVX2`, no skipping | 5.28 s (1.01×) | 1.03 s (0.99×) | identical |
| + skip unselected non-reference pictures | **3.14 s (1.70×)** | **0.63 s (1.63×)** | identical |

- **Skipping frames is the entire speedup.** 265 of 600 access units are never decoded: the clip's 281
  non-reference B-frames minus the 16 that epochs select.
- **SIMD was already on in the release.** A C-only build is 19% slower, so the v0.2.0 binary did use
  its NASM SSE2/SSSE3/SSE4.1 routines.
- **AVX2 was compiled out, and enabling it changes nothing measurable.** `openh264-sys2` never defined
  `HAVE_AVX2`, which the vendored build patch now fixes. But OpenH264's AVX2 code covers only the
  residual IDCT and luma interpolation. The High-profile hot path is scalar CABAC entropy decoding,
  which has no SIMD version at all. The patch is kept because it is free, byte-identical, and makes a
  missing NASM fail the build instead of silently producing a 19% slower binary.
- **The new default is where the 8× comes from.** Auto workers alone give 5×, and skipping gives the
  rest.

### Correctness gates (all on the candidate binary)

| Gate | Result |
|---|---|
| `cargo fmt --check`, `cargo clippy -D warnings`, `cargo deny check licenses` | pass |
| `cargo test --all-targets` | 32 pass (30 existing + disposable-NAL classifier + streaming-oracle equivalence) |
| **`scripts/test_skip_identity.py`**: new vs v0.2.0 release binary, byte for byte | **1,056 cases, 0 failures**: 936 identical artifacts (396 of them with skipping active, 93,716 access units skipped) and 120 identical errors |
| `test_matrix.py` (default and 4 workers) | 16 media + 12 failure cases + PyTorch loader, both pass |
| `test_epoch_selection.py` vs v0.1.6 (1 and 4 workers) | 41/41 exact-value, both pass |
| `test_concurrency.py`, `test_chunk_boundaries.py` vs v0.1.6, `test_audio_write_failure.py` | 38, 30 and 12 checks pass |
| `test_audio_identity.py` vs v0.1.9 (default variant is now auto) | 396 cases: 336 identical artifacts + 60 error agreements |
| `fuzz_chunked_corruption.py` (gate updated, see below) | 24/24 pass |

The identity media: every committed fixture, plus open-GOP, 23.976 fps, VFR, audio-tail, 330 s and
1080p clips, plus x264 `b-pyramid` none/normal/strict, `bf=16` and `refs=1` streams
(`scripts/gen_skip_fixtures.sh`). The project gates ran on a scratch copy of the repository
(`scripts/run_regression_copy.sh`), because several of them regenerate committed fixtures.

**One behaviour change, found by the corruption fuzz:** in 2 of 24 corrupted files, every damaged byte
sat inside a skipped non-reference picture. v0.2.0 decoded that picture and failed. The candidate never
decodes it, so it succeeds, and its output is **byte-identical to the output from the uncorrupted
file**, so no damaged data reaches a tensor. With `--no-skip-nonref` all 24 fail exactly as before.
The fuzz gate now enforces exactly that: strict mode must reject every corruption, and default mode
must either fail or match the clean output.

### Costs of the new default

- **RAM:** the default-run engine now peaks at **496 MiB** on 1080p, against 87 MiB for the old
  1-worker default: about 48 MiB per decoder, 10 decoders here. `--video-workers 1` still peaks at
  87 MiB and is itself 1.68× faster than before.
- **CPU:** 8.8 cores for 957 fps (109 fps/core), against FFmpeg's 9.0 cores for 1,129 fps
  (125 fps/core). One worker is the most CPU-efficient setting: 176 fps/core, up from 107.
- **Many processes at once:** a pipeline that starts several `tenzor` processes without a flag now
  oversubscribes the CPU. Pass `--video-workers` explicitly or use `python/tenzor_batch.py`.
- **Cold start:** setup plus the first clip drops from 6.2 s (old default) to 1.85 s, close to FFmpeg's
  1.8 s.

### What still separates TenzorPipe from FFmpeg (94 ms)

1. **Per-core decoder speed, still about 1.9×.** With non-reference skipping on both sides, one thread:
   libavcodec takes 1.55 s and OpenH264 inside TenzorPipe takes 2.99 s. With 1 worker, `video_decode`
   is 2.89 s of 2.94 s wall; resize, audio and Arrow together are under 5%. The gap is OpenH264's
   CABAC and reconstruction, and SIMD flags cannot close it (see the AVX2 row above).
2. **Parallel efficiency is about 48%.** Ten decoders give 4.8× over one: summed `video_decode` is
   5.30 s against 2.89 s. The 10 chunks run in a single round, so wall time is set by the slowest chunk
   on a 6 P-core + 4 E-core CPU with hyper-threads.
3. **Frames decoded after the last selected picture.** Within each chunk, access units decoded after
   the last selected picture (and its reorder window) cannot affect the output. With a 2 s GOP and
   0.5 s epochs that is roughly the last quarter of each GOP, now mostly P-frames because the B-frames
   are already skipped. Flushing each chunk's decoder once its selected pictures are out would skip
   them. **This is the next candidate, and its gain is unmeasured; an estimate of 15–25% of decode
   time is a guess.** It needs the same identity gate, because flushing early changes when OpenH264
   emits pictures.
4. **For datasets of short clips, run files in parallel.** On 16 × 10 s 360p clips, 16 files × 1
   worker took 0.41 s against 1.95 s sequential. See `docs/FILE_BATCHING.md`.

### Caveats

- **Synthetic content:** one generated 1080p clip (testsrc2 + noise, ~8 Mb/s, x264 High, 3 B-frames,
  2 s GOP) on one machine under WSL2. The share of non-reference pictures depends on the encoder:
  streams without B-frames (Baseline, most phone recordings) gain nothing from skipping, while
  B-pyramid-free encodes gain more.
- **Carried-over rows:** the DALI and TorchCodec rows are from an earlier session. Same-session release
  and FFmpeg rows reproduced their earlier medians within 3% (release 0.1%, FFmpeg 2.2% video / 2.8% A/V).
- **Version string:** these measurements and the first identity gate ran before the 0.3.0 bump. The
  identity scripts now substitute the version string read from Cargo.toml.
