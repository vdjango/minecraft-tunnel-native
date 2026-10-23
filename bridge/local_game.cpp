// SPDX-License-Identifier: AGPL-3.0-or-later
#include "local_game.hpp"
#include "game_wire.hpp"
#include "protocol_data.hpp"
#include <filesystem>
#include <fstream>
#include <future>
#include <sstream>
#ifdef _WIN32
#include <windows.h>
#include <iphlpapi.h>
#elif defined(__APPLE__)
#include <libproc.h>
#endif
namespace bridge {
namespace {
struct Socket {NetSocket fd=invalid_socket;~Socket(){close_socket(fd);}};
void wait_io(NetSocket fd,short events,Clock::time_point deadline){
    auto remaining=std::chrono::duration_cast<std::chrono::milliseconds>(deadline-Clock::now()).count();
    if(remaining<=0)throw Failure(-32000,"Game probe timed out");
    PollFd descriptor{fd,events,0};if(net_poll(&descriptor,1,static_cast<int>(remaining))<=0)throw Failure(-32000,"Game probe timed out");
}
void write_all(NetSocket fd,std::string_view bytes,Clock::time_point deadline){
    while(!bytes.empty()){auto n=net_send(fd,bytes.data(),static_cast<int>(bytes.size()));if(n>0)bytes.remove_prefix(static_cast<size_t>(n));else if(would_block())wait_io(fd,POLLOUT,deadline);else throw Failure(-32000,"Game probe write failed");}
}
std::string read_exact(NetSocket fd,size_t count,Clock::time_point deadline){
    std::string bytes(count,'\0');size_t offset=0;
    while(offset<count){auto n=recv(fd,bytes.data()+offset,static_cast<int>(count-offset),0);if(n>0)offset+=static_cast<size_t>(n);else if(n<0&&would_block())wait_io(fd,POLLIN,deadline);else throw Failure(-32000,"Game probe disconnected");}return bytes;
}
uint32_t read_integer(NetSocket fd,Clock::time_point deadline){uint32_t value=0;for(unsigned i=0;i<5;++i){auto b=static_cast<unsigned char>(read_exact(fd,1,deadline)[0]);if(i==4&&(b&0xf0))throw Failure(-32602,"Invalid game integer");value|=uint32_t(b&127)<<(7*i);if(!(b&128))return value;}throw Failure(-32602,"Invalid game integer");}
std::string integer(uint32_t value){std::string result;do{auto b=value&127;value>>=7;result+=static_cast<char>(b|(value?128:0));}while(value);return result;}
bool java_process(unsigned long pid){
#ifdef _WIN32
    auto process=OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION,FALSE,static_cast<DWORD>(pid));if(!process)return false;
    wchar_t path[32768];DWORD count=32768;bool valid=QueryFullProcessImageNameW(process,0,path,&count)!=0;CloseHandle(process);
    if(!valid)return false;auto name=std::filesystem::path(std::wstring(path,count)).filename().wstring();return _wcsicmp(name.c_str(),L"java.exe")==0||_wcsicmp(name.c_str(),L"javaw.exe")==0;
#elif defined(__APPLE__)
    char path[PROC_PIDPATHINFO_MAXSIZE];if(proc_pidpath(static_cast<int>(pid),path,sizeof(path))<=0)return false;return std::filesystem::path(path).filename()=="java";
#else
    std::ifstream file("/proc/"+std::to_string(pid)+"/comm");std::string name;std::getline(file,name);return name=="java"||name=="javaw";
#endif
}
std::set<int> java_ports(){std::set<int> result;
#ifdef _WIN32
    DWORD size=0;GetExtendedTcpTable(nullptr,&size,FALSE,AF_INET,TCP_TABLE_OWNER_PID_LISTENER,0);std::vector<unsigned char> buffer(size);
    if(size&&GetExtendedTcpTable(buffer.data(),&size,FALSE,AF_INET,TCP_TABLE_OWNER_PID_LISTENER,0)==NO_ERROR){auto table=reinterpret_cast<MIB_TCPTABLE_OWNER_PID*>(buffer.data());for(DWORD i=0;i<table->dwNumEntries;++i)if(java_process(table->table[i].dwOwningPid))result.insert(ntohs(static_cast<uint16_t>(table->table[i].dwLocalPort)));}
#elif defined(__APPLE__)
    std::vector<int> pids(static_cast<size_t>(proc_listallpids(nullptr,0))+32);auto count=proc_listallpids(pids.data(),static_cast<int>(pids.size()*sizeof(int)));
    for(int i=0;i<count;++i)if(java_process(static_cast<unsigned long>(pids[i]))){int bytes=proc_pidinfo(pids[i],PROC_PIDLISTFDS,0,nullptr,0);if(bytes<=0)continue;std::vector<proc_fdinfo> fds(static_cast<size_t>(bytes)/sizeof(proc_fdinfo));bytes=proc_pidinfo(pids[i],PROC_PIDLISTFDS,0,fds.data(),bytes);for(int j=0;j<bytes/static_cast<int>(sizeof(proc_fdinfo));++j)if(fds[j].proc_fdtype==PROX_FDTYPE_SOCKET){socket_fdinfo info{};if(proc_pidfdinfo(pids[i],fds[j].proc_fd,PROC_PIDFDSOCKETINFO,&info,sizeof(info))==sizeof(info)&&info.psi.soi_kind==SOCKINFO_TCP&&info.psi.soi_proto.pri_tcp.tcpsi_state==1)result.insert(ntohs(static_cast<uint16_t>(info.psi.soi_proto.pri_tcp.tcpsi_ini.insi_lport)));}}
#else
    std::set<std::string> inodes;std::error_code ec;
    for(const auto& entry:std::filesystem::directory_iterator("/proc",ec)){auto name=entry.path().filename().string();if(name.find_first_not_of("0123456789")!=std::string::npos||name.empty())continue;if(!java_process(std::stoul(name)))continue;for(const auto& fd:std::filesystem::directory_iterator(entry.path()/"fd",ec)){auto link=std::filesystem::read_symlink(fd.path(),ec).string();if(link.starts_with("socket:["))inodes.insert(link.substr(8,link.size()-9));}}
    for(const char* path:{"/proc/net/tcp","/proc/net/tcp6"}){std::ifstream input(path);std::string line;std::getline(input,line);while(std::getline(input,line)){std::istringstream row(line);std::vector<std::string> fields;std::string field;while(row>>field)fields.push_back(field);if(fields.size()>9&&fields[3]=="0A"&&inodes.contains(fields[9]))result.insert(std::stoi(fields[1].substr(fields[1].find(':')+1),nullptr,16));}}
#endif
    return result;
}
}
bool game_reachable(int port){
    if(port<1||port>65535)return false;
    Socket probe;probe.fd=::socket(AF_INET,SOCK_STREAM,0);if(probe.fd==invalid_socket)return false;
    nonblocking(probe.fd);sockaddr_in address{};address.sin_family=AF_INET;address.sin_addr.s_addr=htonl(INADDR_LOOPBACK);address.sin_port=htons(static_cast<uint16_t>(port));
    if(!connect(probe.fd,reinterpret_cast<sockaddr*>(&address),sizeof(address)))return true;
    bool pending=would_block();
#ifndef _WIN32
    pending=pending||errno==EINPROGRESS;
#endif
    if(!pending)return false;
    PollFd descriptor{probe.fd,POLLOUT,0};if(net_poll(&descriptor,1,80)<=0)return false;
    int error=0;SockLen size=sizeof(error);return !getsockopt(probe.fd,SOL_SOCKET,SO_ERROR,reinterpret_cast<char*>(&error),&size)&&error==0;
}
static Json modern_status(int port,int timeout_ms){
    if(port<1||port>65535||timeout_ms<100||timeout_ms>10000)throw Failure(-32602,"Invalid game probe");
    Socket socket;socket.fd=::socket(AF_INET,SOCK_STREAM,0);if(socket.fd==invalid_socket)throw Failure(-32000,"Game probe unavailable");nonblocking(socket.fd);
    auto deadline=Clock::now()+std::chrono::milliseconds(timeout_ms);sockaddr_in address{};address.sin_family=AF_INET;address.sin_addr.s_addr=htonl(INADDR_LOOPBACK);address.sin_port=htons(static_cast<uint16_t>(port));
    if(connect(socket.fd,reinterpret_cast<sockaddr*>(&address),sizeof(address))){wait_io(socket.fd,POLLOUT,deadline);int error=0;SockLen length=sizeof(error);if(getsockopt(socket.fd,SOL_SOCKET,SO_ERROR,reinterpret_cast<char*>(&error),&length)||error)throw Failure(-32000,"Game world unavailable");}
    std::string handshake(1,'\0');handshake+=integer(0xffffffff);handshake+='\x09';handshake+="localhost";handshake+=static_cast<char>(port>>8);handshake+=static_cast<char>(port&255);handshake+='\x01';
    write_all(socket.fd,integer(static_cast<uint32_t>(handshake.size()))+handshake+std::string("\x01\x00",2),deadline);
    auto size=read_integer(socket.fd,deadline);if(size<2||size>1048576)throw Failure(-32000,"Invalid game status size");auto bytes=read_exact(socket.fd,size,deadline);if(bytes[0]!=0)throw Failure(-32000,"Invalid game status packet");size_t offset=1;uint32_t length=0;bool complete=false;for(unsigned i=0;i<5&&offset<bytes.size();++i){auto b=static_cast<unsigned char>(bytes[offset++]);length|=uint32_t(b&127)<<(7*i);if(!(b&128)){complete=true;break;}}
    if(!complete||offset+length!=bytes.size())throw Failure(-32000,"Invalid game status string");auto status=Json::parse(bytes.substr(offset));auto version=status.value("version",Json::object());if(!version.is_object())throw Failure(-32000,"Invalid game version");
    Json latency=nullptr;try{auto nonce=Clock::now().time_since_epoch().count();std::string ping("\x09\x01",2);ping.append(reinterpret_cast<char*>(&nonce),8);auto started=Clock::now();auto pong_deadline=std::min(deadline,started+std::chrono::milliseconds(500));write_all(socket.fd,ping,pong_deadline);if(read_integer(socket.fd,pong_deadline)==9&&read_exact(socket.fd,9,pong_deadline)==ping.substr(1))latency=std::chrono::duration_cast<std::chrono::milliseconds>(Clock::now()-started).count();}catch(...){}
    return {{"port",port},{"version",version.value("name","").substr(0,64)},{"protocol",version.value("protocol",Json(nullptr))},{"players",status.value("players",Json::object())},{"latency_ms",latency}};
}
Json game_status(int port,int timeout_ms){
    if(port<1||port>65535||timeout_ms<100||timeout_ms>10000)throw Failure(-32602,"Invalid game probe");
    auto started=Clock::now();
    try{return modern_status(port,std::max(100,timeout_ms/2));}catch(...){}
    Socket socket;socket.fd=::socket(AF_INET,SOCK_STREAM,0);if(socket.fd==invalid_socket)throw Failure(-32000,"Game probe unavailable");nonblocking(socket.fd);
    auto deadline=started+std::chrono::milliseconds(timeout_ms);sockaddr_in address{};address.sin_family=AF_INET;address.sin_addr.s_addr=htonl(INADDR_LOOPBACK);address.sin_port=htons(static_cast<uint16_t>(port));
    if(connect(socket.fd,reinterpret_cast<sockaddr*>(&address),sizeof(address))){wait_io(socket.fd,POLLOUT,deadline);int error=0;SockLen length=sizeof(error);if(getsockopt(socket.fd,SOL_SOCKET,SO_ERROR,reinterpret_cast<char*>(&error),&length)||error)throw Failure(-32000,"Game world unavailable");}
    auto payload=std::string(1,'\x4e')+game::legacy_text("localhost")+game::number(static_cast<unsigned>(port),4);
    write_all(socket.fd,std::string("\xfe\x01\xfa",3)+game::legacy_text("MC|PingHost")+game::number(payload.size(),2)+payload,deadline);
    if(read_exact(socket.fd,1,deadline)!=std::string(1,'\xff'))throw Failure(-32000,"Invalid legacy game status");
    auto length=read_exact(socket.fd,2,deadline);auto count=game::Cursor{length}.number(2);if(count>32767)throw Failure(-32000,"Legacy status exceeds limit");
    auto bytes=read_exact(socket.fd,static_cast<size_t>(count)*2,deadline);game::Cursor cursor{bytes};std::u16string decoded;while(cursor.offset<bytes.size())decoded+=static_cast<char16_t>(cursor.number(2));
    auto value=game::utf8(decoded);std::vector<std::string> fields;size_t offset=0;while(true){auto end=value.find('\0',offset);fields.push_back(value.substr(offset,end-offset));if(end==std::string::npos)break;offset=end+1;}
    if(fields.size()!=6||fields[0]!="§1")throw Failure(-32000,"Invalid legacy game status");
    auto protocol=std::stoi(fields[1]);if(protocol==127){static auto profiles=Json::parse(game::profiles_json);for(auto it=profiles.begin();it!=profiles.end();++it)for(const auto& release:it.value().at("releases"))if(release==fields[2])protocol=std::stoi(it.key());}
    return {{"port",port},{"version",fields[2].substr(0,64)},{"protocol",protocol},{"players",{{"online",std::stoi(fields[4])},{"max",std::stoi(fields[5])}}},{"latency_ms",nullptr}};
}
Json discover_worlds(const std::set<int>& excluded,int duration_ms){
    if(duration_ms<0||duration_ms>5000)throw Failure(-32602,"Invalid discovery duration");auto ports=java_ports();Socket socket;socket.fd=::socket(AF_INET,SOCK_DGRAM,0);
    if(socket.fd!=invalid_socket){int yes=1;setsockopt(socket.fd,SOL_SOCKET,SO_REUSEADDR,reinterpret_cast<char*>(&yes),sizeof(yes));sockaddr_in address{};address.sin_family=AF_INET;address.sin_port=htons(4445);address.sin_addr.s_addr=htonl(INADDR_ANY);
        if(!bind(socket.fd,reinterpret_cast<sockaddr*>(&address),sizeof(address))){ip_mreq group{};inet_pton(AF_INET,"224.0.2.60",&group.imr_multiaddr);group.imr_interface.s_addr=htonl(INADDR_ANY);setsockopt(socket.fd,IPPROTO_IP,IP_ADD_MEMBERSHIP,reinterpret_cast<char*>(&group),sizeof(group));nonblocking(socket.fd);auto deadline=Clock::now()+std::chrono::milliseconds(duration_ms);while(Clock::now()<deadline&&ports.size()<32){PollFd poll{socket.fd,POLLIN,0};if(net_poll(&poll,1,50)<=0)continue;char buffer[2048];auto count=recv(socket.fd,buffer,sizeof(buffer),0);if(count<=0)continue;std::string packet(buffer,static_cast<size_t>(count));auto start=packet.find("[AD]"),end=packet.find("[/AD]");if(start==std::string::npos||end<=start+4)continue;auto value=packet.substr(start+4,end-start-4);if(value.size()<=5&&value.find_first_not_of("0123456789")==std::string::npos){int port=std::stoi(value);if(port>0&&port<=65535)ports.insert(port);}}}}
    std::vector<std::future<Json>> probes;for(int port:ports)if(!excluded.contains(port)&&probes.size()<32)probes.push_back(std::async(std::launch::async,[port]{return game_status(port);}));auto worlds=Json::array();for(auto& probe:probes)try{worlds.push_back(probe.get());}catch(...){}log("lan_discovery_complete",{{"worlds",worlds.size()}});return {{"worlds",worlds}};
}
void announce_world(int port,const std::string& title){Socket socket;socket.fd=::socket(AF_INET,SOCK_DGRAM,0);if(socket.fd==invalid_socket)return;nonblocking(socket.fd);sockaddr_in local{};local.sin_family=AF_INET;local.sin_addr.s_addr=htonl(INADDR_LOOPBACK);if(bind(socket.fd,reinterpret_cast<sockaddr*>(&local),sizeof(local)))return;local.sin_port=htons(4445);std::string clean;for(unsigned char c:title)if(c>=32&&c!=127&&c!='['&&c!=']'&&clean.size()<240)clean+=static_cast<char>(c);std::string packet="[MOTD]"+clean+"[/MOTD][AD]"+std::to_string(port)+"[/AD]";sendto(socket.fd,packet.data(),static_cast<int>(packet.size()),0,reinterpret_cast<sockaddr*>(&local),sizeof(local));}
}
