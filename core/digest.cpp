// SPDX-License-Identifier: AGPL-3.0-or-later
#include "digest.hpp"
#include "encoding.hpp"
#include <array>
#include <optional>
#include <stdexcept>
namespace native {
Digest::Digest(const EVP_MD *algorithm) {
  if (!context_ || EVP_DigestInit_ex(context_.get(), algorithm, nullptr) != 1)
    throw std::runtime_error("Digest unavailable");
}
void Digest::update(std::string_view bytes) {
  if (EVP_DigestUpdate(context_.get(), bytes.data(), bytes.size()) != 1)
    throw std::runtime_error("Digest update failed");
}
std::string Digest::final_hex() {
  std::array<unsigned char, EVP_MAX_MD_SIZE> hash{};
  unsigned count = 0;
  if (EVP_DigestFinal_ex(context_.get(), hash.data(), &count) != 1)
    throw std::runtime_error("Digest finalization failed");
  return encoding::hex(
      std::string_view(reinterpret_cast<const char *>(hash.data()), count));
}
std::pair<std::string, std::string> stream_digests(std::istream &input,
                                                   bool with_sha1) {
  Digest sha256(EVP_sha256());
  std::optional<Digest> sha1;
  if (with_sha1)
    sha1.emplace(EVP_sha1());
  std::array<char, 131072> bytes;
  while (input.read(bytes.data(), bytes.size()) || input.gcount()) {
    const std::string_view block(bytes.data(),
                                 static_cast<size_t>(input.gcount()));
    sha256.update(block);
    if (sha1)
      sha1->update(block);
  }
  if (input.bad() || !input.eof())
    throw std::ios_base::failure("File digest read failed");
  return {sha256.final_hex(), sha1 ? sha1->final_hex() : std::string{}};
}
} // namespace native
