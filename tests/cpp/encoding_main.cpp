// SPDX-License-Identifier: AGPL-3.0-or-later
#include "../../core/encoding.hpp"
#include <functional>
#include <iostream>
#include <stdexcept>
#include <vector>
void add_encoding_tests(
    std::vector<std::pair<std::string, std::function<void()>>> &);
int main() {
  using namespace native::encoding;
  for (auto backend : {Backend::scalar, Backend::sse2, Backend::avx2,
                       Backend::avx512bw, Backend::neon})
    std::cout << name(backend) << '=' << available(backend) << '\n';
  std::vector<std::pair<std::string, std::function<void()>>> cases;
  add_encoding_tests(cases);
  for (const auto &[name, test] : cases) {
    try {
      test();
    } catch (const std::exception &error) {
      std::cerr << "FAIL " << name << ": " << error.what() << '\n';
      return 1;
    }
    std::cout << "PASS " << name << '\n';
  }
}
