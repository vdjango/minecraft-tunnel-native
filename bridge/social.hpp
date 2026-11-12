// SPDX-License-Identifier: AGPL-3.0-or-later
#pragma once
#include "ssh.hpp"
namespace bridge {
Json social_event(const Profile&,const Json& params);
std::string sign_delegated_message(const Profile&,const std::string& message);
}
