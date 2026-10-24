// SPDX-License-Identifier: AGPL-3.0-or-later
#include "../core/net.hpp"
#include <chrono>
#include <iostream>

int main() {
    constexpr size_t iterations = 10000000;
    for (size_t capacity : {size_t{65536}, size_t{131100}}) {
        Ring ring(capacity);
        uint64_t checksum = 0;
        const auto start = std::chrono::steady_clock::now();
        for (size_t i = 0; i < iterations; ++i) {
            const auto written = std::min<size_t>(64, ring.writable());
            std::memset(ring.write_ptr(), static_cast<int>(i & 127), written);
            ring.produced(written);
            const auto read = std::min<size_t>(64, ring.readable());
            checksum += static_cast<unsigned char>(ring.read_ptr()[read - 1]);
            ring.consumed(read);
        }
        const auto seconds = std::chrono::duration<double>(std::chrono::steady_clock::now() - start).count();
        std::cout << "{\"case\":\"ring_" << capacity << "\",\"million_cycles_s\":"
                  << (iterations / seconds / 1000000) << ",\"checksum\":" << checksum << "}\n";
    }
}
