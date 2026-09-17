# BUILD_STATUS — TenzorPipe 0.1.9

**Compiled and independently verified within the documented scope.**
Linux x86-64 release binary SHA-256: `675c64e599131174ac57d13e9561fdce3ecebbe31056219a932d47bd14cfe2c3`.
Main media suite completed at 2026-09-16T20:01:53.159470+00:00; subsequent evidence is
included in this release. The supplied v0.1.8 was separately compiled before editing.

This release preserves Claude's chunked decoder architecture and incorporates the
v0.1.7 memory/cancellation work. It is a repair and verification release, not a rewrite.
Concurrent audio/video execution remains the default; video worker count remains
one. `--video-workers 2` is recommended on this measured host when the extra RAM is
acceptable. It is within 5% of the fastest measured worker setting and uses less RAM.

## Repairs

1. **Chunk-buffer overshoot:** the supplied scheduler released its admission credit before emitting the current chunk. A window of two could hold three chunk payloads. The new regression reproduced this failure. Credit now returns after emission and payload release; the bound includes the chunk being emitted.
2. **Emitter-panic deadlock:** a panic could bypass cancellation and strand workers behind the admission window during scoped joins. The regression timed out on the supplied code. The emitter now uses the shared panic-to-error boundary; scope guards cancel before joining. Partial thread-start failures also cancel already-started workers.
3. **Indexing memory:** the prepass now periodically releases mapped source pages, drops unused index vectors before decoding, and applies a conservative planning-payload cap before allocation. The earlier v0.1.7 residency diagnosis is preserved as history, not reused as current measurements.
4. **Chunk validity:** all AVCC NAL lengths and headers are checked before accepting a cut point, and every VCL NAL in the access unit must be IDR. Malformed tails and mixed IDR/non-IDR slices cannot establish a chunk start.
5. **Reproducibility:** failing identity/fuzz checks now fail their scripts; hashing is streamed; the root-only cargo-deny version exception is updated. No dependency or audio DSP algorithm was changed.

## Build and gates

- Rust 1.98.1, pinned Cargo.lock, source-built OpenH264 with NASM 2.16.01.
- opt-level=3, codegen-units=1, LTO disabled, panic=unwind; no FFmpeg engine dependency.
- 26 unit tests; format and Clippy checks; cargo-deny license check: PASS.
- 16 independent media cases, 12 original invalid-input cases; 41 exact-value regression checks; 38 concurrency checks; 30 chunk-boundary/failure checks.
- 760 matrix agreements: 600 successful artifact comparisons and 160 expected-error agreements. 177 runs actually entered chunked mode. 24 corrupted-input checks.
- 35 benchmark artifacts independently verified; four long-memory artifacts, including exact v0.1.6/v0.1.9 logical-column identity over 22 minutes.
- Three dynamic-libc writable-file audits and 20 immediate/delayed publication rereads.
- Host: Linux-6.18.44-x86_64-with-glibc2.39; AMD EPYC 9V74 80-Core Processor; 9 allowed logical CPUs. Python 3.12.14 (main, Aug 25 2026, 14:00:49) [Clang 22.1.3 ].

The linked system C/C++ runtime is required. No portability claim beyond the tested
host is implied. The pinned dependencies, vendor patches and third-party notices are
included. `source-v018-to-v019.patch` documents the engine changes against the supplied
archive. Reference binaries are for regression use only; deploy the current binary.

## Reproduce

```sh
# Linux x86-64; install C/C++ compiler, NASM, FFmpeg and strace first.
rustup toolchain install 1.98.1 --profile minimal --component rustfmt,clippy
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r python/requirements-test.txt
python -m pip install torch==2.14.0+cpu --index-url https://download.pytorch.org/whl/cpu
cargo install cargo-deny --version 0.20.2 --locked
bash scripts/reproduce.sh
```

`reproduce.sh` lists all build, test, fixture, benchmark and independent verification
commands in execution order. It sets CARGO_BUILD_JOBS=2 and uses /tmp outputs by
default; allow about 20 GiB free. The two reference binaries are bundled for the
exact old/new checks. Generated short fixtures are included; long media is regenerated.
Individual benchmark commands:

```sh
export TENZOR_OUTPUT_ROOT=/tmp/tenzor-artifacts
python3 scripts/benchmark_release.py --old-binary reference/bin/tenzor-v0.1.6-linux-x86_64 --v018-binary reference/bin/tenzor-v0.1.8-linux-x86_64
python3 scripts/memory_release.py --old-binary reference/bin/tenzor-v0.1.6-linux-x86_64
python3 scripts/verify_benchmarks.py --records release-benchmarks.json --output release-benchmark-verification.json
python3 scripts/audit_disk_writes.py
```

## Output-location history

Earlier verification found delayed truncation in workspace outputs with both old
and new engines; the root cause was not established. Final runs use /tmp. The current
binary passes 20 immediate and delayed independent rereads there. No reader gate was
relaxed and no filesystem workaround was added to the engine. This is an environment
observation, not proof of a universal storage guarantee.

## Known remaining limitations

- Linux x86-64 CPU binary tested on this host; other architectures, operating systems and older glibc are unverified.
- Tested scope: progressive 8-bit YUV420 H.264 Baseline/High and AAC-LC in MP4; WAV PCM16/float32. No WebM, AV1, VP9, HEVC, FLAC or MP3 support claim.
- One video/audio track maximum. Unsupported color formats, HDR, changing SPS/PPS and complex edits are rejected. General fragmented MP4, rotation and arbitrary VFR conformance are not established.
- Millisecond-rounded timestamps, nearest-neighbor square resizing and arithmetic channel downmix. Existing 129-tap sinc, STFT and Mel definitions are preserved.
- Chunking needs valid IDR starts and clean presentation-time boundaries. Open GOPs, duplicate PTS, insufficient budget or planning allowance cause documented single-decoder fallback.
- Planning metadata is O(sample count) within a conservative 32 MiB vector-payload allowance; allocator bookkeeping is additional. MP4 index metadata has a separate 16 MiB allocation cap. Arrow footer metadata grows with batch count. RSS is not mathematically constant with duration.
- Input files must remain immutable while memory-mapped. Tensor budgets exclude native decoder state, allocator overhead, working tensors and the Arrow batch.
- Ordinary errors clean partial output and unblock workers. Native faults, forced process termination and external storage failures can leave incomplete files. Consumers must wait for exit status 0.
- Nonzero internal OpenH264 threading remains experimental and disabled in the normal release; independent decoder instances provide the tested parallelism.
- Zero-copy compatibility refers to CPU consumer buffer wrapping, not decoding, preprocessing, Arrow construction or GPU transfer. PyTorch cannot enforce read-only Arrow memory; use copy=True before mutation.
- Project-specific BUSL-1.1 licensor, Change Date and Additional Use Grant remain unspecified. No example commercial terms were invented. Dependency license checks do not resolve project licensing or patent rights.
