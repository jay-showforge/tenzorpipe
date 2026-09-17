# TenzorPipe 0.3.0

MP4 H.264/AAC-LC and WAV → synchronized RGB/CHW and Log-Mel tensors in batched
Apache Arrow IPC (`.tenzor`). This release runs audio and video concurrently through bounded queues,
preserving the independently verified v0.1.6 tensor values. See **CHANGELOG.md** for 0.3.0 changes and **BENCHMARKS.md** for current measurements; earlier
build, test and benchmark reports are in docs/releases/.

## Build

Linux x86-64 is the tested target. Extract the complete ZIP, or both split archives into the same parent folder first. Install a C/C++
compiler, NASM, and Rust 1.98.1.
On a conventional Debian/Ubuntu host:

```sh
sudo apt-get update
sudo apt-get install -y build-essential nasm ffmpeg strace python3-venv
rustup toolchain install 1.98.1 --profile minimal --component rustfmt,clippy
cargo build --release --locked
cargo test --locked --all-targets
cargo install cargo-deny --version 0.20.2 --locked
cargo deny check licenses
```

OpenH264 is compiled from BSD-licensed source. No FFmpeg library or executable is
used by the engine. NASM enables OpenH264's assembly optimizations, including the AVX2
routines (selected at runtime by CPUID) via the vendored `openh264-sys2` build patch
(vendor/openh264-sys2/TENZORPIPE_PATCH.md). A NASM failure now fails the build instead of
silently producing a slower C-only decoder; set `OPENH264_ALLOW_C_FALLBACK=1` to accept one.
The lockfile and local SPS metadata and AAC inverse-transform patches are included.

## Run

```sh
./target/release/tenzor -i fixtures/fixture-h264-aac.mp4 -o example.tenzor
./target/release/tenzor -i fixtures/wav-44100-2ch.wav -o audio.tenzor
./target/release/tenzor -i fixtures/high-bframes.mp4 -o small.tenzor \
  --resolution 160 --batch-epochs 2
```

Output must not already exist. Its name is reserved without overwriting another
file. The engine flushes and syncs data, closes the writer, then reopens the Arrow
file before reporting success. Ordinary errors remove the partial output. The
file is visible while it is being written, but is not readable as completed IPC
until its footer is written; consumers must wait for exit status 0. A hard kill
can leave an incomplete file, which must be removed before retrying. No intermediate
decoded media files are created. Input media must remain immutable during processing.

## Execution and profiling

Concurrent audio/video processing is the default. The collector alone writes Arrow.

```sh
./target/release/tenzor -i fixtures/high-bframes.mp4 -o concurrent.tenzor --profile
./target/release/tenzor -i fixtures/high-bframes.mp4 -o serial.tenzor --execution sequential
./target/release/tenzor -i fixtures/high-bframes.mp4 -o rendezvous.tenzor --queue-mib 0
```

`--queue-depth` defaults to 2 (1–4 allowed). `--queue-mib` defaults to 4 (0–64).
Capacity is reduced to fit the combined audio/video queued payload budget; it is
independent of Arrow batch size. Capacity zero uses rendezvous channels. Decoder
state, worker/collector working tensors, allocator overhead and Arrow batches are
additional bounded memory, not included in that payload budget. Default 224/0.5s
queues use 1,229,824 payload bytes across both tracks.

`--profile` prints JSON on stderr with wall-clock stage durations. Audio source
reading/decoding/downmix is excluded from the resampling timer. Channel send/receive
waits are separate. Stage durations overlap across threads and are not CPU time or
an additive decomposition of total latency. Frame copying, scheduling and other
small overheads remain outside individual timers. Profiling itself has overhead.

Worker errors cancel the pipeline, wake blocked channels and join every worker.
Worker/collector Rust panics unwind into reported errors. Native codec crashes,
process kills and hardware/storage failures are not recoverable worker errors.

The normal build accepts only `--decoder-threads 0`. The source-only
`experimental-decoder-threads` feature permits 1/2/4 for controlled experiments;
2 and 4 hung on a tested Baseline fixture even after correcting pre-initialization
configuration. Do not enable that feature in deployments. See docs/releases/v0.1.9/CONCURRENCY_REPORT.md.

## Parallel video decoding

`--video-workers N` decodes H.264 with N independent OpenH264 instances over
IDR-aligned chunks. `1` runs the single-decoder path. `0` and the **default** (since
0.3.0) pick `available_parallelism()`, clamped to 1–16 and further capped at
the clip's number of chunks. The default falls back to one decoder when the experimental
`--decoder-threads` is set.

