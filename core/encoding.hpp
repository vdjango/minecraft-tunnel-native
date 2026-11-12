// SPDX-License-Identifier: AGPL-3.0-or-later
#pragma once
#include <string>
#include <string_view>

namespace native::encoding {
enum class Alphabet { standard, mixed_url };
enum class Backend { scalar, sse2, avx2, avx512bw, neon };

// Pure, bounded byte scans. Explicit backends reject unsupported CPU/OS state.
// Automatic dispatch is immutable after first use; BUGGCRAFT_DISABLE_SIMD=1
// selects the reference path for diagnostics, before starting the process.
bool available(Backend backend);
const char *name(Backend backend);
Backend selected_backend();
bool valid_characters(std::string_view text, Alphabet alphabet,
                      Backend backend);
bool valid_characters(std::string_view text,
                      Alphabet alphabet = Alphabet::standard);

struct DecodeOptions {
  Alphabet alphabet = Alphabet::standard;
  bool unpadded = false;
  bool padding = true;
  bool canonical = true;
};
// Owns all Base64 byte handling; callers retain domain size limits/error codes.
// Throws invalid_argument on malformed input, length_error before EVP int
// overflow. URL encoding is unpadded. No global OpenSSL capability overrides
// are installed.
std::string base64_encode(std::string_view bytes, bool url = false);
std::string base64_decode(std::string_view text, DecodeOptions options = {});
std::string hex(std::string_view bytes);
std::string unhex(std::string_view text);
} // namespace native::encoding
