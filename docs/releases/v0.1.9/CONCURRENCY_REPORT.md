# CONCURRENCY_REPORT — TenzorPipe 0.1.9

The default retains independent audio/video processing with one video decoder.
`--video-workers 2` enables the measured recommendation: bounded, ordered IDR chunks
using independent OpenH264 instances. Internal OpenH264 threading remains zero.
No Rayon pool, GPU or uncontrolled decoder thread fan-out was added.

The planner assigns epochs using the existing nearest-PTS/midpoint/tie rules. It
validates AVCC access units and accepts a cut only when all VCL slices are IDR and
presentation order cleanly separates chunks. Each worker initializes SPS/PPS,
decodes every access unit in its chunk and flushes delayed pictures. Only selected
pictures are color-converted/resized. Results are emitted in chunk order to the
existing bounded epoch channel; the collector writes and releases Arrow batches.

## Defects reproduced and repaired

1. **Chunk-buffer overshoot:** the supplied scheduler released its admission credit before emitting the current chunk. A window of two could hold three chunk payloads. The new regression reproduced this failure. Credit now returns after emission and payload release; the bound includes the chunk being emitted.
2. **Emitter-panic deadlock:** a panic could bypass cancellation and strand workers behind the admission window during scoped joins. The regression timed out on the supplied code. The emitter now uses the shared panic-to-error boundary; scope guards cancel before joining. Partial thread-start failures also cancel already-started workers.
3. **Indexing memory:** the prepass now periodically releases mapped source pages, drops unused index vectors before decoding, and applies a conservative planning-payload cap before allocation. The earlier v0.1.7 residency diagnosis is preserved as history, not reused as current measurements.
4. **Chunk validity:** all AVCC NAL lengths and headers are checked before accepting a cut point, and every VCL NAL in the access unit must be IDR. Malformed tails and mixed IDR/non-IDR slices cannot establish a chunk start.
5. **Reproducibility:** failing identity/fuzz checks now fail their scripts; hashing is streamed; the root-only cargo-deny version exception is updated. No dependency or audio DSP algorithm was changed.


The payload bound is `window * max_chunk_tensor_bytes`, where window is at most
N+1 and is reduced to fit the configured chunk budget. It includes pending, decoding
and currently emitted chunks. The admission index advances only after emission.
If fewer than two chunks fit, the engine falls back. MP4 metadata, planning vectors,
codec state, working tensors, epoch queues and Arrow batches are separate allocations.
Planning vectors have a conservative 32 MiB payload allowance and MP4 indexing a
16 MiB cap. These safeguards do not promise an arbitrary total-RSS ceiling.

Errors broadcast cancellation and wake condition-variable/channel waits. The guard
runs before implicit scoped joins; explicit Result handling covers partial spawn
failure. Panic recovery applies to Rust unwind only. Native crashes or indefinitely
blocked native calls remain process-level failures.

## Evidence and operational choice

26 Rust tests include live-payload and emitter-panic regressions. 38 pipeline checks,
30 boundary/failure checks, 600 successful expanded artifact comparisons, 160 error
agreements and 24 corruption checks pass. The scheduler matrix verifies fallback as
well as true parallel cases; 177 executions actually entered chunked mode.

N2's 330-second median is 7.312 s and median peak RSS
78.85 MiB; N1 is 9.506 s /
60.67 MiB. N3 is only
1.1% faster than N2 on these three runs and uses
95.36 MiB. N4/N8 provide no median speed
benefit. Default1 remains a conservative resource policy; use2 explicitly for this
workload. Automatic cores-minus-two selection is not a universally optimal setting.
See BENCHMARK_REPORT.md for every observation and remaining baseline gap.
