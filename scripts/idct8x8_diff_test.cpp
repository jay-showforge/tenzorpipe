// Differential test: the vendored SIMD 8x8 inverse transform against the scalar
// reference it replaces, over random and adversarial residual blocks.
//
// Built and run by scripts/test_idct8x8_simd.sh. The scalar side is the real
// upstream translation unit (codec/decoder/core/src/decode_mb_aux.cpp), not a copy,
// so the two cannot drift apart; the SIMD side is the same .inc the decoder builds.
//
// The kernel is spec-defined integer arithmetic, so "close enough" is not a passing
// result: every one of the 64 reconstructed samples must match exactly, and the
// residual buffer must come back unmodified (the decoder reuses it).

#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <random>

#include <emmintrin.h>
#include <immintrin.h>

#include "decode_mb_aux.h"

namespace WelsDec {
#define IDCT8X8_FN IdctResAddPred8x8_sse2_test
#define IDCT8X8_TARGET __attribute__ ((target ("sse2")))
#include "decode_mb_aux_simd.inc"

#define IDCT8X8_FN IdctResAddPred8x8_avx2_test
#define IDCT8X8_TARGET __attribute__ ((target ("avx2")))
#include "decode_mb_aux_simd.inc"
} // namespace WelsDec

int main (int argc, char** argv) {
  const long kIterations = argc > 1 ? atol (argv[1]) : 1000000;
  std::mt19937_64 rng (20260919);
  // Strides the decoder actually uses are large and even; vary them anyway, including
  // odd ones, so an accidental alignment assumption in the loads or stores shows up.
  const int16_t kExtremes[] = {0, 1, -1, 255, -255, 32767, -32768, 32735, 32736,
                               16384, -16384, 4095, -4096, 2048, -2048};
  const int kExtremeCount = (int) (sizeof (kExtremes) / sizeof (*kExtremes));
  long bad = 0;

  for (long it = 0; it < kIterations; it++) {
    const int stride = 16 + (int) (rng() % 33);
    const int mode = (int) (it % 5);
    int16_t rs[64], rsA[64], rsB[64];
    for (int i = 0; i < 64; i++) {
      int16_t v;
      switch (mode) {
        case 0: v = (int16_t) ((rng() % 512) - 256); break;               // typical residual
        case 1: v = (int16_t) (rng() & 0xFFFF); break;                    // whole int16 range
        case 2: v = kExtremes[rng() % kExtremeCount]; break;              // saturation corners
        case 3: v = (rng() % 4) ? 0 : (int16_t) (rng() & 0xFFFF); break;  // sparse, as coded blocks are
        default: v = (i == 0) ? (int16_t) (rng() & 0xFFFF) : 0; break;    // DC only
      }
      rs[i] = rsA[i] = rsB[i] = v;
    }
    uint8_t ref[8 * 64], a[8 * 64], b[8 * 64];
    for (int i = 0; i < 8 * 64; i++) ref[i] = a[i] = b[i] = (uint8_t) (rng() & 0xFF);

    WelsDec::IdctResAddPred8x8_c (ref, stride, rs);
    WelsDec::IdctResAddPred8x8_sse2_test (a, stride, rsA);
    WelsDec::IdctResAddPred8x8_avx2_test (b, stride, rsB);

    const bool same = !memcmp (ref, a, sizeof ref) && !memcmp (ref, b, sizeof ref) &&
                      !memcmp (rs, rsA, sizeof rs) && !memcmp (rs, rsB, sizeof rs);
    if (!same && ++bad <= 3) {
      printf ("MISMATCH iteration %ld (stride %d, input mode %d)\n", it, stride, mode);
      for (int i = 0; i < 8; i++) {
        printf ("  row %d   c:", i);
        for (int j = 0; j < 8; j++) printf (" %3u", ref[i * stride + j]);
        printf ("   sse2:");
        for (int j = 0; j < 8; j++) printf (" %3u", a[i * stride + j]);
        printf ("   avx2:");
        for (int j = 0; j < 8; j++) printf (" %3u", b[i * stride + j]);
        printf ("\n");
      }
    }
  }
  printf ("%s: %ld blocks, %ld mismatches (sse2 and avx2 clones vs IdctResAddPred8x8_c)\n",
          bad ? "FAIL" : "PASS", kIterations, bad);
  return bad ? 1 : 0;
}
