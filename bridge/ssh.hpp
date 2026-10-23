// SPDX-License-Identifier: AGPL-3.0-or-later
#pragma once
#include "../core/net.hpp"
#include "../core/heartbeat.hpp"
#include <libssh/libssh.h>
#include <nlohmann/json.hpp>
#include <chrono>
#include <memory>
#include <functional>
namespace bridge {
using Json = nlohmann::json;
using Clock = std::chrono::steady_clock;
struct Failure : std::runtime_error {
    int code;
    Failure(int number, const char* message):std::runtime_error(message),code(number){}
};
struct Profile {
    std::string host, token, key, name;
    int port = 0;
    static Profile decode(const std::string& code);
    static Profile from_json(const Json& value);
};
std::string random_id(size_t count = 18);
std::string room_id(const std::string& secret);
std::string invitation(const std::string& value);
void log(const std::string& event, const Json& fields = Json::object());
class Ssh {
    ssh_session session_ = nullptr;
    ssh_counter_struct traffic_{};
    Heartbeat heartbeat_;
public:
    explicit Ssh(const Profile&, const std::string& user, const std::string& secret = {},int timeout_seconds=4);
    ~Ssh();
    Ssh(const Ssh&) = delete;
    ssh_session get() const { return session_; }
    NetSocket fd() const { return static_cast<NetSocket>(ssh_get_fd(session_)); }
    bool alive() const { return ssh_is_connected(session_) != 0; }
    bool heartbeat();
    int retry(const std::function<int()>& operation, int seconds = 5);
    ssh_channel subsystem(const char* name);
    Json request(Json value,int timeout_seconds=10);
    int forward(const char* name);
    ssh_channel open(const std::string& room, int port);
};
void free_channel(ssh_channel& channel);
}
