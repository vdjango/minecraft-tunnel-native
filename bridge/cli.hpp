#pragma once

namespace bridge {
// Shared native CLI entry. The caller passes the bridge executable's argv;
// version/licenses do not initialize networking, RPC owns acquire/release.
int run_cli(int argc, char **argv);
} // namespace bridge
