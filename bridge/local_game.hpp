// SPDX-License-Identifier: AGPL-3.0-or-later
#pragma once
#include "ssh.hpp"
#include <set>
namespace bridge {
Json game_status(int port,int timeout_ms=2000);
bool game_reachable(int port);
Json discover_worlds(const std::set<int>& excluded,int duration_ms=3000);
void announce_world(int port,const std::string& title);
}