Each worker adds OpenH264 picture buffers: about 48 MiB per extra worker at 1080p (about
14 MiB at 360p), so a 16-thread host can reach ~500 MiB of engine RSS on 1080p input. Pass
`--video-workers 1` (or a small N) where RAM matters more than latency, and always pass an
explicit value when running several `tenzor` processes at once (see
docs/FILE_BATCHING.md and `python/tenzor_batch.py`). BENCHMARKS.md has current measurements.

```sh
./target/release/tenzor -i fixtures/long-330.mp4 -o par.tenzor --profile          # auto workers
./target/release/tenzor -i fixtures/long-330.mp4 -o small.tenzor --video-workers 1 # lowest RAM
```

- Chunks start only at access units whose VCL NALs are all IDR slices and whose
  presentation times follow every earlier access unit. Sync samples that are not
  IDR (for example open-GOP I-frames) are never used as cut points.
- Epoch-to-picture selection is computed once from container timing with the same
  midpoint and tie rules, so output is byte-identical to the single-decoder path.
- A worker may only start chunk `c` while `c < next_chunk_to_emit + window`
  (`window = N + 1`, reduced to fit `--video-buffer-mib`, default 64). Decoded
  tensors, including the chunk being emitted, are bounded by `window × largest chunk`.
  The admission credit is returned only after emission completes.
- `--chunk-target-ms` (default 2000) merges short GOPs into larger chunks.
- The path falls back to the single decoder, logging `video_mode=single_fallback
  reason=...`, for clips with fewer than two valid chunks, duplicate presentation
  timestamps, a budget too small for two in-flight chunks, or excessive planning metadata.
- Planning has a conservative 32 MiB vector-payload allowance and falls back before
  allocation if the sample count would exceed it. This is separate from tensor buffers.
- On Linux, indexing releases mapped source pages every 128 samples so residency
  does not grow with input file size before decoding begins.
- Each worker adds decoder state and allocator overhead to RSS. Queue, chunk and
  batch budgets are separate. See BENCHMARKS.md for current measured RSS.
- Worker and emitter panics cancel before scoped joins; partial thread-start
  failures also trigger cancellation. Native faults remain process-level failures.

### Skipping unused non-reference pictures

Only the picture nearest each epoch start is converted, so most decoded pictures are
discarded. An access unit whose slices all have `nal_ref_idc == 0` (typically
non-reference B-frames) cannot affect any other picture. If no epoch selects it, it is now
never sent to OpenH264. Output is byte-identical to decoding everything; on the 1080p
benchmark clip 265 of 600 access units are skipped. The log reports
`skipped_nonref=N`, and `--no-skip-nonref` restores full decoding.

- Selection is decided before decoding. The chunked path uses its up-front plan; the
  single-decoder path runs a second bounded presentation lookahead. If that lookahead hits
  a metadata error, skipping stops and the normal path reports the error in its usual order.
- Only units containing non-reference non-IDR slices plus SEI, access-unit delimiters or
  filler are skipped. Parameter sets, end-of-sequence/stream NALs, data partitions and
  malformed units are always decoded, so framing errors are reported as before.
- Trade-off: corrupt *slice data* inside a skipped picture is no longer detected, because
  that picture is never decoded. Use `--no-skip-nonref` to validate every picture.

## Audio execution in 0.2.0

Concurrent mode now separates audio decoding from resampling/Mel processing.
A bounded 32-chunk queue preserves PCM order. Its queued mono payload is at most
128 KiB for the supported AAC access units or 512 KiB for WAV chunks; in-flight
chunks and decoder/resampler state are additional memory. The resampler retains
consumed samples only until its next 32,768-sample drain threshold.

`--no-audio-decode-thread` keeps decoding inline. Sequential execution also decodes
inline. Huffman lookup, bit reads, exact formula tables and reused IMDCT buffers
preserve the previous arithmetic and tensor values. The new `audio_source_wait`
profile timer measures consumer wait; `audio_source` is decode/downmix work on its
own thread. Stage timers overlap and cannot be added to recover wall time.

## Tensor contract

One row represents an epoch start, normally every 500 ms. Each row contains:

| Column | Meaning |
|---|---|
| `timestamp_ms` | Epoch start on the movie presentation timeline |
| `audio_valid_frames` | Number of 10 ms hops whose start is inside the audio timeline |
| `video_timestamp_ms` | Presentation timestamp of the selected source frame; absent for audio-only input |
| `video_tensor` | Float32 `[3,H,W]`, RGB, normalized to `[-1,1]`; absent for audio-only input |
| `audio_mel_tensor` | Float32 `[window_ms/10,64]`, frame-major Log-Mel |

Schema metadata supplies shapes, version, source-presence flag, audio DSP settings,
video matrix/range, normalization, sampling, and resize rules. A silent-video file
has `has_audio=false`, zero valid audio hops, and a documented `-10` placeholder;
it must not be interpreted as observed audio. WAV and AAC-only MP4 omit video.

