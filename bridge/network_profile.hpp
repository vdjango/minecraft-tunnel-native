// SPDX-License-Identifier: AGPL-3.0-or-later
#pragma once
#include "ssh.hpp"
#include <span>
namespace bridge {
// RPC worker owns probing and disk I/O; streams only increment in-memory
// counters.
Json network_profile(const std::vector<Profile> &endpoints = {},
                     bool refresh = false);
void network_connected(bool direct);
bool stun_response_matches(std::span<const unsigned char> response,
                           std::span<const unsigned char> request);
} // namespace bridge
