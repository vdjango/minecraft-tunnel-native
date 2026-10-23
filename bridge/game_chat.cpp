// SPDX-License-Identifier: AGPL-3.0-or-later
#include "game_chat.hpp"
#include "game_wire.hpp"
#include "game_legacy.hpp"
#include "protocol_data.hpp"
#include <openssl/rand.h>
#include <openssl/rsa.h>
#include <openssl/x509.h>
#include <zlib.h>
#include <array>
#include <atomic>
#include <condition_variable>
#include <deque>
#include <mutex>
#include <regex>
#include <thread>
namespace bridge {
namespace {
using namespace game;
const Json& profiles(){static const auto value=Json::parse(profiles_json);return value;}
const Json& legacy(){static const auto value=Json::parse(legacy_json);return value;}
using Cipher=std::unique_ptr<EVP_CIPHER_CTX,decltype(&EVP_CIPHER_CTX_free)>;
std::string crypt(EVP_CIPHER_CTX* context,std::string bytes){if(!context)return bytes;std::string output(bytes.size()+16,'\0');int count=0;if(EVP_CipherUpdate(context,reinterpret_cast<unsigned char*>(output.data()),&count,reinterpret_cast<const unsigned char*>(bytes.data()),static_cast<int>(bytes.size()))!=1)throw Failure(-32602,"Game cipher failed");output.resize(static_cast<size_t>(count));return output;}
std::string rsa(EVP_PKEY* key,const std::string& bytes){auto context=std::unique_ptr<EVP_PKEY_CTX,decltype(&EVP_PKEY_CTX_free)>(EVP_PKEY_CTX_new(key,nullptr),EVP_PKEY_CTX_free);size_t size=0;if(!context||EVP_PKEY_encrypt_init(context.get())<=0||EVP_PKEY_CTX_set_rsa_padding(context.get(),RSA_PKCS1_PADDING)<=0||EVP_PKEY_encrypt(context.get(),nullptr,&size,reinterpret_cast<const unsigned char*>(bytes.data()),bytes.size())<=0||size>4096)throw Failure(-32602,"Invalid game public key");std::string result(size,'\0');if(EVP_PKEY_encrypt(context.get(),reinterpret_cast<unsigned char*>(result.data()),&size,reinterpret_cast<const unsigned char*>(bytes.data()),bytes.size())<=0)throw Failure(-32602,"Game encryption failed");result.resize(size);return result;}
}
struct GameChat::Impl {
    int port,protocol,compression=-1;std::string name,uuid,state="login";Json profile;
    Callback callback;std::thread thread;std::atomic<bool> running{true},ready{false};
    NetSocket socket=invalid_socket;std::mutex mutex;std::condition_variable changed;
    std::deque<std::pair<std::string,std::string>> messages;
    Cipher cipher{nullptr,EVP_CIPHER_CTX_free},decipher{nullptr,EVP_CIPHER_CTX_free};
    std::array<double,3> position{};std::array<float,2> rotation{};bool has_position=false;
    unsigned signed_pending=0;std::deque<std::pair<std::string,std::string>> signatures;
    Clock::time_point received=Clock::now(),next_message=Clock::now();
    Impl(int p,int version,std::string identity,Callback events):port(p),protocol(version),name(std::move(identity)),callback(std::move(events)){
        if(name.empty())name="BuggChat"+room_id(random_id()).substr(0,8);
        if(!std::regex_match(name,std::regex("[A-Za-z][A-Za-z0-9_]{2,15}")))throw Failure(-32602,"Invalid chat participant name");
        if(protocol!=78&&!profiles().contains(std::to_string(protocol)))throw Failure(-32602,"Unsupported game chat protocol");
        if(protocol!=78)profile=profiles().at(std::to_string(protocol));
        uuid.resize(16);unsigned size=0;auto source="OfflinePlayer:"+name;
        if(EVP_Digest(source.data(),source.size(),reinterpret_cast<unsigned char*>(uuid.data()),&size,EVP_md5(),nullptr)!=1)throw Failure(-32602,"Game identity unavailable");
        uuid[6]=static_cast<char>((uuid[6]&15)|48);uuid[8]=static_cast<char>((uuid[8]&63)|128);
        thread=std::thread([this]{run();});
    }
    ~Impl(){running=false;changed.notify_all();if(thread.joinable())thread.join();}
    void notice(const std::string& value){callback({{"type","bridge"},{"status",value}});}
    void enqueue(const std::string& sender,const std::string& value){
        if(!plain_text(sender,32)||!plain_text(value))throw Failure(-32602,"Invalid game chat text");
        std::lock_guard guard(mutex);if(!ready)throw Failure(-32000,"Game chat unavailable; message remains in lobby");
        if(messages.size()>=100)throw Failure(-32010,"Game chat queue full");messages.emplace_back(sender,value);
    }
    void wait(short events){
        if(!running)throw Failure(-32004,"Game chat stopped");
        PollFd descriptor{socket,events,0};net_poll(&descriptor,1,50);
        if(Clock::now()-received>std::chrono::seconds(45))throw Failure(-32000,"Game chat timed out");
    }
    void write(std::string bytes){
        bytes=crypt(cipher.get(),std::move(bytes));size_t offset=0;
        while(offset<bytes.size()){if(!running)throw Failure(-32004,"Game chat stopped");auto n=net_send(socket,bytes.data()+offset,static_cast<int>(bytes.size()-offset));if(n>0)offset+=static_cast<size_t>(n);else if(n<0&&would_block())wait(POLLOUT);else throw Failure(-32000,"Game chat disconnected");}
    }
    std::string read(size_t count){
        if(count>maximum_packet)throw Failure(-32602,"Game packet exceeds limit");std::string bytes(count,'\0');size_t offset=0;
        while(offset<count){if(!running)throw Failure(-32004,"Game chat stopped");auto n=recv(socket,bytes.data()+offset,static_cast<int>(count-offset),0);if(n>0){offset+=static_cast<size_t>(n);received=Clock::now();}else if(n<0&&would_block()){if(offset==0)send_messages();wait(POLLIN);}else throw Failure(-32000,"Game chat disconnected");}
        return crypt(decipher.get(),std::move(bytes));
    }
    unsigned read_integer(){uint32_t value=0;for(unsigned i=0;i<5;++i){auto byte=static_cast<unsigned char>(read(1)[0]);if(i==4&&(byte&0xf0))throw Failure(-32602,"Invalid game integer");value|=uint32_t(byte&127)<<(7*i);if(!(byte&128))return value;}throw Failure(-32602,"Invalid game integer");}
    void send_packet(const std::string& kind,std::string payload={}){
        const auto& ids=profile.at(state+"_toServer");if(!ids.contains(kind))throw Failure(-32602,"Unsupported game packet");
        auto packet=integer(ids.at(kind).get<unsigned>())+payload;
        if(compression>=0){if(packet.size()>=static_cast<size_t>(compression)){std::string encoded(compressBound(static_cast<uLong>(packet.size())),'\0');uLongf size=encoded.size();if(compress2(reinterpret_cast<Bytef*>(encoded.data()),&size,reinterpret_cast<const Bytef*>(packet.data()),static_cast<uLong>(packet.size()),1)!=Z_OK)throw Failure(-32602,"Game compression failed");encoded.resize(size);packet=integer(static_cast<unsigned>(packet.size()))+encoded;}else packet=std::string(1,'\0')+packet;}
        write(integer(static_cast<unsigned>(packet.size()))+packet);
    }
    std::pair<std::string,std::string> packet(){
        auto length=read_integer();if(!length||length>maximum_packet)throw Failure(-32602,"Game packet exceeds limit");auto bytes=read(length);
        if(compression>=0){Cursor cursor{bytes};auto size=cursor.integer();auto body=cursor.take(bytes.size()-cursor.offset);if(size){if(size>maximum_packet)throw Failure(-32602,"Inflated game packet exceeds limit");std::string decoded(size,'\0');z_stream z{};z.next_in=reinterpret_cast<Bytef*>(const_cast<char*>(body.data()));z.avail_in=static_cast<uInt>(body.size());z.next_out=reinterpret_cast<Bytef*>(decoded.data());z.avail_out=size;if(inflateInit(&z)!=Z_OK)throw Failure(-32602,"Inflater unavailable");auto result=inflate(&z,Z_FINISH);bool valid=result==Z_STREAM_END&&z.total_out==size&&z.avail_in==0;inflateEnd(&z);if(!valid)throw Failure(-32602,"Invalid compressed game packet");bytes=std::move(decoded);}else bytes=std::string(body);}
        Cursor cursor{bytes};auto id=cursor.integer();std::string kind;for(auto it=profile.at(state+"_toClient").begin();it!=profile.at(state+"_toClient").end();++it)if(it.value()==id){kind=it.key();break;}
        return {kind,std::string(cursor.take(bytes.size()-cursor.offset))};
    }
    Json component(Cursor& cursor){return protocol>=765&&state!="login"?cursor.nbt():Json::parse(cursor.text());}
    void settings(){std::string bytes=text("zh_cn")+std::string("\2\0\1",3);bytes+=protocol<=5?std::string("\0\1",2):std::string(1,'\0');if(protocol>=107)bytes+='\1';if(protocol>=755)bytes+='\0';if(protocol>=757)bytes+='\1';if(protocol>=768)bytes+='\2';send_packet("settings",bytes);}
    void send_position(){std::string bytes=real(position[0]);if(protocol<=5)bytes+=real(position[1]+1.62);bytes+=real(position[1])+real(position[2])+std::string(1,'\1');send_packet("position",bytes);if(profile.at("play_toServer").contains("client_tick_end"))send_packet("client_tick_end");}
    void position_packet(Cursor& cursor){
        std::array<double,3> xyz;std::array<float,2> angles;unsigned teleport=0,flags=0;
        if(protocol>=768){teleport=cursor.integer();for(auto& value:xyz)value=cursor.real64();cursor.take(24);for(auto& value:angles)value=cursor.real32();flags=static_cast<unsigned>(cursor.number(4));}
        else{for(auto& value:xyz)value=cursor.real64();for(auto& value:angles)value=cursor.real32();if(protocol>5)flags=static_cast<unsigned>(cursor.number(1));if(protocol>=107)teleport=cursor.integer();}
        for(unsigned i=0;i<3;++i)position[i]=xyz[i]+((flags&(1u<<i))?position[i]:0);for(unsigned i=0;i<2;++i)rotation[i]=angles[i]+((flags&(1u<<(i+3)))?rotation[i]:0);has_position=true;
        if(protocol>=107){auto bytes=integer(teleport);if(protocol>=777){for(auto value:position)bytes+=real(value);for(auto value:rotation)bytes+=real(value);}send_packet("teleport_confirm",bytes);}send_position();
        if(!ready){ready=true;if(profile.at("play_toServer").contains("player_loaded"))send_packet("player_loaded");notice("游戏聊天已同步");}
    }
    void public_message(std::string sender,std::string value){if(sender!=name&&plain_text(sender,32)&&plain_text(value))callback({{"op","game"},{"name",sender},{"text",value}});}
    void old_chat(Cursor& cursor,bool system){
        auto value=component(cursor);unsigned location=0;if(system&&protocol==759)location=cursor.integer();else if(cursor.offset<cursor.data.size())location=static_cast<unsigned>(cursor.number(1));if(location&&!(system&&protocol==759&&location==1))return;
        if(value.is_object()&&value.contains("translate")&&value["translate"]!="chat.type.text")return;
        std::smatch match;auto content=plain(value);if(std::regex_match(content,match,std::regex("<([^<>]{1,32})> (.{1,1024})")))public_message(match[1],match[2]);
    }
    std::string early_ack(){auto bytes=integer(static_cast<unsigned>(signatures.size()));for(const auto& [sender,signature]:signatures)bytes+=sender+integer(static_cast<unsigned>(signature.size()))+signature;return bytes+std::string(1,'\0');}
    void player_chat(Cursor& cursor){
        std::string sender,signature,value,network_name;unsigned filter=0,type=0;
        if(protocol==759){value=plain(component(cursor));if(cursor.number(1))component(cursor);type=cursor.integer();sender=std::string(cursor.take(16));network_name=plain(component(cursor));}
        else if(protocol==760){
            if(cursor.number(1))cursor.take(cursor.integer());sender=std::string(cursor.take(16));signature=std::string(cursor.take(cursor.integer()));value=cursor.text(256);if(cursor.number(1))component(cursor);cursor.take(16);auto count=cursor.integer();if(count>20)throw Failure(-32602,"Chat acknowledgement exceeds limit");for(unsigned i=0;i<count;++i){cursor.take(16);cursor.take(cursor.integer());}if(cursor.number(1))component(cursor);filter=cursor.integer();if(filter==2){count=cursor.integer();if(count>64)throw Failure(-32602,"Chat filter exceeds limit");cursor.take(count*8);}type=cursor.integer();network_name=plain(component(cursor));if(!signature.empty()){std::erase_if(signatures,[&](const auto& item){return item.first==sender;});signatures.emplace_back(sender,signature);while(signatures.size()>5)signatures.pop_front();send_packet("message_acknowledgement",early_ack());}
        }else{
            if(protocol>=770)cursor.integer();sender=std::string(cursor.take(16));cursor.integer();if(cursor.number(1))signature=std::string(cursor.take(256));value=cursor.text(256);cursor.take(16);auto count=cursor.integer();if(count>20)throw Failure(-32602,"Chat acknowledgement exceeds limit");for(unsigned i=0;i<count;++i)if(!cursor.integer())cursor.take(256);if(cursor.number(1))component(cursor);filter=cursor.integer();if(filter==2){count=cursor.integer();if(count>64)throw Failure(-32602,"Chat filter exceeds limit");cursor.take(count*8);}type=cursor.integer();network_name=(protocol<766||type)?plain(component(cursor)):"Player";
            if(!signature.empty()&&std::none_of(signatures.begin(),signatures.end(),[&](const auto& item){return item.second==signature;})){signatures.emplace_back(sender,signature);while(signatures.size()>20)signatures.pop_front();if(++signed_pending>64){send_packet("message_acknowledgement",integer(signed_pending));signed_pending=0;}}
        }
        if(sender!=uuid&&!filter&&type==static_cast<unsigned>(protocol>=766?1:0))public_message(network_name.empty()?"Player":network_name,value);
    }
    void encryption(const std::string& public_key,const std::string& token,bool old=false){
        if(public_key.size()>4096||token.size()>1024)throw Failure(-32602,"Invalid game encryption challenge");
        const auto* begin=reinterpret_cast<const unsigned char*>(public_key.data());auto key=std::unique_ptr<EVP_PKEY,decltype(&EVP_PKEY_free)>(d2i_PUBKEY(nullptr,&begin,static_cast<long>(public_key.size())),EVP_PKEY_free);if(!key||begin!=reinterpret_cast<const unsigned char*>(public_key.data()+public_key.size()))throw Failure(-32602,"Invalid game public key");
        std::string secret(16,'\0');if(RAND_bytes(reinterpret_cast<unsigned char*>(secret.data()),16)!=1)throw Failure(-32602,"Random unavailable");auto encrypted=rsa(key.get(),secret),verified=rsa(key.get(),token);
        if(old)write(std::string(1,'\xfc')+number(encrypted.size(),2)+encrypted+number(verified.size(),2)+verified);
        else{auto bytes=integer(static_cast<unsigned>(encrypted.size()))+encrypted;if(protocol>=759&&protocol<=760)bytes+='\1';send_packet("encryption_begin",bytes+integer(static_cast<unsigned>(verified.size()))+verified);}
        cipher.reset(EVP_CIPHER_CTX_new());decipher.reset(EVP_CIPHER_CTX_new());if(!cipher||!decipher||EVP_EncryptInit_ex(cipher.get(),EVP_aes_128_cfb8(),nullptr,reinterpret_cast<const unsigned char*>(secret.data()),reinterpret_cast<const unsigned char*>(secret.data()))!=1||EVP_DecryptInit_ex(decipher.get(),EVP_aes_128_cfb8(),nullptr,reinterpret_cast<const unsigned char*>(secret.data()),reinterpret_cast<const unsigned char*>(secret.data()))!=1)throw Failure(-32602,"Game cipher unavailable");
    }
    void modern(){
        std::string handshake=std::string(1,'\0')+integer(static_cast<unsigned>(protocol))+text("localhost")+number(static_cast<unsigned>(port),2)+std::string(1,'\2');write(integer(static_cast<unsigned>(handshake.size()))+handshake);
        auto login=text(name);if(protocol>=759&&protocol<=760)login+='\0';if(protocol>=760&&protocol<=763)login+='\1';if(protocol>=760)login+=uuid;send_packet("login_start",login);
        while(running){auto [kind,bytes]=packet();Cursor cursor{bytes};
            if(kind=="disconnect"||kind=="kick_disconnect")throw Failure(-32602,"Game rejected chat participant; check whitelist and chat policy");
            if(kind=="encryption_begin"){cursor.text(20);auto key=std::string(cursor.take(cursor.integer()));auto token=std::string(cursor.take(cursor.integer()));encryption(key,token);}
            else if(kind=="compress"){auto value=cursor.integer();if(value>maximum_packet)throw Failure(-32602,"Invalid compression threshold");compression=static_cast<int>(value);}
            else if(kind=="login_plugin_request")send_packet("login_plugin_response",integer(cursor.integer())+std::string(1,'\0'));
            else if(kind=="cookie_request")send_packet("cookie_response",text(cursor.text())+std::string(1,'\0'));
            else if(state=="login"&&kind=="success"){if(protocol>=764){send_packet("login_acknowledged");state="configuration";settings();}else state="play";}
            else if(kind=="select_known_packs")send_packet("select_known_packs",std::string(1,'\0'));
            else if(kind=="finish_configuration"){send_packet("finish_configuration");state="play";}
            else if(kind=="start_configuration"){ready=false;send_packet("configuration_acknowledged");state="configuration";}
            else if(kind=="keep_alive"){send_packet("keep_alive",bytes);if(has_position)send_position();}
            else if(kind=="ping")send_packet("pong",std::string(cursor.take(4)));
            else if(kind=="login"&&protocol>=766&&!bytes.empty()&&bytes.back())throw Failure(-32602,"World requires signed chat; lobby chat remains available");
            else if(kind=="position")position_packet(cursor);
            else if(kind=="chunk_batch_finished")send_packet("chunk_batch_received",real(1.0f));
            else if(kind=="player_chat")player_chat(cursor);
            else if(kind=="chat"||kind=="system_chat")old_chat(cursor,kind=="system_chat");
            send_messages();
        }
    }
    void old(){
        write(std::string("\x02\x4e",2)+legacy_text(name)+legacy_text("localhost")+number(static_cast<unsigned>(port),4));LegacyReader reader([this](size_t count){return read(count);});
        // Legacy encryption acknowledgement itself is plaintext.
        Cipher pending_cipher{nullptr,EVP_CIPHER_CTX_free},pending_decipher{nullptr,EVP_CIPHER_CTX_free};
        while(running){auto [id,value]=reader.packet(legacy());
            if(id==0xfd){encryption(value.at("publicKey").get<std::string>(),value.at("verifyToken").get<std::string>(),true);pending_cipher=std::move(cipher);pending_decipher=std::move(decipher);}
            else if(id==0xfc){if(!pending_cipher)throw Failure(-32602,"Unexpected legacy encryption acknowledgement");cipher=std::move(pending_cipher);decipher=std::move(pending_decipher);write(std::string("\xcd\0",2));}
            else if(id==0xff)throw Failure(-32602,"Game rejected legacy chat participant");
            else if(id==0){write(std::string(1,'\0')+number(static_cast<uint32_t>(value.at("keepAliveId").get<int>()),4));if(has_position)write(std::string(1,'\x0b')+real(position[0])+real(position[1])+real(position[1]+1.62)+real(position[2])+std::string(1,'\1'));}
            else if(id==0x0d){position={value.at("x").get<double>(),value.at("y").get<double>(),value.at("z").get<double>()};has_position=true;write(std::string(1,'\x0d')+real(position[0])+real(position[1])+real(value.at("stance").get<double>())+real(position[2])+real(value.at("yaw").get<float>())+real(value.at("pitch").get<float>())+std::string(1,'\1'));if(!ready){ready=true;notice("游戏聊天已同步");}}
            else if(id==3){auto raw=value.at("message").get<std::string>();auto parsed=Json::parse(raw,nullptr,false);if(!parsed.is_discarded())raw=plain(parsed);std::smatch match;if(std::regex_match(raw,match,std::regex("<([^<>]{1,32})> (.{1,1024})")))public_message(match[1],match[2]);}
            send_messages();
        }
    }
    void send_messages(){
        if(!ready||Clock::now()<next_message)return;
        std::pair<std::string,std::string> value;{std::lock_guard guard(mutex);if(messages.empty())return;value=std::move(messages.front());messages.pop_front();}
        auto prefix=std::string("[大厅] ")+value.first+": ";auto units=utf16(value.second),head=utf16(prefix);size_t maximum=protocol<315?100:256;if(head.size()+2>maximum)throw Failure(-32602,"Game chat name too long");auto budget=maximum-head.size();auto length=std::min(budget,units.size());if(length<units.size()&&units[length-1]>=0xd800&&units[length-1]<=0xdbff)--length;auto chunk=utf8(units.substr(0,length));
        if(length<units.size()){std::lock_guard guard(mutex);messages.emplace_front(value.first,utf8(units.substr(length)));}
        if(protocol==78)write(std::string(1,'\3')+legacy_text(prefix+chunk));
        else{auto payload=text(prefix+chunk);if(protocol>=759){auto now=std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::system_clock::now().time_since_epoch()).count();payload+=number(static_cast<uint64_t>(now),8)+number(0,8)+std::string(1,'\0');if(protocol<=760){payload+='\0';if(protocol==760)payload+=early_ack();}else{auto count=std::min<size_t>(signatures.size(),20);auto ack=((1u<<count)-1)<<(20-count);payload+=integer(signed_pending);for(unsigned i=0;i<3;++i)payload+=static_cast<char>((ack>>(i*8))&255);if(protocol>=770)payload+='\0';}}send_packet(protocol>=759?"chat_message":"chat",payload);signed_pending=0;}
        next_message=Clock::now()+std::chrono::milliseconds(600);
    }
    void run(){
        while(running){bool permanent=false;try{
            notice("正在连接游戏聊天");state="login";compression=-1;has_position=false;signed_pending=0;signatures.clear();received=Clock::now();cipher.reset();decipher.reset();socket=::socket(AF_INET,SOCK_STREAM,0);if(socket==invalid_socket)throw Failure(-32000,"Game socket unavailable");nonblocking(socket);tune_tcp(socket);sockaddr_in address{};address.sin_family=AF_INET;address.sin_addr.s_addr=htonl(INADDR_LOOPBACK);address.sin_port=htons(static_cast<uint16_t>(port));auto result=connect(socket,reinterpret_cast<sockaddr*>(&address),sizeof(address));if(result){bool pending=would_block();
#ifndef _WIN32
            pending=pending||errno==EINPROGRESS;
#endif
            if(!pending)throw Failure(-32000,"Game chat unavailable");auto deadline=Clock::now()+std::chrono::seconds(5);while(running&&Clock::now()<deadline){PollFd descriptor{socket,POLLOUT,0};if(net_poll(&descriptor,1,50)>0){int error=0;SockLen size=sizeof(error);if(getsockopt(socket,SOL_SOCKET,SO_ERROR,reinterpret_cast<char*>(&error),&size)||error)throw Failure(-32000,"Game chat unavailable");result=0;break;}}if(result)throw Failure(-32000,"Game chat connection timed out");}
            if(protocol==78)old();else modern();
        }catch(const Failure& error){permanent=error.code==-32602;if(running)notice(permanent?"游戏聊天协议或权限不兼容，大厅聊天仍可使用":"游戏聊天已断开，大厅聊天仍可使用");}
        catch(...){permanent=true;if(running)notice("游戏聊天协议无法识别，大厅聊天仍可使用");}
        ready=false;close_socket(socket);socket=invalid_socket;{std::lock_guard guard(mutex);messages.clear();}if(permanent)break;std::unique_lock guard(mutex);changed.wait_for(guard,std::chrono::seconds(10),[this]{return !running.load();});
        }
    }
};
GameChat::GameChat(int port,int protocol,std::string name,Callback callback):impl_(std::make_unique<Impl>(port,protocol,std::move(name),std::move(callback))){}
GameChat::~GameChat()=default;
void GameChat::send(const std::string& sender,const std::string& text){impl_->enqueue(sender,text);}
bool GameChat::ready() const{return impl_->ready.load();}
}
