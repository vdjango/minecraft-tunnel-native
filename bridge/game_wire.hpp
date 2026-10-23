// SPDX-License-Identifier: AGPL-3.0-or-later
#pragma once
#include "ssh.hpp"
#include <bit>
#include <codecvt>
#include <locale>
namespace bridge::game {
constexpr size_t maximum_packet=2*1024*1024;
inline std::string integer(uint32_t value){std::string bytes;do{auto b=value&127;value>>=7;bytes+=static_cast<char>(b|(value?128:0));}while(value);return bytes;}
inline std::string text(std::string_view value){return integer(static_cast<uint32_t>(value.size()))+std::string(value);}
inline std::string number(uint64_t value,size_t width){std::string bytes(width,'\0');for(size_t i=0;i<width;++i){bytes[width-i-1]=static_cast<char>(value&255);value>>=8;}return bytes;}
inline std::string real(double value){return number(std::bit_cast<uint64_t>(value),8);}
inline std::string real(float value){return number(std::bit_cast<uint32_t>(value),4);}
inline std::u16string utf16(const std::string& value){return std::wstring_convert<std::codecvt_utf8_utf16<char16_t>,char16_t>{}.from_bytes(value);}
inline std::string utf8(const std::u16string& value){return std::wstring_convert<std::codecvt_utf8_utf16<char16_t>,char16_t>{}.to_bytes(value);}
inline std::string legacy_text(const std::string& value){auto units=utf16(value);std::string result=number(units.size(),2);for(auto unit:units)result+=number(unit,2);return result;}
inline bool plain_text(const std::string& text,size_t limit=256){
    if(text.empty())return false;
    auto units=utf16(text);if(units.size()>limit)return false;
    for(auto ch:units)if(ch<32||(ch>=127&&ch<=159)||ch==0xa7||ch==0x2028||ch==0x2029)return false;
    return true;
}
struct Cursor {
    std::string_view data;size_t offset=0;
    std::string_view take(size_t count){if(count>data.size()-offset)throw Failure(-32602,"Truncated game packet");auto bytes=data.substr(offset,count);offset+=count;return bytes;}
    uint64_t number(size_t width){uint64_t result=0;for(unsigned char byte:take(width))result=(result<<8)|byte;return result;}
    uint32_t integer(){uint32_t result=0;for(unsigned i=0;i<5;++i){auto b=number(1);if(i==4&&(b&0xf0))throw Failure(-32602,"Invalid game integer");result|=static_cast<uint32_t>(b&127)<<(7*i);if(!(b&128))return result;}throw Failure(-32602,"Invalid game integer");}
    std::string text(size_t maximum=32767){auto size=integer();if(size>maximum*4)throw Failure(-32602,"Game string exceeds limit");std::string value(take(size));if(utf16(value).size()>maximum)throw Failure(-32602,"Game string exceeds limit");return value;}
    double real64(){return std::bit_cast<double>(number(8));}
    float real32(){return std::bit_cast<float>(static_cast<uint32_t>(number(4)));}
    Json nbt(int tag=-1,unsigned depth=0){
        if(depth>32)throw Failure(-32602,"NBT nesting exceeds limit");if(tag<0)tag=static_cast<int>(number(1));
        if(tag==0)return nullptr;
        if(tag>=1&&tag<=6){constexpr size_t widths[]={0,1,2,4,8,4,8};take(widths[tag]);return nullptr;}
        if(tag==8)return std::string(take(static_cast<size_t>(number(2))));
        if(tag==7||tag==11||tag==12){auto count=number(4);if(count>65536)throw Failure(-32602,"NBT array exceeds limit");take(static_cast<size_t>(count)*(tag==7?1:tag==11?4:8));return nullptr;}
        if(tag==9){auto child=number(1),count=number(4);if(count>4096)throw Failure(-32602,"NBT list exceeds limit");Json values=Json::array();for(uint64_t i=0;i<count;++i)values.push_back(nbt(static_cast<int>(child),depth+1));return values;}
        if(tag==10){Json value=Json::object();for(unsigned i=0;i<4096;++i){auto child=number(1);if(!child)return value;auto key=std::string(take(static_cast<size_t>(number(2))));value[key]=nbt(static_cast<int>(child),depth+1);}throw Failure(-32602,"NBT compound exceeds limit");}
        throw Failure(-32602,"Unknown NBT tag");
    }
};
inline std::string plain(const Json& value){
    if(value.is_string())return value.get<std::string>();std::string result;
    if(value.is_array()){for(const auto& item:value)result+=plain(item);return result;}
    if(!value.is_object())return result;
    auto key=value.value("translate",std::string{});
    if(key=="chat.type.text"||key=="chat.type.announcement"){auto list=value.value("with",value.value("using",Json::array()));if(list.is_array()&&list.size()==2)return "<"+plain(list[0])+"> "+plain(list[1]);}
    if(value.contains("text")&&value["text"].is_string())result=value["text"].get<std::string>();
    if(value.contains("extra"))result+=plain(value["extra"]);return result;
}
}
