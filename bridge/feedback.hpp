// SPDX-License-Identifier: AGPL-3.0-or-later
#pragma once
#include "ssh.hpp"

namespace bridge {
class ControlClient;
// RPC worker only; explicit consent, bounded regular ZIP, authenticated receipt.
// Managed uploads use the selected identity's verified control origin, never a node.
Json submit_feedback(const Profile& profile, const Json& params, ControlClient* control = nullptr);
}
