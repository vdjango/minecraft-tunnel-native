// SPDX-License-Identifier: AGPL-3.0-or-later
#include "runtime.hpp"

int main(int argc, char** argv) {
    std::ios::sync_with_stdio(false);
    std::cin.tie(nullptr);
    if (argc == 2 && std::string(argv[1]) == "--version") {
        std::cout << "Buggcraft native core " BUGGCRAFT_VERSION " (libssh " << ssh_version(0) << ")\n";
        return 0;
    }
#ifdef _WIN32
    WSADATA data;
    if (WSAStartup(MAKEWORD(2,2), &data)) return 1;
#else
    std::signal(SIGPIPE, SIG_IGN);
#endif
    int result = 1;
    try {
        ssh_init();
        std::string line;
        if (!read_line(line)) return 1;
        auto config = Json::parse(line);
        if (config.value("op", "") != "init") throw std::runtime_error("initial configuration required");
        Engine engine(std::move(config));
        result = engine.run();
    } catch (const std::exception& error) {
        std::cout << Json({{"op", "fatal"}, {"reason", error.what()}}).dump() << std::endl;
    }
    ssh_finalize();
#ifdef _WIN32
    WSACleanup();
#endif
    return result;
}
