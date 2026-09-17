# Local openh264-sys2 build patch

Source: openh264-sys2 0.9.8 (crates.io), BSD-2-Clause, bundling OpenH264 upstream source.
Only `build.rs` is changed; no codec source is modified.

1. Define `HAVE_AVX2` for both the NASM objects and the C++ sources on x86/x86_64,
   matching upstream OpenH264's `build/x86-common.mk`. Without it the AVX2 IDCT
   (`IdctResAddPred_avx2`, `IdctFourResAddPred_avx2`) and AVX2 luma motion-compensation
   routines were compiled out. Dispatch remains runtime CPUID-based, so CPUs without
   AVX2 keep the SSE2/SSSE3/SSE4.1 paths. H.264 reconstruction is integer-exact, and
   TenzorPipe's identity gates compare output bytes against the unpatched release.
2. A failed NASM compilation now fails the build instead of silently producing a C-only
   decoder. Set `OPENH264_ALLOW_C_FALLBACK=1` (or upstream's `OPENH264_NO_ASM=1`) to opt out.
