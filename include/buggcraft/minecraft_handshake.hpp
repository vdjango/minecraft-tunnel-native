// SPDX-License-Identifier: AGPL-3.0-or-later
#pragma once
#include <cstdint>
#include <string_view>

namespace buggcraft {
enum class HandshakeState { partial, valid, invalid };
inline HandshakeState minecraft_handshake(std::string_view bytes) {
    size_t offset = 0;
    auto varint = [&](uint32_t& value) {
        value = 0;
        for (unsigned i = 0; i < 5; ++i) {
            if (offset == bytes.size()) return HandshakeState::partial;
            const auto byte = static_cast<unsigned char>(bytes[offset++]);
            if (i == 4 && (byte & 0xf0)) return HandshakeState::invalid;
            value |= uint32_t(byte & 0x7f) << (7 * i);
            if (!(byte & 0x80)) return HandshakeState::valid;
        }
        return HandshakeState::invalid;
    };
    uint32_t length = 0;
    const auto prefix = varint(length);
    if (prefix != HandshakeState::valid) return prefix;
    // The handshake hostname is bounded; a large first packet is never needed.
    if (length < 6 || length > 8192) return HandshakeState::invalid;
    const auto end = offset + length;
    if (end > bytes.size()) return HandshakeState::partial;
    bytes = bytes.substr(0, end);
    uint32_t packet = 0, protocol = 0, hostname = 0, state = 0;
    if (varint(packet) != HandshakeState::valid || packet != 0 ||
        varint(protocol) != HandshakeState::valid ||
        varint(hostname) != HandshakeState::valid || hostname > 255 ||
        hostname > end - offset || end - offset - hostname < 3)
        return HandshakeState::invalid;
    offset += hostname + 2;
    if (varint(state) != HandshakeState::valid || (state != 1 && state != 2) || offset != end)
        return HandshakeState::invalid;
    return HandshakeState::valid;
}
}
