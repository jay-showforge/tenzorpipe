# CONCURRENCY_REPORT — TenzorPipe 0.2.0

Concurrent mode now has an audio decode/downmix producer feeding the existing
resample/Mel worker through 32 ordered PCM chunks. `--no-audio-decode-thread` retains
inline decode; sequential mode also uses inline decode. Video still uses the repaired
v0.1.9 ordered IDR-chunk scheduler with independent OpenH264 instances. The collector
alone creates/writes Arrow batches.

The new queue's supported AAC payload bound is 128 KiB and WAV bound 512 KiB, excluding
producer/consumer working chunks and allocator state. The contiguous source buffer
is drained in 32, 768-sample blocks. Neither allocation grows with total media duration.
MP4/planner/footer metadata and native decoder state have their separately documented
bounds/limits; source files remain immutable while mapped.

The producer catches Rust source panics and sends the error in order. Dropping the
receiver releases a producer blocked in send. A consumer-gone guard runs during normal
return, errors and unwinding; a timed blocked send checks it every 20 ms before scoped
joins. Pipeline cancellation is checked before further decode/epoch work. The engine
also retains v0.1.9 cancellation guards and emitted-chunk accounting. Rust unwind
recovery does not cover native crashes or indefinitely blocked native calls.

30 engine tests include source error/panic, consumer stop/error/panic, pipeline
cancellation and bit-identical threaded/inline epochs. 38 concurrency checks, 30 video
boundary checks, 92 malformed-input comparisons and 12 paired EFBIG failures pass.
The extended audio matrices have 504 successful byte comparisons and 60 matching
expected errors. No floating-point order or output contract was relaxed.

Audio-only median is 1.421 s threaded versus
2.248 s inline and 6.650 s in v0.1.9. Recommended
video workers on this host: **6**, chosen as the smallest count within 5% of the
best five-run median. CLI default remains 1; auto(core count minus 2) is opt-in and
is not claimed optimal. See BENCHMARK_REPORT for CPU/RAM tradeoffs and long-run RSS.

Profile timers overlap. In threaded mode `audio_source` measures producer work and
`audio_source_wait` measures the consumer wait; the resample timer subtracts only its
own source-wait duration. Summed video decode/window-wait times can exceed wall time.
