// SPDX-License-Identifier: AGPL-3.0-or-later
#include "../bridge/codec.hpp"
#include <chrono>
#include <iostream>
#include <stdexcept>

using Clock = std::chrono::steady_clock;

int main() {
    constexpr size_t iterations = 100000;
    for (size_t block : {size_t{256}, size_t{65536}}) {
        bridge::Codec encoder;
        const std::string payload(block, 'x');
        const auto start = Clock::now();
        for (size_t i = 0; i < iterations; ++i) encoder.encode(payload);
        const auto seconds = std::chrono::duration<double>(Clock::now() - start).count();
        std::cout << "{\"case\":\"encode_" << block << "\",\"mib_s\":"
                  << (block * iterations / seconds / 1048576) << ",\"wire_bytes\":"
                  << encoder.wire_bytes << "}\n";
    }
    // Many small frames arriving in one read exercise decoder compaction.
    const std::string frame = std::string(1, static_cast<char>(64)) + std::string(32, 'a');
    std::string wire;
    for (size_t i = 0; i < 3000; ++i) wire += frame;
    bridge::Codec decoder;
    decoder.feed("BCZ1");
    std::string output;
    size_t decoded = 0;
    const auto start = Clock::now();
    for (size_t i = 0; i < 200; ++i) {
        decoder.feed(wire);
        while (decoder.next(output)) {
            if (output != std::string(32, 'a')) throw std::runtime_error("Decoded bytes differ");
            decoded += output.size();
        }
        if (!decoder.complete()) throw std::runtime_error("Trailing frame bytes");
    }
    const auto seconds = std::chrono::duration<double>(Clock::now() - start).count();
    std::cout << "{\"case\":\"decode_coalesced\",\"mib_s\":"
              << (decoded / seconds / 1048576) << ",\"verified\":true}\n";
}
