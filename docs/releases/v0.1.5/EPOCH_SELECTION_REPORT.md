# Epoch-selected resizing — TenzorPipe 0.1.5

Implemented, compiled and measured. All 41 short old/new comparisons and all 12
main benchmark artifacts preserve every logical tensor/timestamp byte from 0.1.4.
Schema version metadata intentionally changes to 0.1.5. Independent source-image,
audio, PyArrow and PyTorch checks also pass; no output fidelity gate was relaxed.

## What the patch changes

- `src/video.rs`: inspect presentation timing before YUV-to-RGB conversion and
  nearest-neighbor spatial resizing. The resizer and its arithmetic are unchanged.
- Use a capped 34-entry timestamp heap plus current/next PTS. This additional
  metadata pass does not buffer pixel planes or a duration-sized selection list.
- Preserve the nearest-picture policy, integer midpoint and earlier-frame tie
  break. A single selected tensor may serve several epochs without another resize.
- `src/main.rs`: consume borrowed selected tensors immediately into bounded Arrow
  batches, preserving audio synchronization, final frames and audio tails.
- Continue feeding all AVCC-converted access units through OpenH264 and flushing
  delayed B pictures. Validate PTS agreement, count, ordering and supported limits.
- Log both decoded and resized picture counts. On the measured 330-second input:
  **9,900 pictures decoded, 660 resized; 93.33% fewer conversions/resizes.**
- No dependency additions. Advance the project-only license exception to exactly
  0.1.5; do not add BUSL to the dependency allowlist or invent commercial terms.

## Measured speed, not projected speed

Generated 640×360/30 fps High Profile H.264/B-frames + 48 kHz AAC, 224×224 output,
0.5-second epochs, batch 32, same host, serial alternating old/new runs. Timings
include startup, decoding, audio preprocessing, Arrow writing and completion.

| Run | Wall seconds | Sampled peak MiB | CPU (one core = 100%) |
|---|---:|---:|---:|
| epoch-patch-old-330-r0 | 19.194 | 58.04 | 99.5% |
| epoch-patch-new-330-r1 | 15.014 | 56.98 | 99.9% |
| epoch-patch-old-330-r2 | 19.736 | 58.06 | 99.9% |
| epoch-patch-new-330-r3 | 15.156 | 56.91 | 99.9% |

Two-run medians: **19.465 s → 15.085 s**, or **22.5% less wall time**
and **1.29× throughput**. These are two repetitions, not a confidence
interval. The separate same-session FFmpeg baseline took **6.184 s**.
TenzorPipe still does not beat it. The proposed 10× overall gain and sub-3.5-second
video time were hypotheses and were not achieved by this patch.

The resize-call reduction does not eliminate full H.264 decoding, audio work,
packet parsing, memory movement or Arrow serialization. Further stage profiling
is needed before identifying the next single dominant function. The pipeline is
still principally single-threaded. Audio/video concurrency is a separate future
change; neither its benefit nor beating the baseline is guaranteed here.

The first paired harness validated large Arrow maps in its own parent process.
Those mappings could inflate a subsequent child's inherited `wait4` RSS high-water
reading. That run is retained under `evidence/initial/parent-map-*`; final paired
measurements use a separate validation process and hash the actual old/new binary.
Validation is outside timed runs. Sampling can still miss brief RSS peaks.

## Memory check

| Media seconds | Wall seconds | Sampled peak MiB | Final artifact MiB |
|---|---:|---:|---:|
| 330 | 15.119 | 56.98 | 399.17 |
| 90 | 4.845 | 55.97 | 108.87 |
| 660 | 30.732 | 74.98 | 798.34 |
| 1320 | 66.376 | 60.27 | 1597.28 |

The 22-minute check was added after the 11-minute run reached a higher allocator
high-water mark than the 5½-minute run. These are observed RSS values, not a claim
of identical RSS at every duration or proof of zero leaks. The engine holds bounded
pixel/audio/tensor buffers; the parser's sample metadata remains capped at 16 MiB,
and Arrow footer metadata grows with batch count. All 2,641 rows across 83 batches
of the 22-minute artifact were independently loaded and checked for finite values
and continuous epoch timestamps. The remuxed source is 1,320.010677 seconds; its
10.677 ms tail correctly requires a 2,641st epoch. An initial test assumed exactly
1,320 seconds; the corrected gate derives expected rows from independently probed
source durations. No intermediate decoded-media files were written.

## Verification and reproduction

- 13 Rust unit tests, including four new timing/selection tests.
- 16 independent media cases and 12 deliberately invalid-input cases.
- 41 exact old/new comparisons at 160/224/336 and 50/330/500 ms, covering B-frames,
  short media, sparse frames, irregular timestamps, midpoint ties and audio tails.
- All 12 main benchmark outputs match v0.1.4 logical hashes, including 11 minutes
  and batch sizes 2/32/64. Four alternating old/new 330-second outputs also match.
- `cargo fmt`, Clippy with warnings denied, and `cargo deny check licenses` pass.

From the v0.1.5 repository, with the prior release binary available:

```sh
cargo build --release --locked
cargo test --locked --all-targets
cargo fmt --check
cargo clippy --all-targets --locked -- -D warnings
cargo deny check licenses
python3 scripts/generate_matrix.py --long --extended-memory
python3 scripts/test_matrix.py
python3 scripts/test_epoch_selection.py --old-binary /path/to/v0.1.4/tenzor
python3 scripts/benchmark_matrix.py
python3 scripts/verify_benchmarks.py
python3 scripts/benchmark_epoch_patch.py --old-binary /path/to/v0.1.4/tenzor
python3 scripts/benchmark_epoch_memory.py
```

Alternatively apply `patches/epoch-selected-resize.patch` to v0.1.4 with
`patch -p1 < /path/to/epoch-selected-resize.patch`, then build. That narrow patch
contains the engine, unit tests, version and license-exception changes. The complete
ZIP additionally includes the regression/benchmark scripts, reports and binary.
Run `python3 scripts/package_release.py` to generate reports, checksums and ZIP after
all evidence is available. Original v0.1.4 reports remain under `docs/releases/`.

Old measured binary SHA-256: `95163cf9b2fd5e0ac2a5431715fe153e53afeff5526024a090523f126eaac0dc`.
New measured binary SHA-256: `ad8f8cdf7df93b3be2da5a6c5098196051c41a44f1784aadfdcb65da64dc249d`.
