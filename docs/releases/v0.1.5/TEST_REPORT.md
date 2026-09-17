# TEST_REPORT — TenzorPipe 0.1.5

**PASS** for the scope below. Tests use the compiled release binary and independent
FFmpeg/Python consumers. No benchmark target is treated as a result.
Binary SHA-256: `ad8f8cdf7df93b3be2da5a6c5098196051c41a44f1784aadfdcb65da64dc249d`.

## Real generated media

FFmpeg generates moving test patterns, tones and a timed flash/beep. There is no
third-party footage. Tests include 720p Baseline H.264/AAC, High Profile B-frames,
short MP4, AAC-only MP4, silent video, 44.1/48 kHz stereo WAV, mono WAV, six-channel
WAV/AAC, 5 ms float WAV, non-divisible durations and 160/224/336 output sizes.
Every listed output has independently checked timestamps, dimensions, finite
values, source-presence metadata and the expected batch count.

| Fixture | Resolution | Epochs | Batches | Worst frame MAE /255 | Active Log-Mel MAE |
|---|---:|---:|---:|---:|---:|
| baseline720.mp4 | 224 | 7 | 4 | 0.000000 | 0.001843 |
| high-bframes.mp4 | 160 | 7 | 4 | 0.152480 | 0.001843 |
| high-bframes.mp4 | 224 | 7 | 4 | 0.156934 | 0.001843 |
| high-bframes.mp4 | 336 | 7 | 4 | 0.167806 | 0.001843 |
| short.mp4 | 224 | 1 | 1 | 0.000000 | 0.004248 |
| silent-video.mp4 | 224 | 3 | 2 | 0.000000 | 0.000000 |
| wav-44100-2ch.wav | — | 3 | 2 | 0.000000 | 0.000003 |
| wav-48000-2ch.wav | — | 3 | 2 | 0.000000 | 0.000004 |
| wav-16000-1ch.wav | — | 3 | 2 | 0.000000 | 0.000011 |
| wav-48000-6ch.wav | — | 3 | 2 | 0.000000 | 0.000001 |
| audio-only.mp4 | — | 7 | 4 | 0.000000 | 0.001843 |
| color709.mp4 | 224 | 3 | 2 | 0.039039 | 0.000941 |
| fullrange.mp4 | 224 | 3 | 2 | 0.049027 | 0.000941 |
| sync-pulse.mp4 | 224 | 5 | 3 | 0.000000 | 0.000016 |
| aac-6ch.mp4 | — | 3 | 2 | 0.000000 | 0.001409 |
| tiny.wav | — | 1 | 1 | 0.000000 | 0.000001 |

A separate 330 ms window test verifies 33×64 audio features and timestamps
0/330/660/990 ms. A flash and a 44.1 kHz AAC tone pulse align at **1,000 ms**;
the audio feature peak is at hop 100. See `synchronization.json`.

## Selection optimization regression

All 41 old/new comparisons preserve every logical tensor and timestamp byte.
Resolutions 160/224/336 and windows 50/330/500 ms are exercised, including sparse
frames, irregular timestamps, midpoint ties and audio extending past video.
The 330-second benchmark decodes 9,900 pictures and resizes exactly 660.
See `epoch-selection-regression.json`, `epoch-patch-benchmarks.json`, and
EPOCH_SELECTION_REPORT.md for commands, counts and alternating timings.

## Video correctness

FFprobe obtains source presentation timestamps and picture types. FFmpeg decodes
selected source frames independently. NumPy applies the documented sampling and
color matrix, then compares them with reconstructed tensors. The fixed gate is
per-frame MAE below 3 on a 0–255 scale; this is an image fidelity test, not merely
a nonzero-value check. Selected I, P and B pictures pass, including delayed output
at EOF and later positions in the long clip. Contact sheets in `evidence/` show
source on the left and tensor reconstruction on the right; these were visually
inspected.

Across all 60 frames of the separate 30-second baseline comparison, worst-frame
MAE is 0.295717/255 and minimum PSNR is
37.440 dB. The maximum isolated pixel difference is
153.703/255. The decoders are therefore
**not bit-exact**; isolated differences are reported rather than hidden behind an
identity claim. `benchmark-worst-frame.png` shows the highest-MAE pair. An initial
maximum-single-pixel assertion was overly strict for this comparison; the final
check consistently uses the same predeclared per-frame MAE criterion as the media
matrix and retains peak error and PSNR as reported measurements.

## Audio correctness

FFmpeg independently decodes original-rate PCM. A NumPy implementation constructs
the sinc phase coefficients and computes STFT/triangular HTK Mel/log-power results.
The FFT inverse AAC transform is separately compared with its original direct
formula on long/short, dense/sparse spectra (maximum allowed difference 2e-6).
Other Rust tests verify arithmetic downmix, filter support, 16 kHz output length,
1 kHz passband gain, better than 60 dB rejection of a 12 kHz tone for both 44.1 and
48 kHz sources, and agreement across irregular 317-sample source chunks.

The oracle compares energetic bins (reference Log-Mel > -5), with MAE < 0.08 and
99th-percentile error < 0.5. Near-floor bins amplify tiny decoder differences;
raw maximum errors remain in `matrix.json`. Every output is finite, valid source
hops are counted, short final windows are analyzed, and fully padded rows equal
the silence floor -10. No epoch-boundary resampler/STFT reset is permitted.

## Arrow / PyTorch

All batches are inspected, not just batch zero. PyArrow memory maps the IPC;
PyTorch data pointers equal the corresponding NumPy/Arrow buffer pointers.
Dynamic video shapes, absent video, negative indexing, copy mode, total row count,
valid-hop masks and source-frame timestamps are checked. PyTorch emits its usual
read-only NumPy warning: mutation requires copy mode. This is payload zero-copy
on CPU, not zero allocation or zero-copy CUDA transfer.

## Deliberate failures

12 cases pass: empty file, truncated MP4, invalid AVCC NAL
length, MPEG-4 Part 2 video in MP4, unsupported MP3 extension, resolutions 0/1025,
window length 0/NaN/non-hop-aligned 0.123, batch size 0 and an over-budget batch.
Each returns nonzero with an understandable error and no completed output.
An existing output is preserved. Video-without-audio and audio-without-video are
accepted with explicit metadata, rather than presented as malformed inputs.
Exact messages are in `failures.json`.

## Streaming and long-file checks

12 measured artifacts are read in full, including 90-, 330- and 660-second inputs.
The 330-second outputs made with batches 2, 32 and 64 have identical SHA-256
streams for each logical tensor/timestamp column. Selected early/middle/late
source imagery in the 330-second output is also independently reconstructed.
Memory results and their index/allocator limitations are in BENCHMARK_REPORT.md.

## Reproduce

```sh
cargo build --release --locked
cargo test --all-targets --locked
cargo fmt --check
cargo clippy --all-targets --locked -- -D warnings
cargo install cargo-deny --version 0.20.2 --locked
cargo deny check licenses
python3 -m pip install -r python/requirements-test.txt
python3 -m pip install torch==2.14.0+cpu --index-url https://download.pytorch.org/whl/cpu
python3 scripts/generate_matrix.py --long --extended-memory
python3 scripts/test_matrix.py
python3 scripts/benchmark_matrix.py
python3 scripts/verify_benchmarks.py
```

Raw evidence: `unit-tests.log`, `matrix.json`, `failures.json`,
`verification-summary.json`, `python-loader.json`, `synchronization.json`,
`benchmark-verification.json` and the image contact sheets. These demonstrate the
tested fixture set, not exhaustive codec conformance or a security audit.
