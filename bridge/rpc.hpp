// SPDX-License-Identifier: AGPL-3.0-or-later
#pragma once
#include <string>
#include <string_view>
namespace bridge {
class Rpc;
Rpc* create_rpc();
void destroy_rpc(Rpc* rpc);
std::string exchange_rpc(Rpc* rpc, std::string_view request);
void run_rpc();
}
