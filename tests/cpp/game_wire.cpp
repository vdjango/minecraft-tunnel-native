// SPDX-License-Identifier: AGPL-3.0-or-later
#include "../../bridge/game_wire.hpp"
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
    cases.emplace_back("game_chat_controls_are_rejected",[require]{
        require(plain_text("hello"));require(!plain_text("hello\nworld"));
        require(!plain_text("\xc2\xa7" "cInjected"));require(!plain_text(std::string(1025,'x')));
    });
}
