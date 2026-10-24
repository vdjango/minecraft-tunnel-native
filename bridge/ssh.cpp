// SPDX-License-Identifier: AGPL-3.0-or-later
#include "ssh.hpp"
#include "profile_exchange.hpp"
#include <libssh/server.h>
#include <openssl/evp.h>
#include <openssl/rand.h>
#include <openssl/crypto.h>
#include <iostream>
#include <mutex>
#include <regex>
namespace bridge {
namespace {
std::string base64_decode(std::string value) {
    if(value.empty() || value.size()>8192 || value.size()%4==1) throw Failure(-32602,"Invalid access code");
    for(char& c:value) { if(c=='-')c='+';else if(c=='_')c='/';else if(!std::isalnum(static_cast<unsigned char>(c))&&c!='='&&c!='+'&&c!='/')throw Failure(-32602,"Invalid access code"); }
    while(value.size()%4)value+='=';
    std::string result(value.size()/4*3,'\0');
    int count=EVP_DecodeBlock(reinterpret_cast<unsigned char*>(result.data()),reinterpret_cast<const unsigned char*>(value.data()),static_cast<int>(value.size()));
    if(count<0)throw Failure(-32602,"Invalid access code");
    if(value.back()=='=')--count;if(value.size()>1&&value[value.size()-2]=='=')--count;
    result.resize(static_cast<size_t>(count));return result;
}
}
std::string random_id(size_t count) {
    std::string bytes(count,'\0');if(RAND_bytes(reinterpret_cast<unsigned char*>(bytes.data()),static_cast<int>(count))!=1)throw std::runtime_error("Secure random unavailable");
    std::string encoded(4*((count+2)/3),'\0');
    EVP_EncodeBlock(reinterpret_cast<unsigned char*>(encoded.data()),reinterpret_cast<const unsigned char*>(bytes.data()),static_cast<int>(count));
    for(char& c:encoded){if(c=='+')c='-';if(c=='/')c='_';}while(!encoded.empty()&&encoded.back()=='=')encoded.pop_back();return encoded;
}
std::string room_id(const std::string& secret) {
    unsigned char digest[32];unsigned int count=0;
    if(EVP_Digest(secret.data(),secret.size(),digest,&count,EVP_sha256(),nullptr)!=1)throw std::runtime_error("SHA256 unavailable");
    constexpr char hex[]="0123456789abcdef";std::string result;result.reserve(32);
    for(size_t i=0;i<16;++i){result+=hex[digest[i]>>4];result+=hex[digest[i]&15];}return result;
}
std::string invitation(const std::string& value) {
    if(!value.starts_with("bugg1-"))throw Failure(-32602,"Invalid room invitation");
    auto secret=value.substr(6);
    if(secret.size()<32||secret.size()>128||secret.find_first_not_of("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_")!=std::string::npos)throw Failure(-32602,"Invalid room invitation");return secret;
}
Profile Profile::from_json(const Json& value) {
    if(!value.is_object()||!value.contains("server_host")||!value.contains("server_port")||!value["server_port"].is_number_integer())throw Failure(-32602,"Invalid node profile");
    Profile p;p.host=value.at("server_host").get<std::string>();p.port=value.at("server_port").get<int>();p.token=value.at("token").get<std::string>();p.key=value.at("server_public_key").get<std::string>();
    if(value.contains("name")&&value["name"].is_string()&&value["name"].get_ref<const std::string&>().size()<=80)p.name=value["name"].get<std::string>();
    if(p.host.empty()||p.host.size()>253||p.host.find_first_of(" /\\@?#\t\r\n")!=std::string::npos||p.host.find('\0')!=std::string::npos||p.port<1||p.port>65535||p.token.size()<32||p.token.size()>2048||p.key.size()>4096)throw Failure(-32602,"Invalid node profile");
    const auto split=p.key.find(' ');ssh_key key=nullptr;
    if(split==std::string::npos)throw Failure(-32602,"Invalid pinned key");
    const auto type=ssh_key_type_from_name(p.key.substr(0,split).c_str());
    auto material=p.key.substr(split+1);material=material.substr(0,material.find_first_of(" \r\n"));
    if(ssh_pki_import_pubkey_base64(material.c_str(),type,&key)!=SSH_OK)throw Failure(-32602,"Invalid pinned key");ssh_key_free(key);return p;
}
Profile Profile::decode(const std::string& code) {
    if(code.starts_with("buggnode2-"))return from_json(redeem_profile_code(code));
    if(code.size()>4096||!code.starts_with("buggnode1-"))throw Failure(-32602,"Invalid access code");
    return from_json(Json::parse(base64_decode(code.substr(10))));
}
Ssh::Ssh(const Profile& p,const std::string& user,const std::string& secret,int timeout_seconds)
    :heartbeat_(static_cast<uint64_t>(Clock::now().time_since_epoch().count())) {
    session_=ssh_new();if(!session_)throw std::bad_alloc();
    ssh_set_counters(session_,nullptr,&traffic_);
    try {
        long timeout=timeout_seconds;int no=0;
        ssh_options_set(session_,SSH_OPTIONS_PROCESS_CONFIG,&no);
        ssh_options_set(session_,SSH_OPTIONS_HOST,p.host.c_str());ssh_options_set(session_,SSH_OPTIONS_PORT,&p.port);
        ssh_options_set(session_,SSH_OPTIONS_USER,user.c_str());ssh_options_set(session_,SSH_OPTIONS_TIMEOUT,&timeout);
        ssh_options_set(session_,SSH_OPTIONS_COMPRESSION,"none");
        if(ssh_connect(session_)!=SSH_OK)throw Failure(-32000,"Node connection failed");
        ssh_key received=nullptr,pinned=nullptr;
        auto split=p.key.find(' ');auto material=p.key.substr(split+1);material=material.substr(0,material.find_first_of(" \r\n"));
        const int imported=ssh_pki_import_pubkey_base64(material.c_str(),ssh_key_type_from_name(p.key.substr(0,split).c_str()),&pinned);
        const int read=ssh_get_server_publickey(session_,&received);
        const bool equal=imported==SSH_OK&&read==SSH_OK&&ssh_key_cmp(received,pinned,SSH_KEY_CMP_PUBLIC)==0;
        ssh_key_free(received);ssh_key_free(pinned);
        if(!equal){log("node_identity_rejected");throw Failure(-32030,"Node identity verification failed");}
        Json credentials{{"token",p.token.substr(0,p.token.find('~'))}};if(!secret.empty())credentials["secret"]=secret;
        auto password=credentials.dump();const int auth=ssh_userauth_password(session_,nullptr,password.c_str());
        OPENSSL_cleanse(password.data(),password.size());
        if(auth!=SSH_AUTH_SUCCESS)throw Failure(-32031,"Node authentication failed");
        tune_tcp(fd());ssh_set_blocking(session_,0);
    }catch(...){ssh_disconnect(session_);ssh_free(session_);session_=nullptr;throw;}
}
Ssh::~Ssh(){if(session_){ssh_disconnect(session_);ssh_free(session_);}}
bool Ssh::heartbeat() {
    const auto action=heartbeat_.poll(traffic_.in_packets);
    if(action==Heartbeat::Action::expired)return false;
    if(action==Heartbeat::Action::probe)ssh_send_keepalive(session_);
    return true;
}
int Ssh::retry(const std::function<int()>& operation,int seconds) {
    const auto deadline=Clock::now()+std::chrono::seconds(seconds);int result;
    while((result=operation())==SSH_AGAIN) {
        if(Clock::now()>deadline||!alive())throw Failure(-32000,"Node operation timed out");
        PollFd descriptor{fd(),static_cast<short>(POLLIN|((ssh_get_poll_flags(session_)&SSH_WRITE_PENDING)?POLLOUT:0)),0};net_poll(&descriptor,1,20);
    }return result;
}
ssh_channel Ssh::subsystem(const char* name) {
    ssh_channel channel=ssh_channel_new(session_);if(!channel)throw std::bad_alloc();
    try {if(retry([&]{return ssh_channel_open_session(channel);})!=SSH_OK||retry([&]{return ssh_channel_request_subsystem(channel,name);})!=SSH_OK)throw Failure(-32000,"Node subsystem unavailable");return channel;}catch(...){free_channel(channel);throw;}
}
Json Ssh::request(Json value,int timeout_seconds) {
    auto channel=subsystem("buggcraft-rooms-v1");
    try {
        auto data=value.dump()+"\n";size_t offset=0;
        while(offset<data.size()){const int n=retry([&]{return ssh_channel_write(channel,data.data()+offset,static_cast<uint32_t>(data.size()-offset));});if(n<=0)throw Failure(-32000,"Node write failed");offset+=static_cast<size_t>(n);}
        std::string line;char buffer[4096];const auto deadline=Clock::now()+std::chrono::seconds(timeout_seconds);
        while(line.find('\n')==std::string::npos){int n=ssh_channel_read_nonblocking(channel,buffer,sizeof(buffer),0);if(n>0)line.append(buffer,static_cast<size_t>(n));else if(n==SSH_ERROR||ssh_channel_is_eof(channel)||Clock::now()>deadline)throw Failure(-32000,"Node response failed");else{PollFd descriptor{fd(),POLLIN,0};net_poll(&descriptor,1,10);}if(line.size()>65536)throw Failure(-32000,"Node response exceeds limit");}
        auto result=Json::parse(line.substr(0,line.find('\n')));free_channel(channel);
        if(!result.is_object()||!result.value("ok",false)||!result.contains("result"))throw Failure(-32000,"Room operation rejected");return result["result"];
    }catch(...){free_channel(channel);throw;}
}
int Ssh::forward(const char* name){int port=0;if(retry([&]{return ssh_channel_listen_forward(session_,name,0,&port);})!=SSH_OK)throw Failure(-32000,"Host forwarding unavailable");return port;}
ssh_channel Ssh::open(const std::string& room,int port){auto channel=ssh_channel_new(session_);if(!channel)throw std::bad_alloc();if(retry([&]{return ssh_channel_open_forward(channel,room.c_str(),port,"127.0.0.1",0);})!=SSH_OK){free_channel(channel);return nullptr;}return channel;}
void free_channel(ssh_channel& channel){if(channel){ssh_channel_send_eof(channel);ssh_channel_close(channel);ssh_channel_free(channel);channel=nullptr;}}
}
