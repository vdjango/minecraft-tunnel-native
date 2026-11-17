// SPDX-License-Identifier: AGPL-3.0-or-later
#include "../../bridge/profile_exchange.hpp"
#include <fstream>
#include <iostream>
#include <iterator>

int main(int argc, char **argv) {
  if (argc != 3)
    return 2;
  std::ifstream file(argv[2], std::ios::binary);
  const std::string ca{std::istreambuf_iterator<char>{file}, {}};
  try {
    for (int i = 0; i != 2; ++i) {
      const auto value =
          bridge::https_json(argv[1], "/v1/probe", {{"sequence", i}},
                             i == 0 ? "test-token" : "", ca);
      if (value.at("sequence") != i)
        return 3;
    }
  } catch (const std::exception &error) {
    std::cerr << error.what() << '\n';
    return 1;
  }
}
