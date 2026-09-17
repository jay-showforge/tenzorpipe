# BUILD_STATUS — TenzorPipe 0.1.6

**Compiled and independently verified for the documented scope.**
Binary SHA-256: `0e1a46ca8e67d872ac95d46b6541a283737d34dda6f27aaadaf6d9e6ad93f2d7`. Verification time: 2026-09-16T15:54:04.098804+00:00.

The default release runs two audio/video workers with a single Arrow collector.
Sequential mode is retained. Queue payload is bounded by bytes and epoch slots,
independently of the Arrow batch size. Workers broadcast cancellation, unblock
channels and join before returning. Release panics now unwind so Rust worker and
collector panics can become errors; native faults cannot be recovered this way.

## Build and gates

- Rust 1.98.1, Cargo.lock, opt-level 3, one codegen unit, LTO disabled, panic=unwind.
- OpenH264 0.9.8 source build with NASM 2.16.01 and BSD-licensed wrapper patch.
- 17 unit tests; 16 independent media cases; 12 original malformed-input cases.
- 41 exact v0.1.5 comparisons; 38 concurrency/budget/failure checks.
- Formatting, Clippy with warnings denied, and cargo-deny licenses: PASS.
- 12 main and 9 additional measured artifacts verified; stage profiling recorded.
- Linux host: Linux-6.18.44-x86_64-with-glibc2.39; AMD EPYC 9V74 80-Core Processor.
- Python 3.12.14 (main, Aug 25 2026, 14:00:49) [Clang 22.1.3 ]; dependencies pinned in python/requirements-test.txt.

The binary requires normal Linux C/C++ runtimes and has no FFmpeg runtime dependency.
Only this host has been executed; cross-platform or older-glibc compatibility is not claimed.
The reset scratch environment required restoring the saved release and reinstalling
Rust, NASM and test dependencies. Initial compiler/lifetime and Clippy errors were
repaired. A generated long fixture failed container validation and was regenerated;
benchmark sources were probed before use.

## Reproduce

Install a C/C++ compiler, NASM, FFmpeg, Python 3.12 and Rustup. On Debian/Ubuntu,
`sudo apt-get install build-essential nasm ffmpeg python3-venv` supplies system tools.
The pinned toolchain installs with Rustup. Final builds used CARGO_BUILD_JOBS=2.

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

## Storage investigation

A delayed-reread test found truncated outputs in this runtime's workspace after
initial independent reads had succeeded. The unchanged v0.1.5 binary reproduced
that failure. Both old and new binaries passed 20 immediate and delayed checks
using /tmp. Final matrix and benchmark artifacts use /tmp, and all are independently
read. This points to an output-location-dependent environment issue; its underlying
mechanism has not been established. No reader gate was relaxed and no publication
workaround was added to the engine. See storage-diagnostics.json and preserved logs.


## Remaining limitations

- Linux x86-64 CPU release only; no Windows/macOS/ARM/GPU binary verified.
- Progressive 8-bit YUV420 H.264 Baseline/High, AAC-LC in MP4, and tested WAV PCM16/float32. No WebM/AV1/VP9/FLAC/MP3/HEVC claims.
- One video and one audio track maximum; unsupported color formats, HDR, changing SPS/PPS and complex edits are rejected. General fragmented MP4, rotation and arbitrary VFR conformance are not established.
- Millisecond-rounded timestamps; square nearest-neighbor resizing; arithmetic channel downmix. Existing sinc/STFT/Mel semantics are preserved exactly.
- Source input must remain immutable while mapped. MP4 index metadata is capped at 16 MiB; Arrow footer metadata grows with batch count. Bounded queues do not imply identical RSS across runs or unlimited file duration.
- Ordinary failures remove partial output. Files are visible while writing; consumers wait for successful exit. Native crashes, forced termination and external storage faults can leave incomplete files.
- Experimental decoder threading is unavailable in the normal binary. Thread counts 2/4 timed out in controlled subprocesses; count 1 passing short fixtures is not a general safety/performance claim.
- Zero-copy PyTorch aliases read-only Arrow memory; use copy=True for mutation. CUDA transfer is not zero-copy.
- Project-specific BUSL-1.1 licensor, change date and additional-use terms remain unresolved. No commercial terms were invented. Dependency checks do not resolve project licensing or patent rights.
