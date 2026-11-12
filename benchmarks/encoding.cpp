// SPDX-License-Identifier: AGPL-3.0-or-later
#include "../core/encoding.hpp"
#include "../core/digest.hpp"
#include "measure.hpp"
#include <sstream>
#include <stdexcept>
int main() {
  using namespace native::encoding;
  std::cerr << "selected_backend=" << name(selected_backend()) << '\n';
  for (size_t size : {size_t{64}, size_t{16384}, size_t{65536}}) {
    // Generated text has all alphabet ranges, matching Base64's byte mix.
    constexpr std::string_view alphabet =
        "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    std::string text(size, 'A');
    for (size_t i = 0; i < size; ++i)
      text[i] = alphabet[(i * 17 + i / 13) % alphabet.size()];
    for (auto backend : {Backend::scalar, Backend::sse2, Backend::avx2,
                         Backend::avx512bw, Backend::neon}) {
      if (!available(backend))
        continue;
      bool valid = false;
      measure(
          std::string("validate_") + name(backend) + "_" + std::to_string(size),
          size,
          [&] { valid = valid_characters(text, Alphabet::standard, backend); });
      if (!valid)
        throw std::runtime_error("Valid alphabet rejected");
    }
  }
  const std::string text(1048576, 'x');
  std::string sha256;
  for (bool with_sha1 : {false, true}) {
    std::pair<std::string, std::string> result;
    measure(with_sha1 ? "stream_sha256_sha1" : "stream_sha256", text.size(),
            [&] {
              std::istringstream input(text);
              result = native::stream_digests(input, with_sha1);
            });
    if (with_sha1) {
      if (sha256 != result.first || result.second.size() != 40)
        throw std::runtime_error("Stream digests differ");
    } else
      sha256 = result.first;
  }
  std::cout << "{\"verified\":true}\n";
}
