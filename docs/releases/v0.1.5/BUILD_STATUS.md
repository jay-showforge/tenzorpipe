# BUILD_STATUS — TenzorPipe 0.1.5

Status: **release compiled and independently verified** for the tested scope.
Verification timestamp: 2026-09-16T07:32:10.952894+00:00.

## Deliverables

- Complete source repository, Cargo.lock, pinned Rust toolchain, patched permissive dependencies.
- `bin/tenzor-linux-x86_64`: executed release binary, not a placeholder.
- `examples/short-224.tenzor`: an actual processed artifact.
- TEST_REPORT.md, BENCHMARK_REPORT.md, DEPENDENCY_POLICY.md and raw `evidence/`.
- Generator, verification, benchmark and CI scripts; short fixtures and reconstructed images.
- THIRD_PARTY_NOTICES and the exact dependency inventory.

Binary SHA-256: `ad8f8cdf7df93b3be2da5a6c5098196051c41a44f1784aadfdcb65da64dc249d`.

## Build environment and gates

Rust 1.98.1 (`48a229cea`, 2026-09-01); Cargo from that toolchain.
Release profile: opt-level 3, one codegen unit, ThinLTO disabled, panic=abort,
stripped symbols. OpenH264 0.9.8 is source-built with NASM 2.16.01.
Test host: Linux-6.18.44-x86_64-with-glibc2.39; AMD EPYC 9V74 80-Core Processor; 9 visible CPUs.
FFmpeg 6.1.1-3ubuntu5 is used only for fixtures, independent decoding and the baseline.
Python 3.12.14, PyArrow 25.0.1, NumPy 2.3.5 and PyTorch 2.14.0+cpu were used.

The binary dynamically needs the normal Linux C/C++ runtime, not FFmpeg. The
highest referenced GLIBC symbol version is 2.34; runtime testing used glibc 2.39.
Compatibility with older distributions has not been separately tested.

| Gate | Result |
|---|---|
| `cargo build --release --locked` | PASS; real release binary |
| `cargo test --all-targets --locked` | PASS; 13 tests |
| `cargo clippy --all-targets --locked -- -D warnings` | PASS |
| `cargo deny check licenses` | PASS; 133 dependency packages inventoried |
| Real-media / Python matrix | PASS; 16 cases |
| Deliberate failures | PASS; 12 cases |
| Benchmark artifact correctness | PASS; 12 artifacts; identical tensors across batch sizes |

## Repairs required

The first build attempt found no Cargo installed. Installing Rust exposed five
source/API errors: MP4 codec `Option` comparisons, string-error conversion for
AVC configuration, and decoder width/height types. These were repaired.
The initial pure-Rust H.264 decoder compiled but failed an independent
Baseline P-frame comparison (5.50/255 mean absolute pixel error); it was replaced
with OpenH264. AAC's direct quadratic inverse transform was replaced with a
numerically verified FFT equivalent. Audio buffering, timing, EOF behavior,
color handling, Arrow metadata and consumer iteration were repaired.

Version 0.1.5 moves nearest-epoch selection before conversion/resizing using a
34-entry timestamp lookahead. All compressed pictures still decode. Four new
unit tests and 41 exact old/new value comparisons guard midpoint ties, B-frame
ordering, sparse/irregular frames, non-default windows and audio tails.

ThinLTO builds intermittently failed with unresolved symbols in this environment;
release builds now disable it. Verification builds use one codegen unit and no
debug info to avoid excessive parallel object-file writes. Build logs include the
observed failures. A temporary-file publication path also left empty output in
observed runs; it was replaced with direct no-overwrite creation, error cleanup,
sync/close and reopening of the completed IPC file. Final artifacts were then
read independently, including every long-file batch.

## Reproduce

Install Rustup, Rust 1.98.1, a C/C++ compiler, NASM, FFmpeg and Python 3.12.
On a conventional Debian/Ubuntu host: `sudo apt-get install build-essential nasm ffmpeg python3-venv`.
The supplied rust-toolchain.toml pins the toolchain. In this constrained build
host, NASM was extracted from Debian's nasm_2.16.01-1_amd64.deb into a local tools
directory after package-manager privilege changes were unavailable. Standard
NASM installations need no special override. `CARGO_BUILD_JOBS=2` was used for
the final build; it changes build concurrency, not engine execution.

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

`./scripts/reproduce.sh` combines the build, license, media and benchmark stages.
Long fixtures and large benchmark outputs are regenerated instead of packaged.
CI configuration is provided; this report records local execution, not a claim
that a remote CI service has already run.

## Known remaining limitations

- Verified on Linux x86-64 only, with synthetic fixtures. No Windows/macOS/ARM/GPU release has been tested.
- Tested video is progressive, 8-bit YUV420 H.264 Baseline and High Profile, including B-frames. Source images reach 720p in the matrix; the 4096×2160 coded-size guard is a limit, not a claim of tested 4K support.
- AAC-LC only; HE-AAC/SBR/PS is rejected. Tested rates are 16 kHz, 44.1 kHz and 48 kHz. WAV PCM16 and float32 are exercised; other accepted PCM depths/rates need additional fixtures.
- MP4 and WAV only. WebM, AV1, VP9, FLAC, MP3, HEVC and other codecs/containers are not claimed. Two sparse/irregular-timing fixtures have old/new equivalence tests. Fragmented MP4, arbitrary VFR conformance, rotation and complex edits are not claimed.
- One video track and one audio track at most. Complex/non-unit-rate edit lists and midstream parameter-set changes are rejected. Timestamps are rounded to milliseconds.
- SDR BT.601/BT.709 and full/limited range are handled; unspecified matrix defaults to BT.601. ICC/HDR/unsupported matrices are rejected. Square nearest-neighbor stretching is the current resize policy.
- Only selected pictures are converted/resized, with bounded timestamp lookahead. Every access unit still decodes. The pipeline remains principally single-threaded; no general speed advantage over FFmpeg is demonstrated.
- MP4 sample-index metadata grows with sample count within a 16 MiB parser budget; Arrow footer/index metadata grows with batch count. Tensor/audio queues are bounded. Very long inputs can hit parser limits; the measured plateau is not proof of constant memory for arbitrarily long files.
- The output path is reserved without overwrite and is visible during writing. Ordinary failures remove it; hard termination may leave incomplete IPC. Consumers must wait for successful process exit. Completed files are flushed, synced and reopened before success.
- Inputs must remain immutable while memory-mapped. Zero-copy PyTorch tensors alias read-only buffers; callers must not mutate them. Use `copy=True` for mutable transforms.
- The supplied project has a BUSL-1.1 identifier but no complete project-specific license/change terms. Those publication terms remain unresolved; no terms were invented or silently replaced.
