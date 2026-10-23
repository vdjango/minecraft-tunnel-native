// SPDX-License-Identifier: AGPL-3.0-or-later
#pragma once
#include "ssh.hpp"
namespace bridge {
// An independent native participant. It never parses or edits player streams.
class GameChat {
    struct Impl;
    std::unique_ptr<Impl> impl_;
public:
    using Callback=std::function<void(Json)>;
    GameChat(int port,int protocol,std::string name,Callback callback);
    ~GameChat();
    void send(const std::string& sender,const std::string& text);
    bool ready() const;
};
}
