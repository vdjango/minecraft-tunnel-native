// SPDX-License-Identifier: AGPL-3.0-or-later
#pragma once
#include "ssh.hpp"
#include <map>
#include <mutex>
namespace bridge {
// Rpc owns this adapter. It calls the control protocol; it never implements
// placement scores.
class ControlClient {
  Profile identity_;
  std::recursive_mutex mutex_;
  Json metadata_, grant_;
  std::string presence_;
  uint64_t sequence_ = 0;
  int64_t inbox_cursor_ = 0;
  std::map<std::string, Json> allocations_;
  std::map<std::string, std::string> secrets_;
  std::map<std::string, Json> listings_;
  Json route_rows_ = Json::array();
  std::string preferred_node_;
  Clock::time_point routes_checked_{};
  Json call(const std::string &, const Json &, bool authenticated = true);
  Profile endpoint(const Json &);
  Json probe_candidates(const Json &, bool preferred_only);

public:
  explicit ControlClient(const Profile &);
  Profile discovery();
  Json routes(bool refresh = false);
  Json select_route(int index);
  Profile allocate(const std::string &room, const std::string &secret,
                   bool host);
  Profile reconnect(const std::string &room);
  Json social(const Json &);
  Json lookup(const Json &);
  Json rooms(const std::string &query = {});
  Json resolve(const std::string &room);
  // Sync only node-confirmed host descriptions. Same values do not write again;
  // failed HTTPS calls leave the cache unchanged for the existing poll to
  // retry.
  void publish(const std::string &room, const Json &info);
  void release(const std::string &room);
};
} // namespace bridge