Video selection is nearest presentation frame to each epoch start, with earlier
frames winning ties. Every compressed access unit is decoded, including P/B
frames; decoder output is reordered by presentation timestamps in a capped queue.
SPS/PPS initialize OpenH264. Strict AVCC-to-Annex-B conversion rejects truncated
NAL lengths. Simple MP4 edit lists account for B-frame composition offsets, AAC
priming, and initial delay. The final video frame is repeated through an audio tail.
Only epoch-selected pictures are converted and resized. A bounded 34-timestamp
metadata lookahead preserves midpoint ties without copying unselected pictures.
Resize uses nearest sampling with aligned corners and square stretching.

Audio uses an arithmetic mean across channels, 16 kHz resampling with a 129-tap
Blackman-windowed sinc and 1,024 fractional phases, a symmetric 400-sample Hann
window, 160-sample hop, 64 triangular HTK Mel bands over 0–8 kHz, unnormalized FFT
power, and `log10(power + 1e-10)`. STFT windows start at each hop (not centered).
Right-edge samples are zero-padded. Pure silence has feature value `-10`, not zero.
The final epoch includes padded rows; use `audio_valid_frames` as its mask.

Resolution defaults to 224 and accepts 32–1024. Window lengths accept 0.05–10 s
in exact 10 ms increments. Batch size accepts 1–1024 epochs with a 256 MiB tensor
payload ceiling. MP4 index allocation is capped at 16 MiB; over-limit inputs fail.

## Python / PyTorch

The `tenzorpipe` package runs the engine in-process through PyO3 (the GIL is released while
decoding) and reads the output as zero-copy PyTorch tensors. One abi3 wheel supports
CPython 3.9+ on Linux x86-64 with glibc 2.34 or newer.

```sh
pip install "dist/tenzorpipe-0.3.0-cp39-abi3-manylinux_2_34_x86_64.whl[torch]"  # built wheel
pip install ".[torch]"                                                         # from source
pip install "tenzorpipe[torch]"                                                # once published on PyPI
```

```python
import tenzorpipe as tp

info = tp.ingest("fixtures/high-bframes.mp4", "example.tenzor", resolution=160)
with tp.load("example.tenzor") as data:
    print(info["epochs"], data.video_shape, data.audio_shape)
    for batch in data.iter_batches():
        print(batch["timestamp_ms"], batch["video"].shape, batch["audio"].shape)
```

`tp.ingest()` accepts the CLI options as keyword arguments (`video_workers`, `window_sec`,
`batch_epochs`, `skip_nonref=False`, ...); options left unset use the CLI defaults, because the
arguments are parsed by the same definition. Failures raise `tenzorpipe.TenzorError` and
remove the partial output. The wheel also installs the `tenzor` command. Without the
`torch` extra, read files with PyArrow (`data.reader`, or `pyarrow.ipc.open_file`).

Building from source (`pip install .`) needs Rust 1.98.1 and NASM; `scripts/build_wheel.sh`
builds the release wheel and smoke-tests it in fresh virtual environments.

`iter_batches()` visits the whole file; `get_batch(0)` is just one batch.
`copy=False` wraps read-only Arrow/NumPy buffers without copying tensor payloads.
PyTorch cannot enforce read-only storage: do not modify these tensors, and keep
owners alive while using them. Use `copy=True` when applying in-place transforms.
No GPU transfer is zero-copy; moving to CUDA allocates GPU storage normally.

## Reproduce verification and benchmarks

```sh
# After the build/test dependencies above are installed:
bash scripts/reproduce.sh
```

The script contains every exact command, including the generated/repository audio identity matrices,
22-minute memory comparison, baseline benchmark, and write audit. It uses the
included v0.1.6 and v0.1.9 Linux comparison binaries in `reference/bin/`.
These are regression references; deploy `bin/tenzor-linux-x86_64` or your rebuilt
v0.2.0 binary. Allow roughly 15 GiB free for regenerated test outputs.

Test outputs default to `out/`. Set `TENZOR_OUTPUT_ROOT=/tmp/tenzor-artifacts`
to choose another output filesystem. Final recorded runs used this override after
workspace outputs failed delayed rereads with both the unchanged old and new
binaries; the local temporary output path passed repeated publication checks.

The media is generated locally with FFmpeg and consists of test patterns,
synthetic tones, and a flash/beep alignment clip. Short fixtures and selected
reconstructed images are included. Long fixtures and large benchmark datasets
are regenerated rather than bundled. FFmpeg is an independent test/oracle and
baseline tool only. See reports for exact commands, environment, measurements,
and the independently verified scope; untested codecs are not advertised.

Historical specifications in `docs/SOURCE_SPEC_v0.1.2.md` are design history,
not claims about the measured performance or supported formats of this release.
