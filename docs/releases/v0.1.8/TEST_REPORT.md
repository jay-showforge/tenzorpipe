# TEST_REPORT — TenzorPipe 0.1.6

**PASS for the tested scope.** Binary SHA-256: `0e1a46ca8e67d872ac95d46b6541a283737d34dda6f27aaadaf6d9e6ad93f2d7`.

## Independent media matrix

| Fixture | Resolution | Epochs | Batches | Worst frame MAE /255 | Active Log-Mel MAE |
|---|---:|---:|---:|---:|---:|
| baseline720.mp4 | 224 | 7 | 4 | 0.000000 | 0.001843 |
| high-bframes.mp4 | 160 | 7 | 4 | 0.152480 | 0.001843 |
| high-bframes.mp4 | 224 | 7 | 4 | 0.156934 | 0.001843 |
| high-bframes.mp4 | 336 | 7 | 4 | 0.167806 | 0.001843 |
| short.mp4 | 224 | 1 | 1 | 0.000000 | 0.004248 |
| silent-video.mp4 | 224 | 3 | 2 | 0.000000 | 0.000000 |
| wav-44100-2ch.wav | 224 | 3 | 2 | 0.000000 | 0.000003 |
| wav-48000-2ch.wav | 224 | 3 | 2 | 0.000000 | 0.000004 |
| wav-16000-1ch.wav | 224 | 3 | 2 | 0.000000 | 0.000011 |
| wav-48000-6ch.wav | 224 | 3 | 2 | 0.000000 | 0.000001 |
| audio-only.mp4 | 224 | 7 | 4 | 0.000000 | 0.001843 |
| color709.mp4 | 224 | 3 | 2 | 0.039039 | 0.000941 |
| fullrange.mp4 | 224 | 3 | 2 | 0.049027 | 0.000941 |
| sync-pulse.mp4 | 224 | 5 | 3 | 0.000000 | 0.000016 |
| aac-6ch.mp4 | 224 | 3 | 2 | 0.000000 | 0.001409 |
| tiny.wav | 224 | 1 | 1 | 0.000000 | 0.000001 |

FFmpeg independently decodes source imagery and original-rate audio. NumPy computes
the documented resize/color and sinc/STFT/triangular Mel/log-power reference. Image
MAE gate remains <3/255; energetic audio bins use MAE <0.08 and p99 <0.5. Reference
maximum errors remain in matrix.json. Short EOF windows, 10 ms hops, nonzero signal,
44.1/48 kHz input, mono/stereo/six-channel audio and absent video/audio are exercised.
The flash/tone fixture remains aligned at 1,000 ms. A 330 ms epoch test checks 33×64
features. Rust tests retain >60 dB high-frequency rejection and streaming continuity.

Across all 60 pictures in the 30-second baseline comparison, worst frame MAE is
0.295717/255; minimum PSNR is
37.440 dB. Different decoders are not claimed
bit-identical; exact identity below compares TenzorPipe modes/releases.

## Exact-value and concurrency checks

- All 41 old/new comparisons match every logical column byte, including 160/224/336,
  50/330/500 ms windows, sparse/VFR fixtures, B pictures, short clips and audio tails.
- 32 sequential/concurrent comparisons cover queue budgets 0/1/4 MiB, resolution
  1024, WAV/AAC-only, silent video, and video continuing after audio EOF. Their tensors and timestamps match exactly.
- Six additional failure checks corrupt later AAC/video packets or impose a real
  file-size write limit. With rendezvous and buffered queues, each fails promptly
  with an error, leaves no output and does not hang. These make 38 checks total.
- Four new Rust tests cover byte caps, blocked receive cancellation, full/rendezvous
  send cancellation and Rust worker/collector panic recovery. Total Rust tests: 17.
- Twelve original invalid input/argument cases still fail correctly. Existing output
  preservation is checked. Normal audio-only/video-only files remain supported.
- PyArrow inspects all batches and dynamic metadata. PyTorch buffer pointer equality,
  full iteration, negative indices and writable copy mode pass. Read-only alias
  warnings are expected; test tensors are never mutated.
- All main long-file columns match across batches 2/32/64; all paired 330-second
  old/sequential/concurrent/profile/rendezvous outputs are exact. The 660-second
  old/new comparison is also checked after timing.

## Decoder-thread experiments

The wrapper now sets nonzero thread count before Initialize. Experimental modes
0/1 produced identical values on five valid short fixtures and cleanly rejected a
corrupt NAL. Counts 2/4 each timed out after 15 seconds on Baseline H.264; later cases
for those counts were quarantined, not reported as passes. Forced timeout can leave
partial files. Nonzero counts are rejected by the normal release before output
creation. Experimental results are tied to their separate binary hash in
`decoder-thread-experiments.json`; short experimental timings were not isolated
performance benchmarks.

## Delayed publication investigation

A delayed-reread test found truncated outputs in this runtime's workspace after
initial independent reads had succeeded. The unchanged v0.1.5 binary reproduced
that failure. Both old and new binaries passed 20 immediate and delayed checks
using /tmp. Final matrix and benchmark artifacts use /tmp, and all are independently
read. This points to an output-location-dependent environment issue; its underlying
mechanism has not been established. No reader gate was relaxed and no publication
workaround was added to the engine. See storage-diagnostics.json and preserved logs.


## Reproduce

```sh
cargo build --release --locked
cargo test --locked --all-targets
cargo fmt --check
cargo clippy --all-targets --locked -- -D warnings
cargo install cargo-deny --version 0.20.2 --locked
cargo deny check licenses
python3 -m pip install -r python/requirements-test.txt
python3 -m pip install torch==2.14.0+cpu --index-url https://download.pytorch.org/whl/cpu
python3 scripts/generate_matrix.py --long --extended-memory
# Optional output-filesystem override used for the final recorded runs:
export TENZOR_OUTPUT_ROOT=/tmp/tenzor-artifacts
python3 scripts/test_matrix.py
python3 scripts/test_epoch_selection.py --old-binary /path/to/v0.1.5/tenzor
python3 scripts/test_concurrency.py
python3 scripts/stress_publication.py --output-root /tmp
python3 scripts/benchmark_matrix.py
python3 scripts/verify_benchmarks.py
python3 scripts/benchmark_concurrency.py --old-binary /path/to/v0.1.5/tenzor
```

Evidence includes matrix.json, verification-summary.json, concurrency-tests.json,
epoch-selection-regression.json, unit-v016.log, benchmark-verification.json,
publication-new-tmp.json and decoder-thread-experiments.json. This is a fixture-based
release verification, not exhaustive codec conformance, leak proof or security audit.
