# DEPENDENCY_POLICY — TenzorPipe 0.1.9

**cargo deny check licenses: PASS**, against the committed Cargo.lock. The only
warnings are unused BSD-3-Clause and ISC allowances. The dependency graph is unchanged
from supplied v0.1.8; only the root package version and its exact license exception
change to 0.1.9. No copyleft decoder or new dependency was introduced.

Allowed dependency licenses are MIT, Apache-2.0, BSD-2-Clause, BSD-3-Clause, ISC,
Unicode-3.0, Zlib and CC0-1.0. Unlisted licenses fail closed. GPL, LGPL, AGPL, SSPL
and other prohibited/copyleft dependencies are not authorized. BUSL-1.1 is an exact
exception for `tenzor-pipe =0.1.9` only, not a dependency allowlist entry.

- OpenH264 wrapper 0.9.8 and openh264-sys2 0.9.8 are BSD-2-Clause. The codec is compiled from source with NASM, not downloaded as a binary. Existing wrapper threading configuration patch is preserved; normal decoder threading is zero.
- rusty_aac 0.5.0 is Apache-2.0. The prior RustFFT IMDCT patch and original reference implementation are retained unchanged.
- rust_h264 0.4.0 is MIT OR Apache-2.0; the local patch exposes SPS/VUI metadata. Its decoder is unused.
- mp4io 0.1.2 is MIT OR Apache-2.0; hound 3.5.1 is Apache-2.0; crossbeam-channel 0.5.17 is MIT OR Apache-2.0.
- Arrow IPC is explicitly enabled with default features disabled. CC0-1.0 covers transitive tiny-keccak; this is a permissive dedication, not a copyleft exception.

THIRD_PARTY_NOTICES contains locked dependency notices, including OpenH264 source
notices; patched crates retain their licenses. FFmpeg and Python are external
verification/baseline tools, not linked into or bundled as engine dependencies.
Fixtures are synthetic and do not require third-party footage.

```sh
cargo install cargo-deny --version 0.20.2 --locked
cargo deny check licenses
cargo metadata --locked --format-version 1 > evidence/cargo-metadata.json
```

**Project license remains incomplete.** The supplied source specifies BUSL-1.1 but
provides no complete project-specific LICENSE with licensor, Change Date and
Additional Use Grant. The suggested four-year/Apache and revenue terms were examples,
not finalized owner terms; this engineering release does not invent them. `publish=false`
remains set. Dependency-policy compliance is not a determination of commercial rights,
project license completeness or codec patent rights. Final commercial release terms
still require the owner's decision.
