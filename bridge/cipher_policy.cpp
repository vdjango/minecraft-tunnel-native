// SPDX-License-Identifier: AGPL-3.0-or-later
#include "cipher_policy.hpp"
#include <openssl/evp.h>
#include <openssl/err.h>
#include <algorithm>
#include <array>
#include <chrono>
#include <cmath>
#include <cstdlib>
#include <memory>
#include <string>
#include <string_view>

namespace bridge {
bool prefer_aes_gcm(double aes_bulk, double chacha_bulk, double aes_small, double chacha_small) {
    // Require a clear bulk win without sacrificing movement-sized packets.
    return std::isfinite(aes_bulk) && std::isfinite(chacha_bulk) &&
        std::isfinite(aes_small) && std::isfinite(chacha_small) &&
        aes_bulk > 0 && chacha_bulk > 0 && aes_small > 0 && chacha_small > 0 &&
        aes_bulk < chacha_bulk * .85 && aes_small < chacha_small * 1.10;
}
namespace {
using Clock = std::chrono::steady_clock;
double probe(const EVP_CIPHER* cipher, int size, unsigned nonce_base) {
    std::unique_ptr<EVP_CIPHER_CTX, decltype(&EVP_CIPHER_CTX_free)> context(EVP_CIPHER_CTX_new(), EVP_CIPHER_CTX_free);
    std::array<unsigned char, 32> key{};
    std::array<unsigned char, 12> iv{};
    std::array<unsigned char, 16384> input{};
    std::array<unsigned char, 16416> output{};
    std::array<unsigned char, 16> tag{};
    if (!context || !cipher || EVP_EncryptInit_ex(context.get(), cipher, nullptr, key.data(), iv.data()) != 1) return 0;
    const int repetitions = size > 1024 ? 32 : 128;
    const auto start = Clock::now();
    for (int i = 0; i < repetitions; ++i) {
        const unsigned nonce = nonce_base + static_cast<unsigned>(i) + 1;
        for (unsigned b = 0; b < 4; ++b) iv[b] = static_cast<unsigned char>(nonce >> (8 * b));
        int count = 0, tail = 0;
        if (EVP_EncryptInit_ex(context.get(), nullptr, nullptr, nullptr, iv.data()) != 1 ||
            EVP_EncryptUpdate(context.get(), nullptr, &count, iv.data(), 4) != 1 ||
            EVP_EncryptUpdate(context.get(), output.data(), &count, input.data(), size) != 1 ||
            EVP_EncryptFinal_ex(context.get(), output.data() + count, &tail) != 1 ||
            EVP_CIPHER_CTX_ctrl(context.get(), EVP_CTRL_AEAD_GET_TAG, 16, tag.data()) != 1) return 0;
    }
    return std::chrono::duration<double, std::nano>(Clock::now() - start).count() / repetitions;
}
CipherPolicy select_policy() {
    CipherPolicy result;
    const char* variable = std::getenv("BUGGCRAFT_SSH_CIPHER_POLICY");
    const std::string_view mode = variable ? variable : "auto";
    if (mode == "library") return result;
    if (mode == "aes-gcm") return {"forced-aes-gcm", "aes256-gcm@openssh.com"};
    if (mode == "chacha") return {"forced-chacha", "chacha20-poly1305@openssh.com"};
    if (mode != "auto") return result;
    const auto start = Clock::now();
    // Local AEAD timings are a conservative dispatch heuristic, not an SSH
    // benchmark: OpenSSH's ChaCha construction differs from EVP's AEAD mode.
    // Never clear errors belonging to the calling SDK application.
    const bool marked = ERR_set_mark() == 1;
    std::array<double, 3> aes_bulk{}, chacha_bulk{}, aes_small{}, chacha_small{};
    for (unsigned i = 0; i < 3; ++i) {
        auto aes = [&] {
            aes_bulk[i] = probe(EVP_aes_256_gcm(), 16384, i * 1024);
            aes_small[i] = probe(EVP_aes_256_gcm(), 128, i * 1024 + 256);
        };
        auto chacha = [&] {
            chacha_bulk[i] = probe(EVP_chacha20_poly1305(), 16384, i * 1024);
            chacha_small[i] = probe(EVP_chacha20_poly1305(), 128, i * 1024 + 256);
        };
        if (i % 2) { chacha(); aes(); } else { aes(); chacha(); }
    }
    if (marked) ERR_pop_to_mark(); else ERR_clear_error();
    auto median = [](auto values) { std::sort(values.begin(), values.end()); return values[1]; };
    result.aes_bulk_ns = median(aes_bulk); result.chacha_bulk_ns = median(chacha_bulk);
    result.aes_small_ns = median(aes_small); result.chacha_small_ns = median(chacha_small);
    result.name = "auto-library";
    if (prefer_aes_gcm(result.aes_bulk_ns, result.chacha_bulk_ns, result.aes_small_ns, result.chacha_small_ns)) {
        result.name = "auto-aes-gcm";
        result.preferred = "aes256-gcm@openssh.com";
    }
    result.probe_ms = std::chrono::duration<double, std::milli>(Clock::now() - start).count();
    return result;
}
}
const CipherPolicy& cipher_policy() {
    static const CipherPolicy policy = select_policy();
    return policy;
}
bool apply_cipher_policy(ssh_session session, const CipherPolicy& policy) {
    if (!policy.preferred[0]) return true;
    const std::string preference = std::string("^") + policy.preferred;
    return ssh_options_set(session, SSH_OPTIONS_CIPHERS_C_S, preference.c_str()) == SSH_OK &&
        ssh_options_set(session, SSH_OPTIONS_CIPHERS_S_C, preference.c_str()) == SSH_OK;
}
}
