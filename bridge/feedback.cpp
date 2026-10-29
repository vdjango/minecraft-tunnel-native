// SPDX-License-Identifier: AGPL-3.0-or-later
#include "feedback.hpp"
#include "../core/net.hpp"
#include <openssl/evp.h>
#include <filesystem>
#include <fstream>
#include <array>
#include <algorithm>
#include <memory>

namespace bridge {
namespace {
constexpr std::uintmax_t max_archive = 20u * 1024u * 1024u;
constexpr auto upload_timeout = std::chrono::seconds(120);

std::string read_line(Ssh& ssh, ssh_channel channel,
                      std::chrono::steady_clock::time_point deadline) {
    std::string line; std::array<char, 4096> buffer{};
    while (line.find('\n') == std::string::npos) {
        const int count = ssh_channel_read_nonblocking(channel, buffer.data(),
                                                        static_cast<uint32_t>(buffer.size()), 0);
        if (count > 0) line.append(buffer.data(), static_cast<size_t>(count));
        else if (count == SSH_ERROR || ssh_channel_is_eof(channel) ||
                 std::chrono::steady_clock::now() > deadline)
            throw Failure(-32000, "Feedback node response failed");
        else { PollFd descriptor{ssh.fd(), POLLIN, 0}; net_poll(&descriptor, 1, 20); }
        if (line.size() > 8192) throw Failure(-32000, "Feedback node response too large");
    }
    return line.substr(0, line.find('\n'));
}

void write_all(Ssh& ssh, ssh_channel channel, const char* data, size_t size,
               std::chrono::steady_clock::time_point deadline) {
    size_t offset = 0;
    while (offset < size) {
        if (std::chrono::steady_clock::now() > deadline) throw Failure(-32000, "Feedback upload timed out");
        const int count = ssh.retry([&] {
            return ssh_channel_write(channel, data + offset,
                                     static_cast<uint32_t>(std::min<size_t>(size - offset, 65536)));
        }, 2);
        if (count <= 0) throw Failure(-32000, "Feedback upload failed");
        offset += static_cast<size_t>(count);
    }
}

Json response_line(Ssh& ssh, ssh_channel channel,
                   std::chrono::steady_clock::time_point deadline) {
    auto value = Json::parse(read_line(ssh, channel, deadline));
    if (!value.is_object() || !value.value("ok", false))
        throw Failure(-32000, "Feedback node rejected the upload");
    return value;
}
}

Json submit_feedback(const Profile& profile, const Json& params) {
    // Check consent before touching the archive or opening a node connection.
    if (!params.is_object() || params.value("consent", false) != true)
        throw Failure(-32602, "Explicit diagnostics upload consent is required");
    const auto path_value = params.value("archive", Json(nullptr));
    const auto metadata = params.value("metadata", Json(nullptr));
    if (!path_value.is_string() || !metadata.is_object()) throw Failure(-32602, "Invalid feedback upload parameters");
    const std::filesystem::path path = std::filesystem::u8path(path_value.get<std::string>());
    std::error_code error; const auto status = std::filesystem::symlink_status(path, error);
    if (error || !std::filesystem::is_regular_file(status) || path.extension() != ".zip")
        throw Failure(-32602, "Feedback archive must be a regular ZIP file");
    const auto size = std::filesystem::file_size(path, error);
    if (error || size == 0 || size > max_archive) throw Failure(-32602, "Feedback archive must be between 1 byte and 20 MiB");
    Ssh connection(profile, "directory:rooms", {}, 5);
    auto channel = connection.subsystem("buggcraft-feedback-upload-v1");
    try {
        const auto deadline = std::chrono::steady_clock::now() + upload_timeout;
        const auto filename = path.filename().u8string();
        const std::string filename_utf8(reinterpret_cast<const char*>(filename.data()), filename.size());
        Json header{{"consent", true}, {"name", filename_utf8}, {"size", size}, {"metadata", metadata}};
        const auto header_line = header.dump() + "\n";
        if (header_line.size() > 8192) throw Failure(-32602, "Feedback metadata is too large");
        write_all(connection, channel, header_line.data(), header_line.size(), deadline);
        const auto ready = response_line(connection, channel, deadline);
        const auto issue = ready.value("issue", std::string{});
        if (issue.empty()) throw Failure(-32000, "Feedback node returned no issue id");
        std::ifstream input(path, std::ios::binary); if (!input) throw Failure(-32000, "Feedback archive cannot be opened");
        std::unique_ptr<EVP_MD_CTX, decltype(&EVP_MD_CTX_free)> digest(EVP_MD_CTX_new(), EVP_MD_CTX_free);
        if (!digest || EVP_DigestInit_ex(digest.get(), EVP_sha256(), nullptr) != 1) throw Failure(-32000, "SHA256 unavailable");
        std::array<char, 65536> block{}; std::uintmax_t sent = 0;
        while (input && sent < size) {
            const auto want = static_cast<std::streamsize>(std::min<std::uintmax_t>(block.size(), size - sent));
            input.read(block.data(), want); const auto count = input.gcount(); if (count <= 0) break;
            EVP_DigestUpdate(digest.get(), block.data(), static_cast<size_t>(count));
            write_all(connection, channel, block.data(), static_cast<size_t>(count), deadline);
            sent += static_cast<std::uintmax_t>(count);
        }
        if (sent != size) throw Failure(-32000, "Feedback archive changed while reading");
        unsigned char digest_bytes[EVP_MAX_MD_SIZE]{}; unsigned int digest_size = 0;
        EVP_DigestFinal_ex(digest.get(), digest_bytes, &digest_size);
        constexpr char hex[] = "0123456789abcdef"; std::string checksum; checksum.reserve(digest_size * 2);
        for (unsigned int i = 0; i < digest_size; ++i) { checksum.push_back(hex[digest_bytes[i] >> 4]); checksum.push_back(hex[digest_bytes[i] & 15]); }
        if (connection.retry([&] { return ssh_channel_send_eof(channel); }, 2) != SSH_OK) throw Failure(-32000, "Feedback finalization failed");
        const auto result = response_line(connection, channel, deadline);
        if (result.value("issue", issue) != issue || result.value("size", 0u) != size || result.value("sha256", std::string{}) != checksum)
            throw Failure(-32000, "Feedback node returned an invalid receipt");
        free_channel(channel); return Json{{"ok", true}, {"issue", issue}, {"size", size}, {"sha256", checksum}};
    } catch (...) { free_channel(channel); throw; }
}
}
