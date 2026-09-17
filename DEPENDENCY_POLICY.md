# DEPENDENCY_POLICY — TenzorPipe 0.3.0

**cargo deny check licenses: PASS** on the pinned lockfile. The unused BSD-3-Clause/ISC
allowance warnings are retained. The exact root-only BUSL exception names =0.3.0. The
permitted dependency license list was not widened.

Allowed: MIT, Apache-2.0, BSD-2-Clause, BSD-3-Clause, ISC, Unicode-3.0, Zlib and CC0-1.0.
Unlisted dependencies fail closed. GPL, LGPL, AGPL, SSPL and other prohibited/copyleft
dependencies are not authorized. BUSL-1.1 is not a dependency allowlist entry.

- Source-built OpenH264 wrapper/sys 0.9.8: BSD-2-Clause; both vendored (vendor/openh264, vendor/openh264-sys2) with TENZORPIPE_PATCH.md build notes; retained NASM assembly and source notices. No downloaded codec binary or FFmpeg engine linkage.
- rusty_aac 0.5.0: Apache-2.0. Vendored Huffman, bit-reader, table and FFT-buffer patches retain the original references/tests and license.
- RustFFT 6.4.1: MIT OR Apache-2.0. No smaller-FFT algorithm or new DSP dependency was introduced.
- rust_h264 0.4.0: MIT OR Apache-2.0; used for patched SPS/VUI metadata, not its decoder.
- mp4io 0.1.2: MIT OR Apache-2.0; hound 3.5.1: Apache-2.0; crossbeam-channel 0.5.17: MIT OR Apache-2.0.
- The vendor AAC test-only rusty_alloc/rusty_alloc-api 0.3.2 dependencies declare MIT; they are not in the production engine graph. The vendor test lockfile is retained.
- Arrow defaults are disabled; IPC is explicit. CC0 covers transitive tiny-keccak.
- PyO3 0.29.2 (MIT OR Apache-2.0) is used only by the Python extension (`bindings/python`), which never links libpython. The wheel carries LICENSE and THIRD_PARTY_NOTICES in its dist-info.
- Curated notices: flatbuffers (upstream Apache-2.0 LICENSE) and the openh264 wrapper, whose crate declares BSD-2-Clause without shipping a license file (see THIRD_PARTY_NOTICES/openh264-0.9.8/LICENSE).

THIRD_PARTY_NOTICES and vendor license files accompany the repository/binaries.
FFmpeg and Python are independent external test/baseline tools, not bundled engine
executables. Media is synthetic. The binary archive includes notices for redistribution.

```sh
cargo install cargo-deny --version 0.20.2 --locked
cargo deny check licenses
cargo metadata --locked --format-version 1 > evidence/cargo-metadata.json
```

**Project license:** Business Source License 1.1 (see LICENSE). Licensor Jonathan Tyler
Montgomery; Additional Use Grant for production use by individuals and legal entities with
annual gross revenue below US$100,000, aggregated across parents, subsidiaries and affiliates
under common control, excluding embedded hardware/OEM production use (commercial license:
licensing@tenzorpipe.org); Change Date 2030-09-16; Change License Apache-2.0. Third-party components keep their own licenses. `publish=false` remains for
crates.io. Dependency license compliance is not a codec patent determination: OpenH264 is
built from source, so Cisco's patent-license coverage for its prebuilt binaries does not apply
to these builds.
