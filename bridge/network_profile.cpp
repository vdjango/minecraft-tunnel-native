// SPDX-License-Identifier: AGPL-3.0-or-later
#include "network_profile.hpp"
#include "journal.hpp"
#include <fstream>
#include <mutex>
#include <openssl/rand.h>
#include <set>
#ifdef _WIN32
#include <windows.h>
#endif
namespace bridge {
namespace {
struct State {
  std::mutex mutex;
  Json value{{"type", "unknown"},
             {"observed_at", 0},
             {"direct_successes", 0},
             {"relay_successes", 0}};
  Clock::time_point checked{};
  bool dirty = false;
  State() {
    try {
      const auto path = Journal::default_root() / "network-profile.json";
      if (std::filesystem::file_size(path) > 2048)
        return;
      auto saved = Json::parse(std::ifstream(path));
      // A previous network's mapping cannot describe the current connection.
      for (const auto *field : {"direct_successes", "relay_successes"})
        if (saved.contains(field) && saved[field].is_number_integer() &&
            saved[field].get<int64_t>() >= 0 &&
            saved[field].get<int64_t>() <= 1000000000)
          value[field] = saved[field];
    } catch (...) {
    }
  }
};
State &state() {
  static State value;
  return value;
}
std::string mapping(std::span<const unsigned char> response) {
  const auto length = static_cast<size_t>((response[2] << 8) | response[3]);
  for (size_t offset = 20; offset + 4 <= 20 + length;) {
    const auto type = (response[offset] << 8) | response[offset + 1];
    const auto size =
        static_cast<size_t>((response[offset + 2] << 8) | response[offset + 3]);
    if (offset + 4 + size > 20 + length)
      return {};
    if ((type == 0x20 || type == 1) && size >= 8 && response[offset + 5] == 1) {
      auto port = (response[offset + 6] << 8) | response[offset + 7];
      if (type == 0x20)
        port ^= 0x2112;
      std::string result = std::to_string(port);
      for (size_t index = 0; index < 4; ++index)
        result +=
            ":" + std::to_string(response[offset + 8 + index] ^
                                 (type == 0x20 ? response[4 + index] : 0));
      return result;
    }
    offset += 4 + ((size + 3) & ~size_t(3));
  }
  return {};
}
std::string probe(const std::vector<Profile> &endpoints) {
  // Keep the same local port for every destination. A socket per server would
  // make differing mappings meaningless. No filtering/full-cone claim is made.
  const auto fd = socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
  if (fd == invalid_socket)
    return "unknown";
  struct Socket {
    NetSocket fd;
    ~Socket() { close_socket(fd); }
  } cleanup{fd};
  nonblocking(fd);
  struct Target {
    sockaddr_in address;
    std::array<unsigned char, 20> request;
    std::string result;
  };
  std::vector<Target> targets;
  std::set<std::pair<uint32_t, uint16_t>> unique;
  for (const auto &endpoint : endpoints) {
    if (targets.size() >= 3)
      break;
    addrinfo hints{};
    hints.ai_family = AF_INET;
    hints.ai_socktype = SOCK_DGRAM;
    addrinfo *raw = nullptr;
    if (getaddrinfo(endpoint.host.c_str(),
                    std::to_string(endpoint.port).c_str(), &hints, &raw))
      continue;
    std::unique_ptr<addrinfo, decltype(&freeaddrinfo)> addresses(raw,
                                                                 freeaddrinfo);
    if (!raw)
      continue;
    const auto address = *reinterpret_cast<sockaddr_in *>(raw->ai_addr);
    const auto ip = ntohl(address.sin_addr.s_addr);
    if (!ip || (ip >> 24) == 127 ||
        !unique.emplace(address.sin_addr.s_addr, address.sin_port).second)
      continue;
    Target target{address, {0, 1, 0, 0, 0x21, 0x12, 0xa4, 0x42}, {}};
    if (RAND_bytes(target.request.data() + 8, 12) != 1)
      continue;
    targets.push_back(target);
  }
  const auto send = [&] {
    for (auto &target : targets)
      if (target.result.empty())
        sendto(fd, reinterpret_cast<const char *>(target.request.data()), 20, 0,
               reinterpret_cast<const sockaddr *>(&target.address),
               sizeof(target.address));
  };
  send();
  const auto deadline = Clock::now() + std::chrono::milliseconds(1500);
  auto retry = Clock::now() + std::chrono::milliseconds(500);
  while (!targets.empty() && Clock::now() < deadline) {
    PollFd descriptor{fd, POLLIN, 0};
    if (net_poll(&descriptor, 1, 50) > 0 && (descriptor.revents & POLLIN)) {
      std::array<unsigned char, 2048> response{};
      sockaddr_in source{};
      SockLen length = sizeof(source);
      const auto count =
          recvfrom(fd, reinterpret_cast<char *>(response.data()),
                   static_cast<int>(response.size()), 0,
                   reinterpret_cast<sockaddr *>(&source), &length);
      if (count > 0)
        for (auto &target : targets)
          if (source.sin_addr.s_addr == target.address.sin_addr.s_addr &&
              source.sin_port == target.address.sin_port &&
              stun_response_matches(
                  {response.data(), static_cast<size_t>(count)},
                  target.request))
            target.result =
                mapping({response.data(), static_cast<size_t>(count)});
    }
    if (std::all_of(targets.begin(), targets.end(),
                    [](const auto &item) { return !item.result.empty(); }))
      break;
    if (Clock::now() >= retry) {
      send();
      retry = deadline;
    }
  }
  std::set<std::string> mappings, external_ips;
  std::set<uint32_t> server_ips;
  size_t received = 0;
  for (const auto &target : targets)
    if (!target.result.empty()) {
      mappings.insert(target.result);
      external_ips.insert(target.result.substr(target.result.find(':') + 1));
      server_ips.insert(target.address.sin_addr.s_addr);
      ++received;
    }
  // Same-server/different-port observations do not establish destination
  // independence. Different public addresses may indicate multiple egresses;
  // the old launcher's classifier deliberately did not call that symmetric NAT.
  return server_ips.size() > 1 && external_ips.size() == 1 && mappings.size() > 1 ? "mapping-dependent-observed"
         : server_ips.size() > 1 && mappings.size() == 1 ? "mapping-stable-observed"
         : received          ? "udp-reachable"
                             : "unknown";
}
} // namespace
bool stun_response_matches(std::span<const unsigned char> response,
                           std::span<const unsigned char> request) {
  if (response.size() < 20 || request.size() < 20 || response[0] != 1 ||
      response[1] != 1 ||
      !std::equal(request.begin() + 4, request.begin() + 20,
                  response.begin() + 4))
    return false;
  const auto body = static_cast<size_t>((response[2] << 8) | response[3]);
  return body % 4 == 0 && body + 20 == response.size();
}
void network_connected(bool direct) {
  auto &owner = state();
  std::lock_guard guard(owner.mutex);
  const auto field = direct ? "direct_successes" : "relay_successes";
  owner.value[field] =
      std::min<int64_t>(1000000000, owner.value[field].get<int64_t>() + 1);
  owner.dirty = true;
}
Json network_profile(const std::vector<Profile> &endpoints, bool refresh) {
  auto &owner = state();
  bool inspect;
  {
    std::lock_guard guard(owner.mutex);
    inspect = !endpoints.empty() &&
              (refresh || owner.checked == Clock::time_point{} ||
               Clock::now() - owner.checked >= std::chrono::minutes(5));
  }
  if (inspect) {
    const auto type = probe(endpoints);
    std::lock_guard guard(owner.mutex);
    owner.value["type"] = type;
    owner.value["observed_at"] =
        std::chrono::duration_cast<std::chrono::seconds>(
            std::chrono::system_clock::now().time_since_epoch())
            .count();
    owner.checked = Clock::now();
    owner.dirty = true;
  }
  std::lock_guard guard(owner.mutex);
  bool persisted = !owner.dirty;
  if (owner.dirty)
    try {
      const auto path = Journal::default_root() / "network-profile.json";
      std::filesystem::create_directories(path.parent_path());
      auto temporary = path;
      temporary += ".tmp";
      {
        std::ofstream output(temporary, std::ios::binary | std::ios::trunc);
        output << owner.value.dump();
        output.close();
        if (!output)
          throw std::runtime_error("profile_write");
      }
#ifdef _WIN32
      if (!MoveFileExW(temporary.c_str(), path.c_str(),
                       MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH))
        throw std::runtime_error("profile_replace");
#else
      std::filesystem::rename(temporary, path);
#endif
      owner.dirty = false;
      persisted = true;
    } catch (...) {
    }
  auto result = owner.value;
  result["persisted"] = persisted;
  return result;
}
} // namespace bridge
