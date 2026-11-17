// SPDX-License-Identifier: AGPL-3.0-or-later
#pragma once
#include "game_wire.hpp"
#include <utility>

namespace bridge {
// Only the first response of a validated modern status connection is changed.
// Login/play, legacy pings, unknown fields and the actual ping/pong pass through.
struct ServerListStatus {
    bool active = false;
    std::string pending;
    std::string feed(std::string_view bytes, const Json &settings, const std::string &icon) {
        if (!active) return std::string(bytes);
        pending.append(bytes);
        if (pending.empty()) return {};
        if (pending.size() <= 131072) {
            // A VarInt header may itself arrive across TCP segments.
            size_t prefix = 0;
            while (prefix < pending.size() && prefix < 5 &&
                   (static_cast<unsigned char>(pending[prefix++]) & 128)) {}
            if (prefix == pending.size() && (static_cast<unsigned char>(pending.back()) & 128) && prefix < 5)
                return {};
            try {
                game::Cursor cursor{pending};
                const auto length = cursor.integer();
                if (length <= 131072 && pending.size() < cursor.offset + length) return {};
                if (length > 131072) throw std::runtime_error("Large status response");
                const auto end = cursor.offset + length;
                if (cursor.integer() != 0) throw std::runtime_error("Not a status response");
                auto response = Json::parse(cursor.text(32767));
                if (cursor.offset != end || !response.is_object()) throw std::runtime_error("Invalid status response");
                auto title = settings.value("title", std::string("Buggcraft Room"));
                if (title.size() > 192) {
                    size_t title_end = 192;
                    while (title_end && (static_cast<unsigned char>(title[title_end]) & 192) == 128) --title_end;
                    title.resize(title_end);
                }
                const auto game = settings.value("game", Json::object());
                std::string detail = game.value("version", std::string{});
                const auto loader = game.value("loader", std::string{});
                if (!loader.empty() && loader != "vanilla") detail += "  |  " + loader + " " + game.value("loader_version", std::string{});
                if (game.contains("mods_count") && game["mods_count"].is_number_unsigned())
                    detail += "  |  " + std::to_string(game["mods_count"].get<size_t>()) + " mods";
                response["description"] = {{"text", "BUGG · "}, {"color", "gold"}, {"bold", true},
                    {"extra", Json::array({Json{{"text", title}, {"color", "white"}},
                        Json{{"text", "\n" + detail}, {"color", "gray"}, {"bold", false}}})}};
                // Use a real server icon if supplied; otherwise use our bundled 64px icon.
                if (!response.contains("favicon") && !icon.empty()) response["favicon"] = icon;
                if (response.contains("players") && response["players"].is_object()) {
                    auto &players = response["players"];
                    if (!players.contains("sample")) players["sample"] = Json::array();
                    if (players["sample"].is_array()) {
                        auto &sample = players["sample"];
                        if (sample.size() > 16) sample.erase(sample.begin() + 16, sample.end());
                        // Sample rows add hover information without inventing online players.
                        sample.push_back({{"id", "00000000-0000-0000-0000-000000000000"}, {"name", "BUGG · " + title}});
                        if (!detail.empty()) sample.push_back({{"id", "00000000-0000-0000-0000-000000000000"}, {"name", detail}});
                    }
                }
                const auto encoded = response.dump();
                if (encoded.size() > 32767) throw std::runtime_error("Enriched status exceeds text limit");
                const auto payload = game::integer(0) + game::text(encoded);
                auto result = game::integer(static_cast<uint32_t>(payload.size())) + payload + pending.substr(end);
                pending.clear();active = false;return result;
            } catch (...) { /* Unsupported/custom responses retain their original bytes. */ }
        }
        active = false;
        return std::exchange(pending, {});
    }
};
} // namespace bridge
