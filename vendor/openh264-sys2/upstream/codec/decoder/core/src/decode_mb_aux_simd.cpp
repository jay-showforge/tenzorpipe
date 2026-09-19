/*!
 * TenzorPipe addition to OpenH264 (BSD-2-Clause, see the licence header of the
 * surrounding sources).
 *
 * Runtime-dispatched 8x8 inverse-transform kernels. Two clones of the same
 * 128-bit integer body are emitted, one for SSE2 and one for AVX2 (VEX encoding,
 * three-operand forms, no vzeroupper hazard because the body stays 128-bit).
 * decoder.cpp selects between them and the scalar fallback from the CPUID flags
 * OpenH264 already detects, so binaries keep running on pre-AVX2 CPUs.
 *
 * On compilers without GCC's function-level target attribute (MSVC), nothing is
 * emitted here and the decoder keeps using IdctResAddPred8x8_c.
 */

#include "decode_mb_aux.h"

#if defined(WELS_HAVE_IDCT8X8_SIMD)

#include <emmintrin.h>
#if defined(__GNUC__) || defined(__clang__)
#include <immintrin.h>
#endif

namespace WelsDec {

#define IDCT8X8_FN IdctResAddPred8x8_sse2
#define IDCT8X8_TARGET __attribute__ ((target ("sse2")))
#include "decode_mb_aux_simd.inc"

#define IDCT8X8_FN IdctResAddPred8x8_avx2
#define IDCT8X8_TARGET __attribute__ ((target ("avx2")))
#include "decode_mb_aux_simd.inc"

} // namespace WelsDec

#endif // WELS_HAVE_IDCT8X8_SIMD
