// SPDX-License-Identifier: AGPL-3.0-or-later
#include "../../bridge/game_wire.hpp"
#include "../../bridge/server_list.hpp"
#include <functional>
#include <vector>

void add_game_wire_tests(std::vector<std::pair<std::string,std::function<void()>>>& cases){
    using namespace bridge::game;
    auto require=[](bool good){if(!good)throw std::runtime_error("game wire assertion failed");};
    auto reject=[require](auto action){bool failed=false;try{action();}catch(const std::exception&){failed=true;}require(failed);};
    cases.emplace_back("game_utf16_supplementary_roundtrip",[require]{
        std::string value="A\xe4\xb8\xad\xe6\x96\x87 \xf0\x9f\x98\x80";
        require(utf16(value).size()==6);require(utf8(utf16(value))==value);
    });
    cases.emplace_back("game_integer_bounds_and_fragment_rejection",[require,reject]{
        for(uint32_t number:{0u,127u,128u,2147483647u,4294967295u}){
            auto bytes=integer(number);Cursor cursor{bytes};require(cursor.integer()==number);
            if(bytes.size()>1)reject([&]{Cursor partial{std::string_view(bytes).substr(0,bytes.size()-1)};partial.integer();});
        }
        std::string invalid(5,'\xff');reject([&]{Cursor cursor{invalid};cursor.integer();});
    });
    cases.emplace_back("game_string_utf16_limit_and_bad_utf8",[reject]{
        auto bytes=text("\xf0\x9f\x98\x80");reject([&]{Cursor cursor{bytes};cursor.text(1);});
        auto invalid=std::string("\x02\xc0\x80",3);reject([&]{Cursor cursor{invalid};cursor.text();});
    });
    cases.emplace_back("game_nbt_rejects_unbounded_list_and_nesting",[reject]{
        auto list=std::string("\x09\x08\xff\xff\xff\xff",6);
        reject([&]{Cursor cursor{list};cursor.nbt();});
        std::string nested;for(unsigned i=0;i<34;++i)nested+=std::string("\x0a\x0a\0\x01x",5).substr(i?1:0);
        reject([&]{Cursor cursor{nested};cursor.nbt();});
        std::string truncated("\x08\0\x05x",4);reject([&]{Cursor cursor{truncated};cursor.nbt();});
    });
    cases.emplace_back("server_status_fragments_preserve_mod_handshake_and_ping",[require]{
        bridge::Json original{{"version",{{"name","1.21.4"},{"protocol",769}}},
            {"players",{{"online",2},{"max",8},{"sample",bridge::Json::array()}}},
            {"forgeData",{{"mods",bridge::Json::array({"example"})}}}};
        const auto body=integer(0)+text(original.dump());
        const auto packet=integer(static_cast<uint32_t>(body.size()))+body;
        const std::string pong("\x09\x01" "abcdefgh",10);
        for(size_t split=1;split<packet.size();++split){
            bridge::ServerListStatus status;status.active=true;
            auto output=status.feed(std::string_view(packet).substr(0,split),{{"title","My Room"}},"data:image/png;base64,test");
            require(output.empty());
            output=status.feed(packet.substr(split)+pong,{{"title","My Room"}},"data:image/png;base64,test");
            Cursor cursor{output};const auto size=cursor.integer();const auto end=cursor.offset+size;
            require(cursor.integer()==0);const auto value=bridge::Json::parse(cursor.text());
            require(value["version"]==original["version"]&&value["forgeData"]==original["forgeData"]);
            require(value["players"]["online"]==2&&value["players"]["max"]==8);
            require(value["players"]["sample"].size()==1&&value.contains("favicon"));
            require(output.substr(end)==pong&&!status.active);
            require(status.feed(pong,{},"")==pong);
        }
        bridge::ServerListStatus invalid;invalid.active=true;
        const std::string custom("\x03\x00\x01!",4);
        require(invalid.feed(custom,{},"")==custom);
    });
    cases.emplace_back("game_chat_controls_are_rejected",[require]{
        require(plain_text("hello"));require(!plain_text("hello\nworld"));
        require(!plain_text("\xc2\xa7" "cInjected"));require(!plain_text(std::string(1025,'x')));
    });
}
