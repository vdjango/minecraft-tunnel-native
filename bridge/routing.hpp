// SPDX-License-Identifier: AGPL-3.0-or-later
#pragma once
#include "ssh.hpp"
#include <vector>

namespace bridge {
Json directory_request(Profile& selected, const std::vector<Profile>& alternatives,
                       const Json& request);
Profile locate_room(Profile& selected, const std::vector<Profile>& alternatives,
                    const std::string& room);
}
