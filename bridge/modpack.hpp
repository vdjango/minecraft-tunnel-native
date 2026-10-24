// SPDX-License-Identifier: AGPL-3.0-or-later
#pragma once
#include "ssh.hpp"
#include <filesystem>
#include <fstream>
#include <openssl/evp.h>
#include <unordered_map>

namespace bridge {
class ModTransfers {
    struct Transfer;
    std::filesystem::path root_;
    Json catalog_=Json::array();
    bool configured_=false;
    std::unordered_map<std::string,std::unique_ptr<Transfer>> transfers_;
    std::vector<std::unique_ptr<Transfer>> serving_;
    void pump(Transfer&,Ssh&);
public:
    ModTransfers();
    ~ModTransfers();
    void configure(const std::string& directory);
    Json metadata() const;
    void accept(ssh_channel channel);
    Json command(const std::string& method,const Json& params,const std::string& room,bool host);
    void tick(Ssh&);
    void disconnect();
};
}
