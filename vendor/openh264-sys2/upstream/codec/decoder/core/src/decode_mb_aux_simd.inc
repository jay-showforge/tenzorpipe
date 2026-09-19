/*!
 * TenzorPipe addition to OpenH264 (BSD-2-Clause, see the licence header of the
 * surrounding sources).
 *
 * 8x8 inverse transform and reconstruction, 128-bit integer SIMD.
 *
 * Upstream ships only IdctResAddPred8x8_c for this kernel on x86 — the 4x4
 * transform has MMX/SSE2/AVX2 assembly, the 8x8 one has a C version and a
 * LoongArch LSX version. High-profile streams use the 8x8 transform, where the
 * scalar version measures 4.3% of all decoder instructions.
 *
 * This file is included once per dispatch target. IDCT8X8_FN names the function
 * and IDCT8X8_TARGET carries its target attribute; both are #undef'd at the end.
 *
 * Bit-exactness: every intermediate in the reference is int16_t and wraps on
 * assignment, and every shift is applied to an already-truncated int16_t, so
 * 16-bit SIMD lanes reproduce it exactly. Only the final (32 + residual) >> 6 is
 * widened to 32 bits, because the reference evaluates that one expression in
 * `int`, and packus's 0..255 saturation is exactly WelsClip1.
 */

#define IDCT8_BUTTERFLY(q0, q1, q2, q3, q4, q5, q6, q7, o0, o1, o2, o3, o4, o5, o6, o7) \
  do {                                                                                  \
    __m128i e0 = _mm_add_epi16 (q0, q4);                                                \
    __m128i e1 = _mm_sub_epi16 (q0, q4);                                                \
    __m128i e2 = _mm_sub_epi16 (q6, _mm_srai_epi16 (q2, 1));                            \
    __m128i e3 = _mm_add_epi16 (q2, _mm_srai_epi16 (q6, 1));                            \
    __m128i f0 = _mm_add_epi16 (e0, e3);                                                \
    __m128i f2 = _mm_sub_epi16 (e1, e2);                                                \
    __m128i f4 = _mm_add_epi16 (e1, e2);                                                \
    __m128i f6 = _mm_sub_epi16 (e0, e3);                                                \
    __m128i g0 = _mm_sub_epi16 (_mm_sub_epi16 (_mm_sub_epi16 (q5, q3), q7),             \
                                _mm_srai_epi16 (q7, 1));                                \
    __m128i g1 = _mm_sub_epi16 (_mm_sub_epi16 (_mm_add_epi16 (q1, q7), q3),             \
                                _mm_srai_epi16 (q3, 1));                                \
    __m128i g2 = _mm_add_epi16 (_mm_add_epi16 (_mm_sub_epi16 (q7, q1), q5),             \
                                _mm_srai_epi16 (q5, 1));                                \
    __m128i g3 = _mm_add_epi16 (_mm_add_epi16 (_mm_add_epi16 (q3, q5), q1),             \
                                _mm_srai_epi16 (q1, 1));                                \
    __m128i f1 = _mm_add_epi16 (g0, _mm_srai_epi16 (g3, 2));                            \
    __m128i f3 = _mm_add_epi16 (g1, _mm_srai_epi16 (g2, 2));                            \
    __m128i f5 = _mm_sub_epi16 (g2, _mm_srai_epi16 (g1, 2));                            \
    __m128i f7 = _mm_sub_epi16 (g3, _mm_srai_epi16 (g0, 2));                            \
    o0 = _mm_add_epi16 (f0, f7);                                                        \
    o1 = _mm_sub_epi16 (f2, f5);                                                        \
    o2 = _mm_add_epi16 (f4, f3);                                                        \
    o3 = _mm_add_epi16 (f6, f1);                                                        \
    o4 = _mm_sub_epi16 (f6, f1);                                                        \
    o5 = _mm_sub_epi16 (f4, f3);                                                        \
    o6 = _mm_add_epi16 (f2, f5);                                                        \
    o7 = _mm_sub_epi16 (f0, f7);                                                        \
  } while (0)

