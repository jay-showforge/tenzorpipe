# 0.1.4 implementation notes

The central decode → preprocess → epoch → Arrow batch architecture is retained.

- `media.rs`: simple MP4 edit-list timeline (encoder trim and initial delay).
- `video.rs`: strict AVCC conversion, SPS/PPS initialization, OpenH264 decoding,
  capped PTS min-heap, dimension/color checks, RGB/CHW conversion.
- `audio.rs`: lazy WAV/AAC source, bounded mono queue, phase-table sinc,
  240-sample STFT overlap, per-epoch Log-Mel and validity mask.
- `storage.rs`: self-describing Arrow fields, fixed-size float32 lists, batches.
- `main.rs`: synchronizes nearest video frames and audio hops; reserves output
  without overwriting, then flushes, syncs, and reopens it before success.
- `python/tenzor.py`: all-batch iteration, indexed rows, dynamic shapes,
  zero-copy read-only aliases, optional copies for mutation.

The supplied `rust_h264` decoder returned wrong Baseline-profile P-frames without
errors. It is no longer used for decoding. Its small patched dependency remains
for SPS/AVCC parsing; the patch exposes VUI color fields previously discarded.
OpenH264 is compiled from source; runtime FFmpeg is not used.

The AAC dependency had an O(M^2) inverse transform with repeated cosine calls.
A local patch replaces the 1024/128-coefficient transforms with a mathematically
equivalent f64 RustFFT embedding. Root tests compare dense/sparse long/short
transforms against the original direct sum, followed by independent real AAC
checks against FFmpeg.

Index metadata still scales with sample count within a fixed 16 MiB parser cap.
Arrow IPC file footer metadata scales with the number of RecordBatches. These are
small compared with video tensors, but this is not a claim of mathematically
constant total memory for arbitrarily long media. Audio queues, video reorder
state, and tensor batches are bounded. Read-only mapped input pages are discarded
on batch boundaries on Unix; the entire input is not copied into a byte vector.

See TEST_REPORT.md and BENCHMARK_REPORT.md for measured verification and limits.
