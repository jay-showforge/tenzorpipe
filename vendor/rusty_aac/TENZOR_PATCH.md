Based on crates.io rusty_aac 0.5.0 (Apache-2.0); upstream license retained.
The decoder originally evaluated its IMDCT with O(M^2) repeated cosine calls.
TenzorPipe replaces only this transform for AAC's 1024/128-coefficient blocks
with an 8M-point, f64 RustFFT embedding. The identity and normalization are
explained next to the implementation. The original direct sum remains as a
reference/fallback. Static FFT plans and per-call bounded buffers are thread-safe.
The hidden public dsp module allows the root test suite to compare both formulas
on dense/sparse long/short spectra, independently of decoding tests.
Encoder, Huffman, windowing, overlap-add and channel algorithms are unchanged.
Full-media correctness is checked against FFmpeg in scripts/test_matrix.py.


## TenzorPipe 0.2.0 performance patch (output bit-identical)

Profiling showed AAC decode, not downmix, dominated audio time. Changes:

- `huffman.rs`: each codebook lazily builds an 11-bit lookup table that returns
  exactly what the original bit-by-bit scan returns (shortest matching length,
  lowest index). Longer codewords, invalid prefixes and streams with fewer than
  11 remaining bits take the original scan (`decode_scan`), so symbols, bit
  positions and errors are unchanged. Tested against the scan for every book on
  valid, random and truncated streams.
- `bits.rs`: `read_bits` reads whole words when all requested bits are present;
  short reads use the original loop, preserving error behaviour. Tested at every
  start position and width 0..=32.
- `dsp.rs`: `dequant` (|q| < 8192) and `sf_gain` (-1024..1024) come from tables
  filled with the identical `powf` expressions; tested bit-for-bit over wide
  ranges. The IMDCT reuses per-thread FFT and scratch buffers; the plan, inputs
  and output arithmetic are unchanged.
