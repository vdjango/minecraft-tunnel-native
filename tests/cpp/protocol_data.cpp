// SPDX-License-Identifier: AGPL-3.0-or-later
#include "protocol_data.hpp"
#include <fstream>
#include <iostream>
#include <iterator>
#include <string>
#include <string_view>

int main(int argc, char** argv) {
    if (argc != 3) return 2;
    const std::string_view tables[] = {
        {bridge::game::profiles_json, sizeof(bridge::game::profiles_json) - 1},
        {bridge::game::legacy_json, sizeof(bridge::game::legacy_json) - 1},
    };
    for (int i = 0; i < 2; ++i) {
        std::ifstream file(argv[i + 1], std::ios::binary);
        if (!file) {
            std::cerr << "Cannot open protocol table: " << argv[i + 1] << '\n';
            return 1;
        }
        std::string expected{std::istreambuf_iterator<char>{file}, {}};
        if (tables[i] != expected) {
            std::cerr << "Embedded protocol table differs from source: " << argv[i + 1] << '\n';
            return 1;
        }
        std::cout << "Verified embedded table: " << expected.size() << " bytes\n";
    }
}
