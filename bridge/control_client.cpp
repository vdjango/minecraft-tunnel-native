// SPDX-License-Identifier: AGPL-3.0-or-later
#include "control_client.hpp"
#include "../core/encoding.hpp"
#include "profile_exchange.hpp"
#include "social.hpp"
#include <algorithm>
#include <openssl/evp.h>
namespace bridge {
namespace {
int64_t seconds() {
  return std::chrono::duration_cast<std::chrono::seconds>(
             std::chrono::system_clock::now().time_since_epoch())
      .count();
}
std::string ssh_key(const std::string &key) {
  auto raw = native::encoding::unhex(key);
  if (raw.size() != 32)
    throw Failure(-32030, "Invalid managed node key");
  const std::string type = "ssh-ed25519";
  auto field = [](const std::string &value) {
    uint32_t n = static_cast<uint32_t>(value.size());
    return std::string{char(n >> 24), char(n >> 16), char(n >> 8), char(n)} +
           value;
  };
  return type + " " + native::encoding::base64_encode(field(type) + field(raw));
}
Json token_claims(const Profile &identity) {
  if (!identity.token.starts_with("acct1."))
    throw Failure(-32602, "Managed login requires a delegated account");
  auto encoded = identity.token.substr(6, identity.token.find('.', 6) - 6);
  return Json::parse(native::encoding::base64_decode(
      encoded, {native::encoding::Alphabet::mixed_url, true, true, false}));
}
} // namespace
Json ControlClient::call(const std::string &operation, const Json &body,
                         bool authenticated) {
  std::lock_guard guard(mutex_);
  if (authenticated &&
      grant_.value("expires_at", int64_t(0)) < seconds() + 60) {
    if (grant_.value("expires_at", int64_t(0)) <= seconds())
      throw Failure(-32031, "Control identity expired; sign in again");
    grant_ = https_json(metadata_.at("origin"), "/v1/identity/renew",
                        Json::object(), grant_.at("token"),
                        metadata_.value("ca_pem", std::string{}));
  }
  return https_json(metadata_.at("origin"), "/v1/" + operation, body,
                    authenticated ? grant_.at("token").get<std::string>()
                                  : std::string{},
                    metadata_.value("ca_pem", std::string{}));
}
ControlClient::ControlClient(const Profile &identity)
    : identity_(identity), metadata_(identity.control) {
  if (metadata_.value("protocol", 0) != 1)
    throw Failure(-32602, "Unsupported managed protocol");
  const auto health = call("health", Json::object(), false);
  if (health.at("signing_public_key") != metadata_.at("signing_public_key"))
    throw Failure(-32030, "Control signing key mismatch");
  auto claims = token_claims(identity_);
  auto delegation = claims.at("g");
  auto challenge = call("identity/challenge",
                        {{"certificate", metadata_.at("certificate")},
                         {"issuer_signature", metadata_.at("issuer_signature")},
                         {"device_grant", delegation},
                         {"delegation_signature", claims.at("s")},
                         {"device_id", delegation.at("r")}},
                        false);
  grant_ = call("identity/prove",
                {{"challenge_id", challenge.at("challenge_id")},
                 {"signature",
                  sign_delegated_message(identity_, challenge.at("message"))}},
                false);
  if (grant_.at("id") != claims.at("a"))
    throw Failure(-32030, "Control account mismatch");
  presence_ = call("presence/open", {{"idempotency_key", random_id(12)}})
                  .at("session_id");
}
Profile ControlClient::endpoint(const Json &item) {
  Profile profile = identity_;
  auto address = item.at("endpoint").get<std::string>();
  auto split = address.rfind(':');
  if (split == std::string::npos)
    throw Failure(-32030, "Invalid managed endpoint");
  profile.host = address.substr(0, split);
  if (profile.host.starts_with('[') && profile.host.ends_with(']'))
    profile.host = profile.host.substr(1, profile.host.size() - 2);
  profile.port = std::stoi(address.substr(split + 1));
  profile.key = ssh_key(item.at("public_key"));
  profile.token = grant_.at("token");
  profile.name = item.value(
      "region", metadata_.value("region", std::string("Managed node")));
  return profile;
}
Profile ControlClient::discovery() {
  std::lock_guard guard(mutex_);
  auto candidates =
      call("placement/candidates",
           {{"region", metadata_.value("region", std::string("cn-north"))},
            {"operator", metadata_.value("operator", std::string("unknown"))}})
          .at("candidates");
  if (candidates.empty())
    throw Failure(-32022, "No managed node is ready");
  return endpoint(candidates[0]);
}
Json ControlClient::probe_candidates(const Json &candidates,
                                     bool preferred_only) {
  Json measurements = Json::array();
  Json rows = Json::array();
  for (const auto &candidate : candidates.at("candidates")) {
    const auto id = candidate.at("node_id").get<std::string>();
    Json row = {{"index", rows.size()},
                {"id", id},
                {"name", endpoint(candidate).name},
                {"region", candidate.value("region", std::string{})},
                {"selected", id == preferred_node_},
                {"reachable", false},
                {"rtt_ms", nullptr}};
    if (preferred_only && !preferred_node_.empty() && id != preferred_node_) {
      for (const auto &previous : route_rows_)
        if (previous.at("id") == id) {
          row["reachable"] = previous.at("reachable");
          row["rtt_ms"] = previous.at("rtt_ms");
          break;
        }
      rows.push_back(std::move(row));
      continue;
    }
    try {
      const auto started = Clock::now();
      Ssh ssh(endpoint(candidate), "probe", {}, 2, {}, true);
      const int rtt = std::max(1, int(std::chrono::duration<double, std::milli>(
                                          Clock::now() - started)
                                          .count()));
      measurements.push_back({{"node_id", id}, {"rtt_ms", rtt}});
      row["reachable"] = true;
      row["rtt_ms"] = rtt;
    } catch (...) {
    }
    rows.push_back(std::move(row));
  }
  route_rows_ = std::move(rows);
  routes_checked_ = Clock::now();
  return measurements;
}
Json ControlClient::routes(bool refresh) {
  std::lock_guard guard(mutex_);
  if (refresh || route_rows_.empty() ||
      Clock::now() - routes_checked_ > std::chrono::seconds(30)) {
    auto candidates = call(
        "placement/candidates",
        {{"region", metadata_.value("region", std::string("cn-north"))},
         {"operator", metadata_.value("operator", std::string("unknown"))}});
    probe_candidates(candidates, false);
  }
  Json chosen = Json::object();
  for (auto &row : route_rows_) {
    row["selected"] = row.at("id") == preferred_node_;
    if (!preferred_node_.empty()) {
      if (row.at("selected") == true)
        chosen = row;
    } else if (row.at("reachable") == true &&
               (chosen.empty() ||
                row.at("rtt_ms").get<int>() < chosen.at("rtt_ms").get<int>())) {
      chosen = row;
    }
  }
  return {{"routes", route_rows_},
          {"managed", true},
          {"auto", preferred_node_.empty()},
          {"name", chosen.value("name", std::string{})},
          {"node_latency_ms", chosen.value("rtt_ms", Json(nullptr))}};
}
Json ControlClient::select_route(int index) {
  std::lock_guard guard(mutex_);
  if (index < -1 || index >= int(route_rows_.size()))
    throw Failure(-32602, "Invalid route index; refresh the node list");
  if (index >= 0 && route_rows_.at(index).at("reachable") != true)
    throw Failure(-32022, "Selected node is unreachable");
  preferred_node_ = index < 0
                        ? std::string{}
                        : route_rows_.at(index).at("id").get<std::string>();
  return routes(false);
}
Profile ControlClient::allocate(const std::string &room,
                                const std::string &secret, bool host) {
  std::lock_guard guard(mutex_);
  Json request = {{"idempotency_key", random_id(12)},
                  {"intent", host ? "new" : "join"},
                  {"room_id", room},
                  {"secret", secret},
                  {"slots", 1},
                  {"bandwidth", 256000}};
  if (host) {
    auto candidates = call(
        "placement/candidates",
        {{"region", metadata_.value("region", std::string("cn-north"))},
         {"operator", metadata_.value("operator", std::string("unknown"))}});
    // Reuse the same bounded authenticated probes used by the node picker.
    // Joining always follows the host; a manual preference affects new rooms.
    Json measurements = probe_candidates(candidates, true);
    if (measurements.empty())
      throw Failure(-32022, "No reachable managed node");
    request["probe_token"] = candidates.at("probe_token");
    request["measurements"] = measurements;
  }
  auto allocation = call("placement/reserve", request);
  auto encoded = allocation.at("ticket").get<std::string>();
  auto split = encoded.find('.');
  // HTTPS authenticates the service; the selected node independently verifies
  // this signature.
  auto ticket = Json::parse(native::encoding::unhex(encoded.substr(0, split)));
  auto profile = endpoint(ticket);
  profile.allocation_ticket = encoded;
  allocations_[room] = allocation;
  secrets_[room] = secret;
  return profile;
}
void ControlClient::release(const std::string &room) {
  std::lock_guard guard(mutex_);
  auto found = allocations_.find(room);
  if (found == allocations_.end())
    return;
  call("placement/release",
       {{"reservation_id", found->second.at("reservation_id")}});
  allocations_.erase(found);
  secrets_.erase(room);
  listings_.erase(room);
}
Json ControlClient::lookup(const Json &request) {
  std::lock_guard guard(mutex_);
  auto result = call(
      "identity/lookup",
      {{"identifier",
        request.value("query", request.value("identifier", std::string{}))}});
  Json players = Json::array();
  for (const auto &a : result.at("accounts"))
    players.push_back(
        {{"account", a.at("account_id")}, {"username", a.at("username")}});
  return {{"players", players}, {"accounts", players}};
}
Json ControlClient::rooms(const std::string &query) {
  std::lock_guard guard(mutex_);
  auto result = call("room/list", {{"query", query}});
  const auto measured = routes(false).at("routes");
  for (auto &room : result.at("rooms")) {
    const auto region = room.value("node_region", std::string{});
    room["node_name"] = region;
    room["node_latency_ms"] = nullptr;
    // Older controls expose region rather than a node ID. A sample is safe
    // only when that region identifies exactly one measured candidate.
    const Json *match = nullptr;
    int count = 0;
    for (const auto &route : measured)
      if (!region.empty() && route.value("region", std::string{}) == region) {
        match = &route;
        ++count;
      }
    if (count == 1 && match) {
      room["node_name"] = match->at("name");
      room["node_latency_ms"] = match->at("rtt_ms");
    }
  }
  return result;
}
Json ControlClient::resolve(const std::string &room) {
  std::lock_guard guard(mutex_);
  return call("room/resolve", {{"room_id", room}});
}
void ControlClient::publish(const std::string &room, const Json &info) {
  std::lock_guard guard(mutex_);
  const auto allocation = allocations_.find(room);
  if (allocation == allocations_.end() || !info.contains("public") ||
      !info.contains("require_invite"))
    return;
  Json game = Json::object();
  const auto metadata = info.value("game", Json::object());
  for (const auto *key : {"version", "loader", "loader_version", "protocol"})
    if (metadata.contains(key))
      game[key] = metadata[key];
  Json listing = {{"title", info.at("title")},
                  {"public", info.at("public")},
                  {"require_invite", info.at("require_invite")},
                  {"game", game},
                  {"availability", info.at("availability")},
                  {"connections", info.at("connections")},
                  {"capacity", info.at("capacity")}};
  if (listings_.contains(room) && listings_.at(room) == listing)
    return;
  call("room/publish",
       {{"room_id", room},
        {"reservation_id", allocation->second.at("reservation_id")},
        {"room_epoch", allocation->second.at("room_epoch")},
        {"secret", secrets_.at(room)},
        {"room", listing}});
  listings_[room] = std::move(listing);
}
Json ControlClient::social(const Json &params) {
  std::lock_guard guard(mutex_);
  auto op = params.at("operation").get<std::string>();
  auto fields = params.value("fields", Json::object());
  if (op == "presence")
    call("presence/refresh",
         {{"session_id", presence_}, {"sequence", ++sequence_}});
  else if (op == "offline")
    call("presence/close",
         {{"session_id", presence_}, {"sequence", ++sequence_}});
  else if (op == "join") {
    auto state = call("social/state", Json::object());
    for (const auto &item : state.at("invitations"))
      if (item.at("account") == params.at("target") &&
          item.at("id") == fields.at("ref") && item.contains("invite"))
        return {{"invite", item.at("invite")}};
    throw Failure(-32004, "Invitation expired");
  } else if (op != "state") {
    static const std::map<std::string, std::string> kinds = {
        {"request", "friend_request"},  {"accept", "friend_accept"},
        {"decline", "friend_reject"},   {"remove", "friend_remove"},
        {"invite", "invite"},           {"request_join", "join_request"},
        {"accept_join", "join_accept"}, {"decline_join", "join_reject"}};
    if (!kinds.contains(op))
      throw Failure(-32602, "Unsupported managed social operation");
    Json body = {{"event_id", random_id(12)},
                 {"target", params.at("target")},
                 {"kind", kinds.at(op)}};
    if (fields.contains("room")) {
      auto room = fields.at("room").get<std::string>();
      body["room_id"] = room;
      auto location =
          call(op == "request_join" ? "room/inspect" : "room/locate",
               {{"room_id", room}});
      body["room_epoch"] = location.at("epoch");
      if ((op == "invite" || op == "accept_join") && secrets_.contains(room))
        body["invite"] = "bugg1-" + secrets_.at(room);
      if (op == "accept_join" || op == "decline_join")
        body["request_event_id"] = fields.at("ref");
    }
    call("social/apply", body);
  }
  auto inbox = call("social/inbox", {{"after", inbox_cursor_}});
  Json cursors = Json::array();
  for (const auto &item : inbox.at("events"))
    cursors.push_back(item.at("cursor"));
  if (!cursors.empty()) {
    call("social/ack", {{"cursors", cursors}, {"state", "delivered"}});
    inbox_cursor_ = inbox.at("cursor");
  }
  return call("social/state", Json::object());
}
Profile ControlClient::reconnect(const std::string &room) {
  std::lock_guard guard(mutex_);
  auto found = allocations_.find(room);
  if (found == allocations_.end())
    throw Failure(-32004, "Room allocation closed");
  auto allocation =
      call("placement/reconnect",
           {{"reservation_id", found->second.at("reservation_id")}});
  auto encoded = allocation.at("ticket").get<std::string>();
  auto profile = endpoint(Json::parse(
      native::encoding::unhex(encoded.substr(0, encoded.find('.')))));
  profile.allocation_ticket = encoded;
  return profile;
}
} // namespace bridge
