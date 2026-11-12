// SPDX-License-Identifier: AGPL-3.0-or-later
#include "encoding.hpp"
// Internal codec interface is pinned to aklomp/base64 0.5.2. Do not use its
// mutable global dispatcher: concurrent SDK calls own per-call stream state.
extern "C" {
#include "codecs.h"
}
#include <algorithm>
#include <array>
#include <cstdlib>
#include <limits>
#include <stdexcept>
#if defined(BUGGCRAFT_X86_SCANS) && defined(_MSC_VER)
#include <intrin.h>
#endif

namespace native::encoding {
std::string unhex(std::string_view text) {
    if(text.size()%2||text.size()>65536)throw std::invalid_argument("Invalid hexadecimal input");
    std::string result(text.size()/2,'\0');
    auto digit=[](char c)->int{if(c>='0'&&c<='9')return c-'0';if(c>='a'&&c<='f')return c-'a'+10;return -1;};
    for(size_t i=0;i<result.size();++i){int a=digit(text[i*2]),b=digit(text[i*2+1]);if(a<0||b<0)throw std::invalid_argument("Invalid hexadecimal input");result[i]=char((a<<4)|b);}
    return result;
}
namespace detail {
#ifdef BUGGCRAFT_X86_SCANS
bool scan_sse2(std::string_view, Alphabet);
bool scan_avx2(std::string_view, Alphabet);
bool scan_avx512(std::string_view, Alphabet);
#endif
#ifdef BUGGCRAFT_NEON_SCAN
bool scan_neon(std::string_view, Alphabet);
#endif
} // namespace detail
namespace {
constexpr auto table = [] {
  std::array<unsigned char, 256> values{};
  for (unsigned c = 'A'; c <= 'Z'; ++c)
    values[c] = 1;
  for (unsigned c = 'a'; c <= 'z'; ++c)
    values[c] = 1;
  for (unsigned c = '0'; c <= '9'; ++c)
    values[c] = 1;
  values['+'] = values['/'] = 1;
  values['-'] = values['_'] = 2;
  return values;
}();
struct Capabilities {
  bool sse2 = false, avx2 = false, avx512 = false;
};
Capabilities capabilities() {
  Capabilities result;
#ifdef BUGGCRAFT_X86_SCANS
#ifdef _MSC_VER
  int regs[4];
  __cpuid(regs, 0);
  const int maximum = regs[0];
  if (maximum < 1)
    return result;
  __cpuidex(regs, 1, 0);
  result.sse2 = (regs[3] & (1 << 26)) != 0;
  const bool osxsave = (regs[2] & (1 << 27)) != 0;
  const bool avx = (regs[2] & (1 << 28)) != 0;
  const auto state = osxsave ? _xgetbv(0) : 0;
  if (maximum >= 7 && avx && (state & 6) == 6) {
    __cpuidex(regs, 7, 0);
    result.avx2 = (regs[1] & (1 << 5)) != 0;
    // /arch:AVX512 permits F, BW, CD, DQ and VL, so require all of them.
    constexpr unsigned required =
        (1u << 16) | (1u << 17) | (1u << 28) | (1u << 30) | (1u << 31);
    result.avx512 = (state & 0xe6) == 0xe6 &&
                    (static_cast<unsigned>(regs[1]) & required) == required;
  }
#else
  __builtin_cpu_init();
  result.sse2 = __builtin_cpu_supports("sse2");
  result.avx2 = __builtin_cpu_supports("avx2");
  result.avx512 =
      __builtin_cpu_supports("avx512f") && __builtin_cpu_supports("avx512bw");
#endif
#endif
  return result;
}
unsigned sextet(char ch) {
  if (ch >= 'A' && ch <= 'Z')
    return static_cast<unsigned>(ch - 'A');
  if (ch >= 'a' && ch <= 'z')
    return static_cast<unsigned>(ch - 'a' + 26);
  if (ch >= '0' && ch <= '9')
    return static_cast<unsigned>(ch - '0' + 52);
  return ch == '+' || ch == '-' ? 62u : 63u;
}
} // namespace
bool available(Backend backend) {
  static const auto caps = capabilities();
  switch (backend) {
  case Backend::scalar:
    return true;
  case Backend::sse2:
    return caps.sse2;
  case Backend::avx2:
    return caps.avx2;
  case Backend::avx512bw:
    return caps.avx512;
  case Backend::neon:
#ifdef BUGGCRAFT_NEON_SCAN
    return true; // ASIMD is part of the AArch64 platform baseline.
#else
    return false;
#endif
  }
  return false;
}
const char *name(Backend backend) {
  switch (backend) {
  case Backend::scalar:
    return "scalar";
  case Backend::sse2:
    return "sse2";
  case Backend::avx2:
    return "avx2";
  case Backend::avx512bw:
    return "avx512bw";
  case Backend::neon:
    return "neon";
  }
  return "unknown";
}
Backend selected_backend() {
  static const auto backend = [] {
    const auto *disabled = std::getenv("BUGGCRAFT_DISABLE_SIMD");
    if (disabled && std::string_view(disabled) == "1")
      return Backend::scalar;
    // Prefer AVX2 for mixed interactive traffic. AVX-512 is measured via
    // the explicit benchmark path before changing the default policy.
    for (auto candidate : {Backend::avx2, Backend::sse2, Backend::neon})
      if (available(candidate))
        return candidate;
    return Backend::scalar;
  }();
  return backend;
}
namespace {
const codec &selected_codec() {
  static const codec value = [] {
    int flags = BASE64_FORCE_PLAIN;
    if (selected_backend() == Backend::avx2)
      flags = BASE64_FORCE_AVX2;
#ifdef BUGGCRAFT_BASE64_NEON
    if (selected_backend() == Backend::neon)
      flags = BASE64_FORCE_NEON64;
#endif
    codec result{};
    codec_choose(&result, flags);
    return result;
  }();
  return value;
}
} // namespace
bool valid_characters(std::string_view text, Alphabet alphabet,
                      Backend backend) {
  if (!available(backend))
    throw std::invalid_argument("Unavailable SIMD backend");
  switch (backend) {
#ifdef BUGGCRAFT_X86_SCANS
  case Backend::sse2:
    return detail::scan_sse2(text, alphabet);
  case Backend::avx2:
    return detail::scan_avx2(text, alphabet);
  case Backend::avx512bw:
    return detail::scan_avx512(text, alphabet);
#endif
#ifdef BUGGCRAFT_NEON_SCAN
  case Backend::neon:
    return detail::scan_neon(text, alphabet);
#endif
  default:
    const unsigned mask = alphabet == Alphabet::standard ? 1u : 3u;
    for (unsigned char ch : text)
      if (!(table[ch] & mask))
        return false;
    return true;
  }
}
bool valid_characters(std::string_view text, Alphabet alphabet) {
  return valid_characters(text, alphabet, selected_backend());
}
std::string base64_encode(std::string_view bytes, bool url) {
  if (bytes.size() >
      static_cast<size_t>(std::numeric_limits<int>::max() / 4) * 3)
    throw std::length_error("Base64 input too large");
  if (bytes.empty())
    return {};
  std::string result(4 * ((bytes.size() + 2) / 3), '\0');
  base64_state state{};
  size_t count = 0, tail = 0;
  selected_codec().enc(&state, bytes.data(), bytes.size(), result.data(),
                       &count);
  base64_stream_encode_final(&state, result.data() + count, &tail);
  result.resize(count + tail);
  if (url) {
    for (char &ch : result) {
      if (ch == '+')
        ch = '-';
      else if (ch == '/')
        ch = '_';
    }
    while (!result.empty() && result.back() == '=')
      result.pop_back();
  }
  return result;
}
std::string base64_decode(std::string_view text, DecodeOptions options) {
  if (text.empty())
    return {};
  if (text.size() > static_cast<size_t>(std::numeric_limits<int>::max() - 4))
    throw std::length_error("Base64 input too large");
  size_t padding = 0;
  if (text.back() == '=')
    ++padding;
  if (text.size() > 1 && text[text.size() - 2] == '=')
    ++padding;
  const size_t chars = text.size() - padding;
  if ((!options.padding && padding) || (padding && text.size() % 4) ||
      (!options.unpadded && text.size() % 4) || chars % 4 == 1 ||
      !valid_characters(text.substr(0, chars), options.alphabet))
    throw std::invalid_argument("Invalid Base64 encoding");
  const size_t final_padding = (4 - chars % 4) % 4;
  if (options.canonical &&
      ((final_padding == 2 && (sextet(text[chars - 1]) & 15)) ||
       (final_padding == 1 && (sextet(text[chars - 1]) & 3))))
    throw std::invalid_argument("Noncanonical Base64 encoding");
  std::string normalized;
  if (options.alphabet == Alphabet::mixed_url || text.size() % 4) {
    normalized.assign(text.substr(0, chars));
    for (char &ch : normalized) {
      if (ch == '-')
        ch = '+';
      else if (ch == '_')
        ch = '/';
    }
    normalized.append(final_padding, '=');
    text = normalized;
    padding = final_padding;
  }
  std::string result(text.size() / 4 * 3, '\0');
  base64_state state{};
  size_t count = 0;
  if (selected_codec().dec(&state, text.data(), text.size(), result.data(),
                           &count) != 1 ||
      state.bytes != 0)
    throw std::invalid_argument("Invalid Base64 encoding");
  result.resize(count);
  return result;
}
std::string hex(std::string_view bytes) {
  constexpr char digits[] = "0123456789abcdef";
  if (bytes.size() > std::string{}.max_size() / 2)
    throw std::length_error("Hex input too large");
  std::string result(bytes.size() * 2, '\0');
  for (size_t i = 0; i < bytes.size(); ++i) {
    const auto ch = static_cast<unsigned char>(bytes[i]);
    result[2 * i] = digits[ch >> 4];
    result[2 * i + 1] = digits[ch & 15];
  }
  return result;
}
} // namespace native::encoding
