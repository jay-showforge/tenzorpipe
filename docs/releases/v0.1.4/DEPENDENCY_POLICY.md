# Dependency policy — 0.1.4

The engine's dependency graph permits only MIT, Apache-2.0, BSD-2-Clause,
BSD-3-Clause, ISC, Unicode-3.0, Zlib and CC0-1.0. Unlisted licenses fail closed.
GPL, LGPL, AGPL, SSPL and other copyleft/prohibited dependencies are not authorized.
`cargo deny check licenses` must pass on the committed Cargo.lock.

## Changes

- OpenH264 0.9.8 bindings and openh264-sys2 0.9.8 use BSD-2-Clause. The codec is
  built from source, not loaded from a downloaded binary. Its source license and
  third-party notices must accompany redistributed binaries.
- `rust_h264` 0.4.0 (MIT OR Apache-2.0) is locally patched only to expose SPS VUI
  metadata. Its failed Baseline decoder is unused. Original license files and
  a patch explanation are retained in `vendor/rust_h264/`.
- `rusty_aac` 0.5.0 is Apache-2.0. A local patch replaces its quadratic IMDCT
  with a numerically verified RustFFT calculation; the original reference and
  license remain in `vendor/rusty_aac/`. RustFFT is already an allowed dependency.
- MP4 parsing uses `mp4io` 0.1.2
  (MIT OR Apache-2.0); WAV uses `hound` 3.5.1 (Apache-2.0).
- Arrow default CSV/JSON features are disabled; IPC is enabled explicitly.
- CC0-1.0 is allowed for Arrow's transitive `tiny-keccak`; CC0 is a permissive
  dedication, not a copyleft exception.
- The project itself retains the supplied BUSL-1.1 identifier. An exact
  package-and-version exception applies only to `tenzor-pipe =0.1.4`. BUSL is
  **not** added to the dependency allowlist. This does not relicense the project.

## Reproduce

```sh
cargo install cargo-deny --version 0.20.2 --locked
cargo deny check licenses
cargo metadata --locked --format-version 1 > evidence/cargo-metadata.json
```

The full dependency inventory and license scan output are included in `evidence/`.
`THIRD_PARTY_NOTICES/` contains available license and notice texts for the locked
dependencies, including OpenH264's upstream source notices.

FFmpeg, its encoders, and Python verification tools are external test/baseline
tools. They are not linked into the engine and their executables are not bundled.
Generated media is synthetic; no third-party footage is needed to reproduce tests.

The supplied archive provided no complete BUSL license text or project-specific
change terms. This release preserves that state and identifies it as a remaining
publication limitation rather than inventing licensing terms. Dependency license
checks do not establish patent rights or resolve the project's own release terms.
