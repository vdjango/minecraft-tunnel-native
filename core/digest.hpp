// SPDX-License-Identifier: AGPL-3.0-or-later
#pragma once
#include <istream>
#include <memory>
#include <openssl/evp.h>
#include <string>
#include <string_view>
#include <utility>
namespace native {
// Per-operation state, confined to its caller's thread. Uses OpenSSL's own
// architecture dispatch; final_hex consumes the current digest operation.
class Digest {
  std::unique_ptr<EVP_MD_CTX, decltype(&EVP_MD_CTX_free)> context_{
      EVP_MD_CTX_new(), EVP_MD_CTX_free};

public:
  explicit Digest(const EVP_MD *algorithm);
  void update(std::string_view bytes);
  std::string final_hex();
};
// Single bounded-buffer pass; second digest is opt-in, never rereads input.
// Throws ios_base::failure on read errors, runtime_error on crypto failure.
std::pair<std::string, std::string> stream_digests(std::istream &input,
                                                   bool with_sha1);
} // namespace native
