# DEPENDENCY_POLICY — TenzorPipe 0.2.0

**cargo deny check licenses: PASS** on the pinned runtime lockfile. The unused
BSD-3-Clause/ISC allowance warnings are retained. The supplied deny.toml still named
root v0.1.9; its exact root-only BUSL exception now names=0.2.0. The permitted dependency
license list and runtime dependency versions were not widened or changed.

Allowed: MIT, Apache-2.0, BSD-2-Clause, BSD-3-Clause, ISC, Unicode-3.0, Zlib and CC0-1.0.
Unlisted dependencies fail closed. GPL, LGPL, AGPL, SSPL and other prohibited/copyleft
dependencies are not authorized. BUSL-1.1 is not a dependency allowlist entry.

- Source-built OpenH264 wrapper/sys 0.9.8: BSD-2-Clause; retained NASM assembly and source notices. No downloaded codec binary or FFmpeg engine linkage.
- rusty_aac 0.5.0: Apache-2.0. Vendored Huffman, bit-reader, table and FFT-buffer patches retain the original references/tests and license.
- RustFFT 6.4.1: MIT OR Apache-2.0. No smaller-FFT algorithm or new DSP dependency was introduced.
- rust_h264 0.4.0: MIT OR Apache-2.0; used for patched SPS/VUI metadata, not its decoder.
- mp4io 0.1.2: MIT OR Apache-2.0; hound 3.5.1: Apache-2.0; crossbeam-channel 0.5.17: MIT OR Apache-2.0.
- The vendor AAC test-only rusty_alloc/rusty_alloc-api 0.3.2 dependencies declare MIT; they are not in the production engine graph. The vendor test lockfile is retained.
- Arrow defaults are disabled; IPC is explicit. CC0 covers transitive tiny-keccak.

THIRD_PARTY_NOTICES and vendor license files accompany the repository/binaries.
FFmpeg and Python are independent external test/baseline tools, not bundled engine
executables. Media is synthetic. The binary archive includes notices for redistribution.

```sh
cargo install cargo-deny --version 0.20.2 --locked
cargo deny check licenses
cargo metadata --locked --format-version 1 > evidence/cargo-metadata.json
```

**Project license remains incomplete.** BUSL-1.1 was supplied without finalized
owner-specific licensor, Change Date or Additional Use Grant. The earlier proposed
revenue/change terms were examples, not an owner decision; none were invented here.
`publish=false` remains. Dependency compliance is not a commercial-rights or codec
patent determination. This limitation remains before a commercial publication.
