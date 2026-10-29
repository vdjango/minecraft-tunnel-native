// SPDX-License-Identifier: AGPL-3.0-or-later
#include "routing.hpp"
#include <optional>
#include <array>

namespace bridge {
namespace {
struct Sources {
    std::array<const Profile*, 3> nodes{};
    size_t count = 1;
    Sources(const Profile& selected, const std::vector<Profile>& alternatives) {
        nodes[0] = &selected;
        for (const auto& source : alternatives) {
            bool duplicate = false;
            for (size_t i = 0; i < count; ++i)
                duplicate |= nodes[i]->key == source.key && nodes[i]->host == source.host && nodes[i]->port == source.port;
            if (!duplicate) nodes[count++] = &source;
            if (count == nodes.size()) break;
        }
    }
};
}
Json directory_request(Profile& selected, const std::vector<Profile>& alternatives,
                       const Json& request, double* latency_ms) {
    if(latency_ms)*latency_ms=-1;
    const auto op = request.value("op", "");
    const bool read = op == "ping" || op == "list" || op == "state" || op == "topology" ||
                      op == "account_lookup" || op == "locate" || op == "resolve";
    const size_t attempts = read ? 3 : 1;
    const Sources sources(selected, alternatives);
    for (size_t attempt = 0; attempt < attempts && attempt < sources.count; ++attempt) {
        const auto& source = *sources.nodes[attempt];
        try {
            Ssh directory(source, "directory:rooms");
            if(latency_ms)try{*latency_ms=directory.ping();}catch(...){*latency_ms=-1;}
            auto result = directory.request(request);
            if (attempt) log("directory_failover", {{"operation", op}, {"attempts", attempt + 1}});
            selected = source;  // Future reads start with the working directory.
            return result;
        } catch (...) {
            if (!read) throw;
            log("directory_unavailable", {{"operation", op}, {"attempt", attempt + 1}});
        }
    }
    throw Failure(-32000, "Room directory unavailable; retry with an online node");
}

Profile locate_room(Profile& selected, const std::vector<Profile>& alternatives,
                    const std::string& room) {
    // Seed relationships are discovery configuration, never a game route.
    // Ask one directory for its local signed index, then pin the owner key
    // before sending the room secret. Never follow a chain of referrals.
    constexpr size_t max_attempts = 3;
    const auto started = Clock::now();
    std::optional<Profile> fallback;
    const Sources sources(selected, alternatives);
    for (size_t attempt = 0; attempt < max_attempts && attempt < sources.count; ++attempt) {
        const auto& source = *sources.nodes[attempt];
        Json result;
        try {
            Ssh directory(source, "directory:rooms");
            result = directory.request({{"op", "locate"}, {"id", room}});
        } catch (...) {
            log("room_directory_unavailable", {{"room", room}, {"attempt", attempt + 1}});
            continue;
        }
        // A standalone or older node may not have a federation referral.
        // Its normal room authentication still decides whether admission is valid.
        if (!result.contains("profile") || result["profile"].is_null()) {
            if (!fallback) fallback = source;
            continue;
        }
        auto owner = Profile::from_json(result.at("profile"));
        log("room_owner_resolved", {{"room", room}, {"attempts", attempt + 1},
            {"remote", owner.key != selected.key},
            {"elapsed_ms", std::chrono::duration_cast<std::chrono::milliseconds>(Clock::now() - started).count()}});
        selected = source;
        return owner;
    }
    if (fallback) { selected = *fallback; return *fallback; }
    throw Failure(-32000, "Room directory unavailable; retry with an online node");
}
}