/* Transpose an 8x8 block of int16 held one row per register. */
#define IDCT8_TRANSPOSE(r0, r1, r2, r3, r4, r5, r6, r7) \
  do {                                                  \
    __m128i t0 = _mm_unpacklo_epi16 (r0, r1);           \
    __m128i t1 = _mm_unpackhi_epi16 (r0, r1);           \
    __m128i t2 = _mm_unpacklo_epi16 (r2, r3);           \
    __m128i t3 = _mm_unpackhi_epi16 (r2, r3);           \
    __m128i t4 = _mm_unpacklo_epi16 (r4, r5);           \
    __m128i t5 = _mm_unpackhi_epi16 (r4, r5);           \
    __m128i t6 = _mm_unpacklo_epi16 (r6, r7);           \
    __m128i t7 = _mm_unpackhi_epi16 (r6, r7);           \
    __m128i s0 = _mm_unpacklo_epi32 (t0, t2);           \
    __m128i s1 = _mm_unpackhi_epi32 (t0, t2);           \
    __m128i s2 = _mm_unpacklo_epi32 (t1, t3);           \
    __m128i s3 = _mm_unpackhi_epi32 (t1, t3);           \
    __m128i s4 = _mm_unpacklo_epi32 (t4, t6);           \
    __m128i s5 = _mm_unpackhi_epi32 (t4, t6);           \
    __m128i s6 = _mm_unpacklo_epi32 (t5, t7);           \
    __m128i s7 = _mm_unpackhi_epi32 (t5, t7);           \
    r0 = _mm_unpacklo_epi64 (s0, s4);                   \
    r1 = _mm_unpackhi_epi64 (s0, s4);                   \
    r2 = _mm_unpacklo_epi64 (s1, s5);                   \
    r3 = _mm_unpackhi_epi64 (s1, s5);                   \
    r4 = _mm_unpacklo_epi64 (s2, s6);                   \
    r5 = _mm_unpackhi_epi64 (s2, s6);                   \
    r6 = _mm_unpacklo_epi64 (s3, s7);                   \
    r7 = _mm_unpackhi_epi64 (s3, s7);                   \
  } while (0)

/* Reconstruct one row: (32 + residual) >> 6, add the prediction, clip to 0..255.
   The shift is done in 32-bit lanes because the reference evaluates 32 + residual
   in `int`, and packus's saturation is exactly WelsClip1. */
#define IDCT8_STORE_ROW(v, i)                                                        \
  do {                                                                               \
    const __m128i kLo = _mm_srai_epi32 (_mm_unpacklo_epi16 (kZero, v), 16);          \
    const __m128i kHi = _mm_srai_epi32 (_mm_unpackhi_epi16 (kZero, v), 16);          \
    const __m128i kRes = _mm_packs_epi32 (_mm_srai_epi32 (_mm_add_epi32 (kLo, kRound), 6), \
                                          _mm_srai_epi32 (_mm_add_epi32 (kHi, kRound), 6)); \
    uint8_t* pRow = pPred + (i) * kiStride;                                          \
    const __m128i kPred = _mm_unpacklo_epi8 (_mm_loadl_epi64 ((const __m128i*) pRow), kZero); \
    _mm_storel_epi64 ((__m128i*) pRow, _mm_packus_epi16 (_mm_add_epi16 (kRes, kPred), kZero)); \
  } while (0)

IDCT8X8_TARGET
void IDCT8X8_FN (uint8_t* pPred, const int32_t kiStride, int16_t* pRs) {
  const __m128i kZero = _mm_setzero_si128();
  const __m128i kRound = _mm_set1_epi32 (32);

  __m128i r0 = _mm_loadu_si128 ((const __m128i*) (pRs + 0 * 8));
  __m128i r1 = _mm_loadu_si128 ((const __m128i*) (pRs + 1 * 8));
  __m128i r2 = _mm_loadu_si128 ((const __m128i*) (pRs + 2 * 8));
  __m128i r3 = _mm_loadu_si128 ((const __m128i*) (pRs + 3 * 8));
  __m128i r4 = _mm_loadu_si128 ((const __m128i*) (pRs + 4 * 8));
  __m128i r5 = _mm_loadu_si128 ((const __m128i*) (pRs + 5 * 8));
  __m128i r6 = _mm_loadu_si128 ((const __m128i*) (pRs + 6 * 8));
  __m128i r7 = _mm_loadu_si128 ((const __m128i*) (pRs + 7 * 8));

  /* Horizontal pass. Transposing first puts element j of every row into register
     j, so a single butterfly covers all eight rows at once. */
  IDCT8_TRANSPOSE (r0, r1, r2, r3, r4, r5, r6, r7);
  __m128i h0, h1, h2, h3, h4, h5, h6, h7;
  IDCT8_BUTTERFLY (r0, r1, r2, r3, r4, r5, r6, r7, h0, h1, h2, h3, h4, h5, h6, h7);

  /* The vertical pass reads columns of the horizontal result, one transpose away. */
  IDCT8_TRANSPOSE (h0, h1, h2, h3, h4, h5, h6, h7);
  __m128i v0, v1, v2, v3, v4, v5, v6, v7;
  IDCT8_BUTTERFLY (h0, h1, h2, h3, h4, h5, h6, h7, v0, v1, v2, v3, v4, v5, v6, v7);

  /* Unrolled: an indexed loop over an eight-register array spills it to the stack
     and costs more than the transform itself. */
  IDCT8_STORE_ROW (v0, 0);
  IDCT8_STORE_ROW (v1, 1);
  IDCT8_STORE_ROW (v2, 2);
  IDCT8_STORE_ROW (v3, 3);
  IDCT8_STORE_ROW (v4, 4);
  IDCT8_STORE_ROW (v5, 5);
  IDCT8_STORE_ROW (v6, 6);
  IDCT8_STORE_ROW (v7, 7);
}

#undef IDCT8X8_FN
#undef IDCT8X8_TARGET
#undef IDCT8_BUTTERFLY
#undef IDCT8_TRANSPOSE
#undef IDCT8_STORE_ROW
