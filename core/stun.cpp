// SPDX-License-Identifier: AGPL-3.0-or-later
#include "runtime.hpp"

void Engine::stun() {
    std::array<unsigned char, 513> bytes;
    sockaddr_storage source{};
    SockLen source_length = sizeof(source);
    int received = static_cast<int>(recvfrom(udp, reinterpret_cast<char*>(bytes.data()), static_cast<int>(bytes.size()),
                                            0, reinterpret_cast<sockaddr*>(&source), &source_length));
    if (received < 20 || received > 512 || bytes[0] != 0 || bytes[1] != 1 ||
        bytes[4] != 0x21 || bytes[5] != 0x12 || bytes[6] != 0xa4 || bytes[7] != 0x42 ||
        (bytes[2] * 256 + bytes[3]) != received - 20 || bytes[3] % 4) return;
    std::array<unsigned char, 16> ip{};
    uint16_t port;
    bool v4 = source.ss_family == AF_INET;
    if (v4) {
        auto* address = reinterpret_cast<sockaddr_in*>(&source);
        std::memcpy(ip.data(), &address->sin_addr, 4);
        port = ntohs(address->sin_port);
        if (ip[0] >= 224 || (ip[0] == 0 && ip[1] == 0 && ip[2] == 0 && ip[3] == 0)) return;
    } else if (source.ss_family == AF_INET6) {
        auto* address = reinterpret_cast<sockaddr_in6*>(&source);
        std::memcpy(ip.data(), &address->sin6_addr, 16);
        port = ntohs(address->sin6_port);
        if (ip[0] == 0xff || std::all_of(ip.begin(), ip.end(), [](auto b) { return b == 0; })) return;
    } else return;
    auto second = std::chrono::duration_cast<std::chrono::seconds>(Clock::now().time_since_epoch()).count();
    if (stun_second != second) { stun_second = second; stun_count = 0; }
    if (++stun_count > 1000) return;
    std::string key(reinterpret_cast<char*>(ip.data()), v4 ? 3 : 8);
    if (stun_buckets.size() >= 4096 && !stun_buckets.count(key)) stun_buckets.clear();
    auto& bucket = stun_buckets[key];
    if (bucket.first != second) bucket = {second, 0};
    if (++bucket.second > 20) return;
    auto response = bytes;
    response[0] = 1; response[1] = 1;
    response[2] = 0; response[3] = v4 ? 12 : 24;
    response[20] = 0; response[21] = 0x20;
    response[22] = 0; response[23] = v4 ? 8 : 20;
    response[24] = 0; response[25] = v4 ? 1 : 2;
    uint16_t mapped_port = port ^ 0x2112;
    response[26] = static_cast<unsigned char>(mapped_port >> 8);
    response[27] = static_cast<unsigned char>(mapped_port & 255);
    for (size_t i = 0; i < (v4 ? 4U : 16U); ++i) response[28+i] = ip[i] ^ bytes[4+i];
    sendto(udp, reinterpret_cast<char*>(response.data()), v4 ? 32 : 44, 0,
           reinterpret_cast<sockaddr*>(&source), source_length);
}
