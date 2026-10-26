// SPDX-License-Identifier: AGPL-3.0-or-later
#pragma once
#include "ssh.hpp"
#include <filesystem>
#include <fstream>
#include <rtc/rtc.hpp>
#include <deque>
#include <mutex>
#include <openssl/evp.h>
#include <unordered_map>

namespace bridge {
class ModTransfers {
    struct Transfer;
    std::filesystem::path root_;
    Json catalog_=Json::array();
    bool configured_=false;
    bool allow_transfers_=false;
    std::unordered_map<std::string,std::shared_ptr<Transfer>> transfers_;
    std::vector<std::shared_ptr<Transfer>> serving_;
    void bind_peer_channel(const std::shared_ptr<Transfer>&);
    void pump(Transfer&,Ssh&);
    void pump_peer(Transfer&);
public:
    ModTransfers();
    ~ModTransfers();
    void configure(const std::string& directory);
    void set_allowed(bool allowed){allow_transfers_=allowed;}
    Json metadata() const;
    void accept(ssh_channel channel);
    void accept_peer(std::shared_ptr<rtc::DataChannel> channel);
    Json command(const std::string& method,const Json& params,const std::string& room,bool host,
                 std::shared_ptr<rtc::DataChannel> peer_channel={});
    void tick(Ssh&);
    void disconnect();
};
}
