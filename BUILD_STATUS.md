# BUILD_STATUS — TenzorPipe 0.2.0, independently verified EPYC release

**PASS within the documented scope.** Binary SHA-256: `c914ddbd5663c9837c501020e58f98b1cdb150708ae0b37fcbc5df8374858b77`.
The bundled current binary is rebuilt with Rust 1.98.1, not the supplied Rust 1.95 binary.
The v0.1.9 comparator is the earlier Rust 1.98.1 release with SHA-256
`675c64e599131174ac57d13e9561fdce3ecebbe31056219a932d47bd14cfe2c3`. Both builds use source OpenH264 with NASM.

The production Rust engine and audio math are unchanged from the supplied v0.2.0.
The release was rebuilt with pinned Rust 1.98.1 and NASM. Repairs were to verification
and packaging: update the root-only license exception from =0.1.9 to =0.2.0; make
identity/fuzz failures return nonzero; require generated valid media to actually
decode; stream artifact hashing; test real write-limit failure; restore the full
PyTorch gate; replace stale reports and packaging assumptions with current evidence.

The initial generated noise fixture lacked a moov box. Both engines rejected it,
which was agreement but not decode success. It was regenerated through /tmp,
FFprobe-checked and all 168 generated-media comparisons were rerun successfully.
The first EFBIG limit was above the small WAV artifact size; it was lowered so all
chosen files actually encounter the error. These failed attempts are retained under
evidence/v020-review/. No production validation or numerical tolerance was relaxed.


## Completed gates

- `cargo build --release --locked`, format, Clippy with warnings denied, cargo-deny licenses: PASS.
- 30 engine tests. Vendored AAC: 94 pass and 4 ignored with default features, and 94 pass / 4 ignored with the engine's no-default-features configuration. These are the same 94 tests, not 188 distinct tests.
- 16 independent real-media cases, 12 invalid-input cases and the full PyArrow/PyTorch loader gate.
- 41 exact epoch-selection, 38 concurrency and 30 chunk-boundary/failure checks.
- 168 successful generated-audio comparisons; 336 successful repository comparisons +60 expected-error agreements. 92 corruption/truncation agreements and 12 paired EFBIG write failures.
- 80 timed and independently checked artifacts: 11 warm-ups, 55 repeated unprofiled measurements and 14 separate diagnostic/short runs. 25 retained artifacts pass delayed rereads and the independent baseline/source comparison.
- Four memory runs through 22 minutes with exact old/new logical-column identity, 20 immediate/delayed publication rereads and three dynamic-libc writable-open audits.

CLI defaults remain one video worker and concurrent audio processing with the new
bounded decode thread. On this host, `--video-workers 6` is the smallest measured
worker count within 5% of the fastest median. This is an explicit workload/resource
recommendation; automatic core-count selection is not universally optimal.

Host: Linux-6.18.44-x86_64-with-glibc2.39; AMD EPYC 9V74 80-Core Processor; 9 allowed
logical CPUs. Compiler/lockfile, third-party notices, source patches, exact input
inventory and machine-readable evidence are included. Historical Claude and v0.1.9
reports are under docs/releases; their timings are not current EPYC measurements.

## Reproduce

```sh
# Linux x86-64: extract the complete ZIP (or both split ZIPs), then cd tenzorpipe-v0.2.0.
# Install a C/C++ compiler, NASM, FFmpeg, Python 3.12 and Rustup.
rustup toolchain install 1.98.1 --profile minimal --component rustfmt,clippy
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r python/requirements-test.txt
python -m pip install torch==2.14.0+cpu --index-url https://download.pytorch.org/whl/cpu
cargo install cargo-deny --version 0.20.2 --locked
bash scripts/reproduce.sh
```

The script contains every exact build, fixture, correctness, benchmark and audit
command in execution order. It uses CARGO_BUILD_JOBS=2 and /tmp output by default.
Allow about 15 GiB free for generated media, retained outputs and build caches.
Reference binaries are included in the binaries archive. Large audio fixtures and
long video are regenerated. The normal engine has no FFmpeg runtime dependency.


Earlier workspace delayed-truncation behavior remains an environment observation,
not a resolved engine defect. Current benchmark outputs use /tmp and pass independent
immediate/delayed reads. Instrumentation cannot establish a universal storage guarantee.

## Known limitations

- Tested Linux x86-64 CPU release; Windows, macOS, ARM, GPU and older-glibc compatibility are unverified.
- Progressive 8-bit YUV420 H.264 Baseline/High plus AAC-LC in MP4, and tested WAV PCM16/PCM24/float32. No WebM, AV1, VP9, HEVC, FLAC or MP3 claim.
- One audio/video track maximum. Unsupported color formats/HDR/changing SPS and complex edit lists are rejected. General fragmented MP4, rotation and arbitrary VFR conformance are not established.
- Millisecond timestamps, nearest-neighbor square resizing and arithmetic downmix; established sinc, STFT and Mel semantics remain unchanged.
- Chunking falls back for insufficient clean IDR cuts, duplicate PTS, insufficient buffer budget or excessive planning metadata. Input must remain immutable while mapped.
- MP4 index allocation is capped at 16 MiB and planning vector payload at 32 MiB. Arrow footer/index metadata grows with duration/batch count. Queue bounds exclude native decoder state, allocator overhead and working/batch tensors.
- Rust errors/panics cancel and join workers; native crashes, hung native calls, forced termination and storage faults remain process-level limitations. Consumers wait for successful exit; a hard kill can leave a partial file.
- Nonzero internal OpenH264 threading is experimental and disabled in the normal binary. The tested parallelism uses independent decoder instances.
- Zero-copy-compatible loading refers to CPU Arrow/PyTorch buffer aliasing, not decode/preprocessing, serialization or GPU transfer. Use copy=True before mutating tensors.
- Byte identity is demonstrated against the pinned v0.1.9 Linux binary on these fixtures, not guaranteed across every architecture/compiler/codec input. Four vendor tests are explicitly ignored, not passes.
- BUSL-1.1 owner-specific license/change/use terms remain unresolved. No project commercial rights or codec patent determination is implied by dependency license checks.
