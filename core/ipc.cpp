// SPDX-License-Identifier: AGPL-3.0-or-later
#include "runtime.hpp"
#include "encoding.hpp"

std::string encode(const char* bytes, size_t length) {
    return native::encoding::base64_encode(std::string_view(bytes, length));
}
std::string decode(const std::string& text) {
    if (text.size() % 4 || text.size() > max_ipc_frame) throw std::runtime_error("invalid IPC encoding");
    try { return native::encoding::base64_decode(text); }
    catch (const std::invalid_argument&) { throw std::runtime_error("invalid IPC encoding"); }
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
