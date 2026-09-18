# Changelog

## 0.3.1 — 2026-09-18

- **Cross-architecture behaviour is now measured rather than assumed.** Video tensors are
  bit-identical on x86-64 and aarch64 and are gated at zero difference. Log-Mel audio is not:
  RustFFT selects AVX2 on one and NEON on the other, and across the 21 cases the coefficients
  differ by a median of 2e-4 log10 units, 1.4e-2 at the 99.9th percentile and 2.8e-2 at worst.
  `scripts/test_arch_tolerance.py` reports that distribution and bounds it; binary determinism
  is now a per-architecture guarantee, verified 21/21 on x86-64 and 21/21 on ARM64 hardware.
  The earlier claim that byte-identity spanned architectures was never exercised on ARM.
- Fixed two release gates that could not pass as written: the NEON symbol check inspected a
  stripped binary, and `hevc-av.mp4` re-encoded its audio with FFmpeg's native AAC encoder,
  making recorded digests depend on the runner's FFmpeg build.

- **ARM64 (aarch64) support, verified.** The engine builds on ARM with no source changes:
  H.264 uses the vendored OpenH264's ARM64 NEON assembly (NASM is x86-only), and the H.265
  decoder, its kernels and RustFFT all have NEON paths. New gates: `scripts/test_arch_identity.py`
  checks this machine's tensors against digests recorded for its own architecture
  (`evidence/arch-digests-<machine>.json`, 21 cases across H.264, H.265, WAV and AAC), and
  `scripts/check_arm64_openh264.sh` cross-compiles the vendored decoder for aarch64 and
  decodes under emulation from an x86-64 host, requiring pictures identical to the host
  build's. A new `arm64` CI workflow runs the full suite, the FFmpeg oracle, the identity
  gate and an aarch64 wheel build on native ARM64 runners; the x86-64 workflow now runs the
  identity, H.265 and audio-tail gates too.
- `--profile` JSON records `"arch"`, so benchmark evidence says which machine produced it.
- `scripts/build_wheel.sh` accepts `WHEEL_TARGET` for cross-architecture wheels.

- **H.265 / HEVC support.** MP4 files with an H.265 video track convert like H.264 ones:
  same epoch selection, nearest-picture rule, resize and colour conversion. The decoder is
  `rusty_h265` 0.6.0 (Apache-2.0, pure Rust, no FFI, `forbid(unsafe_code)` outside its SIMD
  kernels), so the engine still links no FFmpeg and no GPL/LGPL code. Scope: Main profile,
  8-bit 4:2:0; Main 10, Main Intra/Still Picture, the range extensions, interlaced content
  and 4:2:2/4:4:4 are refused up front with the `ffmpeg` command that converts them.
  Video tensors match an independent FFmpeg decode exactly (max |delta| 2.4e-7 across the
  fixtures). H.264 output is unchanged: 576 byte-identical artifacts in the 864-case gate.
