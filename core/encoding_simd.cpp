// SPDX-License-Identifier: AGPL-3.0-or-later
// Compiled separately per ISA. Nothing in this TU runs before dispatch.
#include "encoding.hpp"
#if defined(BUGGCRAFT_SCAN_NEON)
#if defined(_MSC_VER) && !defined(__clang__)
#include <arm64_neon.h>
#else
#include <arm_neon.h>
#endif
#else
#include <immintrin.h>
#endif

namespace native::encoding::detail {
#if defined(BUGGCRAFT_SCAN_AVX512)
bool scan_avx512(std::string_view text, Alphabet alphabet) {
  constexpr size_t width = 64;
  size_t offset = 0;
  for (; text.size() - offset >= width; offset += width) {
    const auto x = _mm512_loadu_si512(text.data() + offset);
    auto range = [x](char lo, char hi) {
      return _mm512_cmpge_epu8_mask(x, _mm512_set1_epi8(lo)) &
             _mm512_cmple_epu8_mask(x, _mm512_set1_epi8(hi));
    };
    auto valid = range('A', 'Z') | range('a', 'z') | range('0', '9') |
                 _mm512_cmpeq_epi8_mask(x, _mm512_set1_epi8('+')) |
                 _mm512_cmpeq_epi8_mask(x, _mm512_set1_epi8('/'));
    if (alphabet == Alphabet::mixed_url)
      valid |= _mm512_cmpeq_epi8_mask(x, _mm512_set1_epi8('-')) |
               _mm512_cmpeq_epi8_mask(x, _mm512_set1_epi8('_'));
    if (valid != ~static_cast<__mmask64>(0))
      return false;
  }
  return valid_characters(text.substr(offset), alphabet, Backend::scalar);
}
#elif defined(BUGGCRAFT_SCAN_NEON)
bool scan_neon(std::string_view text, Alphabet alphabet) {
  constexpr size_t width = 16;
  size_t offset = 0;
  for (; text.size() - offset >= width; offset += width) {
    const auto x =
        vld1q_u8(reinterpret_cast<const uint8_t *>(text.data() + offset));
    auto range = [x](uint8_t lo, uint8_t hi) {
      return vandq_u8(vcgeq_u8(x, vdupq_n_u8(lo)), vcleq_u8(x, vdupq_n_u8(hi)));
    };
    auto valid =
        vorrq_u8(vorrq_u8(range('A', 'Z'), range('a', 'z')), range('0', '9'));
    valid = vorrq_u8(valid, vorrq_u8(vceqq_u8(x, vdupq_n_u8('+')),
                                     vceqq_u8(x, vdupq_n_u8('/'))));
    if (alphabet == Alphabet::mixed_url)
      valid = vorrq_u8(valid, vorrq_u8(vceqq_u8(x, vdupq_n_u8('-')),
                                       vceqq_u8(x, vdupq_n_u8('_'))));
    if (vminvq_u8(valid) != 255)
      return false;
  }
  return valid_characters(text.substr(offset), alphabet, Backend::scalar);
}
#else
#if defined(BUGGCRAFT_SCAN_AVX2)
#define SCAN_NAME scan_avx2
#define OP(x) _mm256_##x
#define LOAD(x) _mm256_loadu_si256(reinterpret_cast<const __m256i *>(x))
#define BIT_AND _mm256_and_si256
#define BIT_OR _mm256_or_si256
constexpr size_t width = 32;
constexpr int full_mask = -1;
#else
#define SCAN_NAME scan_sse2
#define OP(x) _mm_##x
#define LOAD(x) _mm_loadu_si128(reinterpret_cast<const __m128i *>(x))
#define BIT_AND _mm_and_si128
#define BIT_OR _mm_or_si128
constexpr size_t width = 16;
constexpr int full_mask = 65535;
#endif
bool SCAN_NAME(std::string_view text, Alphabet alphabet) {
  size_t offset = 0;
  for (; text.size() - offset >= width; offset += width) {
    const auto x = LOAD(text.data() + offset);
    auto range = [x](char lo, char hi) {
      return BIT_AND(
          OP(cmpgt_epi8)(x, OP(set1_epi8)(static_cast<char>(lo - 1))),
          OP(cmpgt_epi8)(OP(set1_epi8)(static_cast<char>(hi + 1)), x));
    };
    auto valid =
        BIT_OR(BIT_OR(range('A', 'Z'), range('a', 'z')), range('0', '9'));
    valid = BIT_OR(valid, BIT_OR(OP(cmpeq_epi8)(x, OP(set1_epi8)('+')),
                                 OP(cmpeq_epi8)(x, OP(set1_epi8)('/'))));
    if (alphabet == Alphabet::mixed_url)
      valid = BIT_OR(valid, BIT_OR(OP(cmpeq_epi8)(x, OP(set1_epi8)('-')),
                                   OP(cmpeq_epi8)(x, OP(set1_epi8)('_'))));
    if (OP(movemask_epi8)(valid) != full_mask)
      return false;
  }
  return valid_characters(text.substr(offset), alphabet, Backend::scalar);
}
#endif
} // namespace native::encoding::detail
