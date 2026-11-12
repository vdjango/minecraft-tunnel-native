// SPDX-License-Identifier: AGPL-3.0-or-later
#pragma once
#include "ssh.hpp"

namespace bridge {
Json redeem_profile_code(const std::string& code);
// Verified HTTPS POST, bounded response, no redirects. Optional CA is scoped to this call.
Json https_json(const std::string& origin,const std::string& path,const Json& body,
                const std::string& token={},const std::string& ca_pem={});
}
