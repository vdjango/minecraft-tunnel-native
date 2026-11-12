#include "cli.hpp"
#include "rpc.hpp"
#include "runtime.hpp"
#include <csignal>
#include <iostream>
#include <string_view>
#ifdef BUGGCRAFT_EMBEDDED_NOTICES
#include "bridge_notices.hpp"
#endif

int bridge::run_cli(int argc, char **argv) {
  if (argc >= 2 && std::string_view(argv[1]) == "--native-bridge") {
    --argc;
    ++argv;
  }
  if (argc == 2 && std::string(argv[1]) == "--version") {
    std::cout << "Buggcraft native bridge " BUGGCRAFT_VERSION
                 " protocol=1 C++20\n";
    return 0;
  }
#ifdef BUGGCRAFT_EMBEDDED_NOTICES
  if (argc == 2 && std::string(argv[1]) == "--licenses") {
    std::cout.write(reinterpret_cast<const char *>(bridge_notices),
                    sizeof(bridge_notices));
    return 0;
  }
#endif
#ifndef _WIN32
  std::signal(SIGPIPE, SIG_IGN);
#endif
  std::ios::sync_with_stdio(false);
  std::cin.tie(nullptr);
  int result = 0;
  try {
    bridge::acquire_runtime();
    try {
      bridge::run_rpc();
    } catch (...) {
      bridge::release_runtime();
      throw;
    }
    bridge::release_runtime();
  } catch (...) {
    std::cerr << "[bridge] bridge_fatal\n";
    result = 1;
  }
  return result;
}
