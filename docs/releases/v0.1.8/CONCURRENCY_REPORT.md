# Concurrency and empirical profiling — 0.1.6

**Implemented and verified:** dual audio/video workers, byte-budgeted queues,
explicit epoch/EOF validation, one Arrow writer, cancellation and joins, sequential
comparison mode, optional profiling and isolated decoder-thread experiments.

## Architecture and bounds

MP4 metadata is parsed once and borrowed by scoped workers; WAV uses its existing
reader. The audio worker retains the existing AAC/downmix/sinc/STFT/Mel calculation.
The video worker decodes every access unit and converts only selected pictures.
An owned selected tensor crosses the channel; it is copied into the Arrow batch.
Channel ownership transfer does not make the complete pipeline zero-copy.

Capacity per track is min(requested depth, byte budget / sum of present-track epoch
payloads). Default depth 2 uses 1,229,824 queued payload bytes at 224/0.5s. At 336 it
uses 2,735,104 bytes. A too-small budget yields a rendezvous channel rather than an
unbounded allocation or an unsupported resolution. Worker/collector working tensors,
including the decoder callback's tensor and owned channel copy, are additional
bounded allocations. Arrow batch size is independent of queue depth.

Every data message has an epoch index; the collector checks order. Source picture
PTS is carried separately from the epoch grid. Workers send a checked final count
only after finishing decoding. Existing audio padding, final-video repetition and
absent-source metadata are preserved. Cancellation disconnects a broadcast channel,
waking blocked sends/receives, and receivers are dropped before joins. Original
worker or writer errors take precedence over cancellation consequences. Rust panic
recovery uses unwind; native decoder faults remain process-level failures.

## Measured stages on the 330-second fixture

| Stage (elapsed seconds) | Sequential | Concurrent |
|---|---:|---:|
| setup | 0.0052 | 0.0051 |
| video_parse | 0.0265 | 0.0290 |
| video_decode | 8.7697 | 9.4330 |
| video_resize | 0.2971 | 0.2948 |
| audio_source | 3.8483 | 4.5376 |
| audio_resample | 2.3813 | 2.3934 |
| audio_mel | 0.3988 | 0.4078 |
| arrow_pack | 0.0803 | 0.0749 |
| arrow_write | 1.0780 | 1.1459 |
| worker_send_wait | 0.0000 | 2.9137 |
| collector_receive_wait | 0.0000 | 8.8556 |
| final_sync | 0.0000 | 0.0000 |

Profiled wall times: 16.917 s sequential,
10.093 s concurrent. Source decoding/downmix is explicitly excluded
from audio_resample. The source timer includes audio packet reads and validation;
video_decode measures OpenH264 calls and EOF flush. Conversion/resizing is separate.
ArrowPack covers batch assembly and Arrow array construction. ArrowWrite covers IPC
encoding/writing/flushing; FinalSync covers fsync. Queue timings include channel
operation cost and blocking. Startup, selected-tensor copies, scheduling and some
metadata/teardown overhead remain outside individual stage timers. Overlapping
worker and collector waits are not additive CPU costs.

## Decoder threading result

Pinned openh264 0.9.8 configured the decoder's thread option after Initialize. The
local BSD wrapper patch moves nonzero options before Initialize and leaves zero
untouched. Even with that correction, counts 2 and 4 timed out after 15 seconds on
a Baseline fixture. Count 1 matched five valid fixtures and rejected a corrupted NAL.
This does not establish general thread safety or a throughput advantage. The normal
binary rejects nonzero counts. The experimental feature is source-only and disabled
by default; no experimental binary is included in the release.

To reproduce the isolated experiment, use separate target directories and the
watchdog script; do not replace a deployed binary:

```sh
cargo build --release --locked
CARGO_TARGET_DIR=target-experiment cargo build --release --locked --features experimental-decoder-threads
python3 scripts/test_decoder_threads.py --binary target-experiment/release/tenzor --reference target/release/tenzor
```

The script stops testing a thread count after its first hang/crash/value mismatch,
records unrun cases, and deletes timeout leftovers. A watchdog kill cannot run the
engine's ordinary cleanup. No claim of deadlock immunity is made for native threaded
decoding. Further work needs a safe decoder API/bitstream integration investigation,
not an assumption of linear speedup.

## Current outcome

Paired median improvement: 15.940 → 10.402 s (34.7% less wall time).
Baseline: 5.938 s. Full measured table and memory behavior are
in BENCHMARK_REPORT.md. Exact comparisons, failure checks and publication findings
are in TEST_REPORT.md. Licensing business decisions remain separate and unchanged.
