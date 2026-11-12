// SPDX-License-Identifier: AGPL-3.0-or-later
#include "../core/ipc.hpp"
#include "measure.hpp"
#include <array>
#include <chrono>
#include <iostream>
#include <memory>
#include <openssl/crypto.h>
#include <openssl/evp.h>
#include <random>
#include <stdexcept>
#include <vector>

int main() {
  std::cerr << OpenSSL_version(OPENSSL_VERSION) << '\n';
  std::mt19937 rng(983451);
  for (size_t size : {size_t{64}, size_t{16384}, size_t{65536}}) {
    std::string input(size, '\0');
    for (auto &ch : input)
      ch = static_cast<char>(rng());
    const auto encoded = encode(input.data(), input.size());
    std::string output;
    measure("ipc_encode_" + std::to_string(size), size,
            [&] { output = encode(input.data(), input.size()); });
    if (output != encoded)
      throw std::runtime_error("Encode mismatch");
    measure("ipc_decode_" + std::to_string(size), size,
            [&] { output = decode(encoded); });
    if (output != input)
      throw std::runtime_error("Decode mismatch");
  }
  std::string input(65536, '\0');
  for (auto &ch : input)
    ch = static_cast<char>(rng());
  for (auto *algorithm : {EVP_sha1(), EVP_sha256()}) {
    std::unique_ptr<EVP_MD_CTX, decltype(&EVP_MD_CTX_free)> ctx(
        EVP_MD_CTX_new(), EVP_MD_CTX_free);
    std::array<unsigned char, EVP_MAX_MD_SIZE> output{}, expected{};
    unsigned length = 0, expected_length = 0;
    if (!EVP_Digest(input.data(), input.size(), expected.data(),
                    &expected_length, algorithm, nullptr))
      return 1;
    measure(std::string(EVP_MD_get0_name(algorithm)) + "_65536", input.size(),
            [&] {
              if (!EVP_DigestInit_ex(ctx.get(), algorithm, nullptr) ||
                  !EVP_DigestUpdate(ctx.get(), input.data(), input.size()) ||
                  !EVP_DigestFinal_ex(ctx.get(), output.data(), &length))
                throw std::runtime_error("Digest failed");
            });
    if (length != expected_length || output != expected)
      throw std::runtime_error("Digest mismatch");
  }
  // AEAD reinitialisation and tags are part of each measured SSH-sized record.
  // Nonces are unique within the benchmark key, also across warmup iterations.
  std::array<unsigned char, 16> key{}, tag{};
  std::array<unsigned char, 12> iv{};
  std::vector<unsigned char> encrypted(input.size() + 16),
      plain(input.size() + 16);
  std::unique_ptr<EVP_CIPHER_CTX, decltype(&EVP_CIPHER_CTX_free)> ctx(
      EVP_CIPHER_CTX_new(), EVP_CIPHER_CTX_free);
  uint64_t nonce = 0;
  int count = 0, tail = 0;
  measure("AES128-GCM_65536", input.size(), [&] {
    ++nonce;
    for (size_t i = 0; i < 8; ++i)
      iv[i] = static_cast<unsigned char>(nonce >> (i * 8));
    if (!EVP_EncryptInit_ex(ctx.get(), EVP_aes_128_gcm(), nullptr, key.data(),
                            iv.data()) ||
        !EVP_EncryptUpdate(
            ctx.get(), encrypted.data(), &count,
            reinterpret_cast<const unsigned char *>(input.data()),
            static_cast<int>(input.size())) ||
        !EVP_EncryptFinal_ex(ctx.get(), encrypted.data() + count, &tail) ||
        !EVP_CIPHER_CTX_ctrl(ctx.get(), EVP_CTRL_GCM_GET_TAG, 16, tag.data()))
      throw std::runtime_error("AEAD encryption failed");
  });
  const auto encoded_size = count + tail;
  if (!EVP_DecryptInit_ex(ctx.get(), EVP_aes_128_gcm(), nullptr, key.data(),
                          iv.data()) ||
      !EVP_DecryptUpdate(ctx.get(), plain.data(), &count, encrypted.data(),
                         encoded_size) ||
      !EVP_CIPHER_CTX_ctrl(ctx.get(), EVP_CTRL_GCM_SET_TAG, 16, tag.data()) ||
      !EVP_DecryptFinal_ex(ctx.get(), plain.data() + count, &tail) ||
      std::string_view(reinterpret_cast<char *>(plain.data()),
                       static_cast<size_t>(count + tail)) != input)
    throw std::runtime_error("AEAD roundtrip failed");
  std::cout << "{\"verified\":true}\n";
}