- H.265 uses the same worker pool, chunk budget and skip policy as H.264. Chunks start at
  IRAP access units: a chunk decodes through the next chunk's IRAP and the RASL pictures
  that follow it, so open-GOP encodes (x265's default, where the only IDR is frame 0) also
  parallelise. Access units whose slices are all sub-layer non-reference at the stream's
  highest `TemporalId`, and that no epoch selects, are never decoded.
- Measured on a 2-core sandbox with a 330-second 640x360 clip: H.265 7.01 s at one worker,
  5.30 s at two, 5.15 s at four; 8.33 s with `--no-skip-nonref` at two workers (skipping
  removes 5,939 of 9,900 access units). The same clip in H.264 takes 4.88 s at two workers,
  so H.265 costs about 8% more end to end here. A 22-minute H.265 clip converts in 21.4 s at
  119 MiB peak RSS.
- H.265 memory is bounded by the same budgets as H.264: converted tensors are handed to the
  collector as they are produced rather than buffered per decode pass (a 330-second clip at
  one worker went from 423 MiB to 58 MiB), and peak RSS no longer grows with clip length.
- New: `scripts/gen_hevc_fixtures.sh` and `scripts/test_hevc_fidelity.py` (FFmpeg-oracle
  comparison plus the expected refusals), both wired into `scripts/release_gates.sh`.
- Accept an audio track that ends slightly before the duration its container declares:
  the gap is padded with silence and reported once on stderr, and its frames are not
  counted as valid audio. `--audio-tail-tolerance-ms` (default 25 ms, `0` restores the
  strict check) bounds what is accepted; longer gaps are still an error. Container
  durations are rounded and editors trim tails, so sub-millisecond shortfalls are normal
  in real files and previously rejected them outright.
- Unsupported inputs now name what was found and how to convert it: video codec
  (for example "video track is H.265/HEVC"), audio codec, HE-AAC, and unsupported
  file extensions all include the matching `ffmpeg` command.
- `tenzorpipe.ingest(audio_tail_tolerance_ms=...)` exposes the same option.
- New: `scripts/gen_short_tail_fixtures.py` (derives short-tail fixtures from an existing
  fixture, no FFmpeg needed) and `scripts/test_audio_tail_tolerance.py` (end-to-end policy
  and message checks, plus an optional byte-identity pass against a previous binary).
- Output for files that already converted is unchanged: 145 fixture/setting cases are
  byte-identical to the 0.3.0 binary, with differences only in the two message texts above.

## 0.3.0 — 2026-09-16

- License the project under the Business Source License 1.1 (LICENSE): Additional Use Grant
  for production use below US$100,000 annual gross revenue, aggregated across parents,
  subsidiaries and affiliates under common control; larger organizations and embedded
  hardware/OEM production use need a commercial license (licensing@tenzorpipe.org); Change
  Date 2030-09-16; Change License Apache-2.0.
- `--quiet`/`-q` suppresses engine diagnostics on stderr; `--profile` JSON is returned to the
  caller instead of printed by the engine. `tenzorpipe.ingest()` is silent unless
  `verbose=True` and returns stage timers as `info["profile"]` when `profile=True`.
- docs/PACKAGING.md and `COMPATIBILITY=manylinux_2_28|manylinux_2_17 scripts/build_wheel.sh`
  for wheels that install on older glibc (zig cross-linking; outputs verified byte-identical
  with `scripts/check_wheel_identity.py`).
- Skip decoding access units whose slices are all non-reference (`nal_ref_idc == 0`) and that no
  epoch selects, in both the chunked and single-decoder paths. Output is byte-identical to 0.2.0
  (1,056-case gate against the release binary). 1080p benchmark: 1 worker 5.02 s → 2.99 s, auto
  1.03 s → 0.63 s. Logs report `skipped_nonref=N`; `--no-skip-nonref` restores full decoding.
  Corrupt slice data inside a skipped picture is no longer detected (the output equals the clean input's).
- `--video-workers` now defaults to auto: `available_parallelism()`, clamped to 1–16 and capped at the
  clip's IDR chunk count (previously 1; `0` previously meant cores − 2). Default 1080p run
  5.02 s → 0.63 s at ~500 MiB engine RSS (was 87 MiB). `--decoder-threads` runs still default to 1.
- Vendor `openh264-sys2` 0.9.8 with a build-script patch: define `HAVE_AVX2` for NASM and C++ (upstream
  makefile parity), and fail the build when NASM fails instead of silently building C-only OpenH264
  (19% slower). AVX2 itself measured no speedup on High-profile decode.
- Add `scripts/test_skip_identity.py`, `scripts/gen_skip_fixtures.sh`, `scripts/run_regression_copy.sh`;
  the chunked corruption fuzz now checks both strict (`--no-skip-nonref`) and default semantics.
- Add the competitor benchmark suite (`bench/`) with results in BENCHMARKS.md, and a file-level
  batching scaffold (`python/tenzor_batch.py`, docs/FILE_BATCHING.md).
- Python package `tenzorpipe` (PyO3 0.29 + maturin, abi3 wheel for CPython 3.9+):
  `tenzorpipe.ingest()` runs the engine in-process with the GIL released, `tenzorpipe.load()`
  returns the zero-copy `TenzorDataset`, and the wheel installs the `tenzor` command. PyTorch is
  now an optional extra; `python/tenzor.py` remains as a compatibility shim.
- README Quickstart: installation, a five-line ingestion example and benchmark highlights.
- The engine is a library crate (`tenzor_pipe::convert`); the `tenzor` binary and the Python
  extension (workspace member `bindings/python`) share it.

## 0.2.0 — independent EPYC verification

- Accept the supplied exact-value Huffman/bit-read/formula-table and IMDCT-buffer optimizations and contiguous sinc buffer.
- Verify bounded threaded audio decode and the inline fallback without changing production Rust source or floating-point order.
- Rebuild with pinned Rust 1.98.1 and NASM; restore full PyTorch and cargo-deny gates.
- Fix release scripts so failed checks fail the process and generated valid fixtures must decode successfully; deduplicate repeated fixture cases.
- Run five paired performance repetitions across 1/2/3/4/6/8 video workers, extended memory, write-failure, corruption and independent media checks.
- Keep the smaller-IMDCT numerical change deferred and commercial project license terms explicit.

## 0.2.0

- Audio speedups with output byte-identical to 0.1.9 (apart from the embedded
  version string), measured on a 2-core sandbox: 330 s audio-only 6.94 s → 1.68 s.
- AAC Huffman decoding uses a lookup table (was a linear scan per bit); whole-word
  bit reads; exact dequantization and scalefactor-gain tables; IMDCT buffer reuse.
- Resampler keeps decoded PCM in a contiguous buffer with a same-order dot-product
  fast path and block-wise release of consumed samples.
- Concurrent mode decodes audio on its own thread feeding the resampler through a
  bounded 32-chunk queue (`--no-audio-decode-thread` restores inline decoding).
- New profile stage `audio_source_wait`; `audio_source` is decode time on the
  decode thread.
- 0.1.9 reports and release metadata moved to docs/releases/v0.1.9/; the 0.1.9
  binary is now reference/bin/tenzor-v0.1.9-linux-x86_64 (identity oracle).

## 0.1.9

- Independently build and verify the supplied 0.1.8 chunked-decoder implementation.
- Count the emitting chunk against its payload budget; add a regression that reproduces the previous overshoot.
- Catch emitter panics and cancel before scoped joins, including partial thread-start failure.
- Require all VCL NALs in a cut candidate to be IDR slices.
- Release mapped input pages during planning; cap planning vector payload before allocation.
- Reproduce 600 successful normalized-byte comparisons, 160 expected-error agreements, source-image fidelity, and corrupted-input recovery.
- Benchmark 1/2/3/4/8 workers on the EPYC environment and retain explicit resource controls.
- Dependency versions and tensor/DSP definitions remain unchanged.


## 0.1.8

- Add `--video-workers`, `--chunk-target-ms` and `--video-buffer-mib`: chunked
  N-decoder H.264 decoding over verified IDR boundaries, with epoch selection
  pre-computed from container timing and a bounded ordered reorder window.
- Default stays `--video-workers 1`, which is the unchanged 0.1.6 decode path.
- Add `video_window_wait` and `video_reorder_wait` profile stages; video
  parse/decode/resize stages are summed across workers when N > 1.
- Output is byte-identical to 0.1.6 (except the embedded version string) across
  760 fixture/setting/worker combinations; see V0.1.8_REPORT.md.
- The 0.1.7 two-half experiment was not carried forward (its source was not
  available). Its strict epoch order plus 2–4 slot queue most likely stalled the
  second decoder; that diagnosis was not measured on 0.1.7 itself.

## 0.1.6

- Concurrent audio/video workers with a single synchronized Arrow collector.
- Byte-budgeted queues independent of batch size, including zero-capacity rendezvous.
- Broadcast cancellation, source/error preservation, explicit EOF counts, worker
  joins and Rust panic recovery with release unwind enabled.
- Optional active-stage and wait-time profiling; sequential comparison mode.
- Patch the OpenH264 wrapper to configure thread count before Initialize; isolate
  decoder-thread experiments behind a disabled-by-default build feature.
- Preserve exact v0.1.5 logical values; add corrupted-track, disk-full, channel
  cancellation, panic, budget and delayed-publication tests.
- Add same-host benchmarks and report failed decoder-thread experiments.

## 0.1.5

- Move nearest-epoch selection ahead of YUV-to-RGB conversion and resizing.
- Decode every H.264 access unit; use bounded PTS lookahead and preserve earlier
  midpoint ties, sparse frames, B-frame ordering, final frames and audio tails.
- Convert each selected picture once and borrow it across all matching epochs.
- Expose decoded/resized counts; verify 9,900 decoded versus 660 resized at 30 fps.
- Add exact old/new tensor regression checks and alternating measured benchmarks.
- No new dependencies or changes to the project licensing terms.

## 0.1.4

- Compile and independently validate the supplied 0.1.3 implementation.
- Fix MP4 codec-option API, AVC config error conversion and decoder dimension types.
- Replace the failed Baseline-profile decoder with source-built OpenH264; feed
  all access units, convert strict AVCC lengths to Annex-B, initialize SPS/PPS,
  flush delayed pictures, cap reordering, and reject changing parameter sets.
- Expose and honor SPS/MP4 SDR color matrix and range; validate supported pixel
  formats and reject HDR/unsupported formats.
- Apply simple MP4 edit lists, including AAC priming, and record selected-frame
  presentation timestamps alongside epoch timestamps.
- Replace whole-file PCM/Log-Mel accumulation with bounded pull-based decoding,
  windowed-sinc resampling, overlapping STFT, epoch batches and explicit EOF masks.
- Correct Mel bin frequencies; handle windows shorter than 25 ms and use the
  real silence log floor for padding.
- Replace the AAC dependency's quadratic inverse transform with an equivalent
  FFT calculation, retaining the direct formula for numerical verification.
- Add non-overwriting output reservation, cleanup on ordinary errors, completion
  reopening checks, and argument/resource limits.
- Verify multi-batch dynamic-shape PyArrow/PyTorch loading and zero-copy pointers;
  add a copy option for mutable tensor use.
- Add generated fixtures, independent numerical/image checks, failure tests,
  measured baseline/memory harnesses, pinned tools, license inventory and notices.
- Disable ThinLTO after observed unresolved-symbol release-link failures.

See the reports for passing evidence, actual speed/memory results and limits.
