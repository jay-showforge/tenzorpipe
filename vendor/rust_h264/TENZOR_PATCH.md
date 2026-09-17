Based on crates.io rust_h264 0.4.0, MIT OR Apache-2.0; upstream license files retained.
Local patch: src/sps.rs exposes VUI full-range, transfer and matrix fields that upstream
already read but discarded. Decoder algorithms are unchanged. Needed to avoid silently
converting BT.709/full-range inputs using BT.601 limited-range coefficients.
Upstream crate excludes its testdata; TenzorPipe verifies the decoder using independently
generated media and FFmpeg output in scripts/verify_media.py.
