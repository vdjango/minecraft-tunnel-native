// SPDX-License-Identifier: AGPL-3.0-or-later
#pragma once
#include <chrono>
#include <iostream>
#include <string>
template <class F>
void measure(const std::string &label, size_t bytes, F operation) {
  for (int i = 0; i < 16; ++i)
    operation();
  const auto start = std::chrono::steady_clock::now();
  size_t count = 0;
  double seconds;
  do {
    for (int i = 0; i < 32; ++i)
      operation();
    count += 32;
    seconds =
        std::chrono::duration<double>(std::chrono::steady_clock::now() - start)
            .count();
  } while (seconds < 0.2);
  std::cout << "{\"case\":\"" << label << "\",\"mib_s\":"
            << (static_cast<double>(bytes) * count / seconds / 1048576)
            << ",\"seconds\":" << seconds << ",\"iterations\":" << count
            << "}\n";
}
