// SPDX-License-Identifier: AGPL-3.0-or-later
#pragma once
#include "game_wire.hpp"
namespace bridge::game {
// Pre-Netty packets have no length prefix. Consume every field with a strict
// budget, including fields not used by the independent chat participant.
class LegacyReader {
    std::function<std::string(size_t)> read_;
    size_t remaining_=maximum_packet;
public:
    explicit LegacyReader(std::function<std::string(size_t)> reader):read_(std::move(reader)){}
    std::string take(size_t count){if(count>remaining_)throw Failure(-32602,"Legacy packet exceeds limit");remaining_-=count;return read_(count);}
    uint64_t number(size_t width){auto bytes=take(width);return Cursor{bytes}.number(width);}
    size_t count(size_t width,size_t maximum=65536){auto n=number(width);if(n>maximum)throw Failure(-32602,"Legacy array exceeds limit");return static_cast<size_t>(n);}
    Json field(const std::string& kind){
        if(kind=="double"){auto bytes=take(8);return Cursor{bytes}.real64();}
        if(kind=="float"){auto bytes=take(4);return Cursor{bytes}.real32();}
        if(kind=="int")return static_cast<int32_t>(number(4));
        if(kind=="short")return static_cast<int16_t>(number(2));
        if(kind=="ushort")return number(2);
        if(kind=="long")return number(8);
        if(kind=="byte")return static_cast<int8_t>(number(1));
        if(kind=="ubyte"||kind=="bool")return number(1);
        if(kind=="string"||kind=="ustring"||kind=="ascii"){
            auto n=count(2,32767);if(kind=="ascii")return take(n);std::u16string result;for(size_t i=0;i<n;++i)result+=static_cast<char16_t>(number(2));return utf8(result);
        }
        if(kind=="byteArray16"||kind=="byteArray32")return Json::binary(std::vector<uint8_t>{}); // handled by packet() for crypto challenges
        if(kind=="slot"){auto item=static_cast<int16_t>(number(2));if(item!=-1){take(3);auto n=static_cast<int16_t>(number(2));if(n!=-1){if(n<0)throw Failure(-32602,"Invalid slot");take(static_cast<size_t>(n));}}return item;}
        if(kind=="slotArray"||kind=="stringArray"){auto n=count(2,4096);for(size_t i=0;i<n;++i)field(kind=="slotArray"?"slot":"string");}
        else if(kind=="intArray8")take(4*count(1,255));
        else if(kind=="byteVectorArray")take(3*count(4));
        else if(kind=="objectData"){if(number(4))take(6);}
        else if(kind=="mapChunkBulk"){auto n=count(2,4096),size=count(4,maximum_packet);take(1+size+12*n);}
        else if(kind=="propertyArray"){auto n=count(4,4096);for(size_t i=0;i<n;++i){field("string");take(8);take(25*count(2,4096));}}
        else if(kind=="entityMetadata"){const char* types[]={"byte","short","int","float","string","slot","intVector"};for(unsigned i=0;i<256;++i){auto marker=number(1);if(marker==127)return nullptr;if((marker>>5)>=7)throw Failure(-32602,"Invalid metadata type");field(types[marker>>5]);}throw Failure(-32602,"Legacy metadata exceeds limit");}
        else if(kind=="intVector"||kind=="byteVector")take(kind=="intVector"?12:3);
        else throw Failure(-32602,"Unknown legacy field");
        return nullptr;
    }
    std::pair<unsigned,Json> packet(const Json& definitions){
        remaining_=maximum_packet;auto id=static_cast<unsigned>(number(1));auto key=std::to_string(id);
        if(!definitions.contains(key))throw Failure(-32602,"Unknown legacy packet");Json value=Json::object();
        for(const auto& item:definitions.at(key)){
            auto name=item.at("name").is_string()?item.at("name").get<std::string>():std::string{};auto kind=item.at("type").get<std::string>();
            if(id==0xcf&&(name=="scoreName"||name=="value")&&value.value("remove",0))continue;
            if(id==0xd1){auto mode=value.value("mode",-1);if((name=="name"||name=="prefix"||name=="suffix"||name=="friendlyFire")&&mode!=0&&mode!=2)continue;if(name=="players"&&mode!=0&&mode!=3&&mode!=4)continue;}
            if(kind=="byteArray16"||kind=="byteArray32"){auto bytes=take(count(kind=="byteArray16"?2:4,maximum_packet));value[name]=bytes;}
            else value[name]=field(kind);
        }
        if(id==0x64&&value.value("inventoryType",0)==11)take(4);
        return {id,std::move(value)};
    }
};
}
