// SPDX-License-Identifier: AGPL-3.0-or-later
#include "runtime.hpp"

std::string encode(const char* bytes, size_t length) {
    std::string result(4 * ((length + 2) / 3), '\0');
    EVP_EncodeBlock(reinterpret_cast<unsigned char*>(result.data()),
                    reinterpret_cast<const unsigned char*>(bytes), static_cast<int>(length));
    return result;
}
std::string decode(const std::string& text) {
    if (text.size() % 4 || text.size() > max_ipc_frame) throw std::runtime_error("invalid IPC encoding");
    size_t padding = 0;
    if (!text.empty() && text.back() == '=') ++padding;
    if (text.size() > 1 && text[text.size() - 2] == '=') ++padding;
    for (size_t i = 0; i < text.size() - padding; ++i) {
        const unsigned char ch = static_cast<unsigned char>(text[i]);
        const bool valid = (ch >= 'A' && ch <= 'Z') || (ch >= 'a' && ch <= 'z') ||
                           (ch >= '0' && ch <= '9') || ch == '+' || ch == '/';
        if (!valid) throw std::runtime_error("invalid IPC encoding");
    }
    for (size_t i = text.size() - padding; i < text.size(); ++i) {
        if (text[i] != '=') throw std::runtime_error("invalid IPC encoding");
    }
    if (padding > 2 || (!text.empty() && padding && text.size() < 4))
        throw std::runtime_error("invalid IPC encoding");
    auto sextet = [](char ch) -> unsigned {
        if (ch >= 'A' && ch <= 'Z') return static_cast<unsigned>(ch - 'A');
        if (ch >= 'a' && ch <= 'z') return static_cast<unsigned>(ch - 'a' + 26);
        if (ch >= '0' && ch <= '9') return static_cast<unsigned>(ch - '0' + 52);
        return ch == '+' ? 62u : 63u;
    };
    if (padding == 2 && (sextet(text[text.size() - 3]) & 0x0f) != 0)
        throw std::runtime_error("invalid IPC encoding");
    if (padding == 1 && (sextet(text[text.size() - 2]) & 0x03) != 0)
        throw std::runtime_error("invalid IPC encoding");
    std::string result(text.size() / 4 * 3 + 3, '\0');
    int length = EVP_DecodeBlock(reinterpret_cast<unsigned char*>(result.data()),
        reinterpret_cast<const unsigned char*>(text.data()), static_cast<int>(text.size()));
    if (length < 0) throw std::runtime_error("invalid IPC encoding");
    if (!text.empty() && text.back() == '=') --length;
    if (text.size() > 1 && text[text.size()-2] == '=') --length;
    result.resize(static_cast<size_t>(length));
    return result;
}
bool read_line(std::string& result) {
    result.clear();
    char byte;
    while (std::cin.get(byte)) {
        if (byte == '\n') return true;
        if (result.size() == max_ipc_frame) throw std::runtime_error("IPC frame limit exceeded");
        result.push_back(byte);
    }
    return false;
}
