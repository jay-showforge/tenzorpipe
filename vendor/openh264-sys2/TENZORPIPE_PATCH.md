# Local openh264-sys2 build patch

Source: openh264-sys2 0.9.8 (crates.io), BSD-2-Clause, bundling OpenH264 upstream source.
`build.rs` is changed, and three codec files gain a runtime-dispatched 8x8 transform kernel
(items 3 and 4 below). Everything else in `upstream/` is untouched.

1. Define `HAVE_AVX2` for both the NASM objects and the C++ sources on x86/x86_64,
   matching upstream OpenH264's `build/x86-common.mk`. Without it the AVX2 IDCT
   (`IdctResAddPred_avx2`, `IdctFourResAddPred_avx2`) and AVX2 luma motion-compensation
   routines were compiled out. Dispatch remains runtime CPUID-based, so CPUs without
   AVX2 keep the SSE2/SSSE3/SSE4.1 paths. H.264 reconstruction is integer-exact, and
   TenzorPipe's identity gates compare output bytes against the unpatched release.
2. A failed NASM compilation now fails the build instead of silently producing a C-only
   decoder. Set `OPENH264_ALLOW_C_FALLBACK=1` (or upstream's `OPENH264_NO_ASM=1`) to opt out.
3. `cargo:rerun-if-changed=upstream` (and `build.rs`) is emitted. Without it cargo never
   notices an edit under `upstream/`, relinks the previously compiled decoder objects and
   silently ships the old kernels.
4. **Runtime-dispatched SIMD for the 8x8 inverse transform.** Upstream has MMX/SSE2/AVX2
   assembly for the 4x4 transform and only C for the 8x8 one, which High-profile streams use
   for most residual blocks; it measures 4.30% of all decoder instructions. Added:
   - `codec/decoder/core/src/decode_mb_aux_simd.inc` — the 128-bit integer body, included once
     per dispatch target. Every intermediate is int16 and wraps exactly as the reference does;
     only the final `(32 + residual) >> 6` is widened, because the reference evaluates that one
     expression in `int`, and `packus`'s 0..255 saturation is exactly `WelsClip1`.
   - `codec/decoder/core/src/decode_mb_aux_simd.cpp` — emits `IdctResAddPred8x8_sse2` and
     `IdctResAddPred8x8_avx2` via `__attribute__((target(...)))`. Nothing is emitted on MSVC or
     on non-x86, where the C version is kept.
   - `codec/decoder/core/inc/decode_mb_aux.h`, `codec/decoder/core/src/decoder.cpp` —
     declarations and CPUID dispatch, in the `WELS_CPU_SSE2` and `WELS_CPU_AVX2` blocks
     OpenH264 already uses, so binaries still run on pre-AVX2 CPUs.

   Measured: −2.73% of first-pass instructions (5,052,675,826 → 4,914,597,280 on a 2 s 1080p
   clip at one worker), with every artifact digest unchanged.
   `scripts/test_idct8x8_simd.sh` compares both clones against the real upstream C function
   over a million random, sparse, DC-only and saturation-corner blocks, since artifact digests
   alone would never exercise the SSE2 clone on an AVX2 machine.
