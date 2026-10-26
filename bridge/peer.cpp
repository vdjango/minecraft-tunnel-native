// SPDX-License-Identifier: AGPL-3.0-or-later
#include "session.hpp"
#include <algorithm>
#include <array>
#include <openssl/rand.h>
#include <sstream>
namespace bridge {
namespace {
bool stun_responds(const Profile& profile){
    addrinfo hints{};hints.ai_family=AF_UNSPEC;hints.ai_socktype=SOCK_DGRAM;hints.ai_protocol=IPPROTO_UDP;
    auto service=std::to_string(profile.port);addrinfo* results=nullptr;
    if(getaddrinfo(profile.host.c_str(),service.c_str(),&hints,&results)!=0)return false;
    struct Addresses{addrinfo* value;~Addresses(){if(value)freeaddrinfo(value);}} addresses{results};
    std::array<unsigned char,20> request{0,1,0,0,0x21,0x12,0xa4,0x42};
    if(RAND_bytes(request.data()+8,12)!=1)return false;
    std::vector<NetSocket> sockets;std::vector<PollFd> descriptors;
    struct Sockets{std::vector<NetSocket>& values;~Sockets(){for(auto fd:values)close_socket(fd);}} cleanup{sockets};
    for(auto* address=results;address;address=address->ai_next){
        // A loopback STUN server advertises unusable loopback srflx candidates.
        if(address->ai_family==AF_INET){auto ip=ntohl(reinterpret_cast<sockaddr_in*>(address->ai_addr)->sin_addr.s_addr);if(!ip||(ip>>24)==127)continue;}
        else if(address->ai_family==AF_INET6){const auto& ip=reinterpret_cast<sockaddr_in6*>(address->ai_addr)->sin6_addr;if(IN6_IS_ADDR_LOOPBACK(&ip)||IN6_IS_ADDR_UNSPECIFIED(&ip))continue;}
        auto fd=socket(address->ai_family,address->ai_socktype,address->ai_protocol);if(fd==invalid_socket)continue;
        if(connect(fd,address->ai_addr,static_cast<SockLen>(address->ai_addrlen))!=0||
           ::send(fd,reinterpret_cast<const char*>(request.data()),static_cast<int>(request.size()),0)!=static_cast<int>(request.size())){close_socket(fd);continue;}
        sockets.push_back(fd);descriptors.push_back({fd,POLLIN,0});
    }
    if(descriptors.empty())return false;
    // Retry once so one lost datagram does not disable STUN for the session.
    auto received=net_poll(descriptors.data(),descriptors.size(),250);
    if(received==0){
        for(auto fd:sockets)::send(fd,reinterpret_cast<const char*>(request.data()),static_cast<int>(request.size()),0);
        received=net_poll(descriptors.data(),descriptors.size(),250);
    }
    if(received<=0)return false;
    for(size_t i=0;i<descriptors.size();++i)if(descriptors[i].revents&POLLIN){
        std::array<unsigned char,2048> response{};auto count=::recv(sockets[i],reinterpret_cast<char*>(response.data()),static_cast<int>(response.size()),0);
        if(count<20||response[0]!=1||response[1]!=1||response[4]!=0x21||response[5]!=0x12||response[6]!=0xa4||response[7]!=0x42||
           !std::equal(request.begin()+8,request.end(),response.begin()+8))continue;
        const auto body=static_cast<size_t>((response[2]<<8)|response[3]);if(body%4==0&&body+20<=static_cast<size_t>(count))return true;
    }
    return false;
}
}
Json candidate_counts(const std::string& sdp){
    std::array<size_t,4> counts{};
    std::istringstream input(sdp);std::string line;
    while(std::getline(input,line))if(line.starts_with("a=candidate:")){
        auto type=line.rfind(" typ ");if(type==std::string::npos)continue;
        auto value=line.substr(type+5);auto end=value.find_first_of(" \t\r");if(end!=std::string::npos)value.resize(end);
        if(value=="host")++counts[0];else if(value=="srflx")++counts[1];else if(value=="prflx")++counts[2];else if(value=="relay")++counts[3];
    }
    return {{"total",counts[0]+counts[1]+counts[2]+counts[3]},{"host",counts[0]},{"srflx",counts[1]},{"prflx",counts[2]},{"relay",counts[3]}};
}
void validate_sdp(const std::string& sdp){
    if(sdp.empty()||sdp.size()>12288)throw std::runtime_error("Invalid peer description size");
    std::istringstream input(sdp);std::string line;unsigned count=0;
    while(std::getline(input,line)){
        if(line.starts_with("m=")&&!line.starts_with("m=application "))throw std::runtime_error("Unsupported peer media");
        if(line.starts_with("a=candidate:")){
            std::istringstream candidate(line);std::string foundation,component,protocol,priority,address,type,kind;int port=0;
            if(!(candidate>>foundation>>component>>protocol>>priority>>address>>port>>type>>kind)||protocol!="UDP"&&protocol!="udp"||port<1||port>65535||++count>32)throw std::runtime_error("Invalid ICE candidate");
            in_addr v4{};in6_addr v6{};bool valid=false;
            if(inet_pton(AF_INET,address.c_str(),&v4)==1){auto ip=ntohl(v4.s_addr);valid=ip!=0&&(ip>>24)!=127&&(ip>>28)!=14&&(ip&0xffff0000)!=0xa9fe0000;}
            else if(inet_pton(AF_INET6,address.c_str(),&v6)==1){valid=!IN6_IS_ADDR_UNSPECIFIED(&v6)&&!IN6_IS_ADDR_LOOPBACK(&v6)&&!IN6_IS_ADDR_MULTICAST(&v6)&&!IN6_IS_ADDR_LINKLOCAL(&v6);}
            if(!valid)throw std::runtime_error("Unusable ICE candidate");
        }
    }
}
void Session::signal(Json value){auto data=value.dump()+"\n";if(signal_output_.size()+data.size()>131072)throw std::runtime_error("Peer signaling output limit");signal_output_+=data;}
std::shared_ptr<rtc::PeerConnection> Session::create_peer(const std::string& key,bool client,std::string player,std::string attempt,bool files){
    rtc::Configuration config;config.disableAutoNegotiation=true;config.maxMessageSize=16385;
    for(const auto& server:stun_profiles_)config.iceServers.emplace_back(server.host,static_cast<uint16_t>(server.port));
    auto pc=std::make_shared<rtc::PeerConnection>(config);std::weak_ptr<Session> weak=shared_from_this();std::weak_ptr<rtc::PeerConnection> peer=pc;
    pc->onGatheringStateChange([weak,peer,key,client,attempt,files](rtc::PeerConnection::GatheringState state){
        if(state!=rtc::PeerConnection::GatheringState::Complete)return;
        if(auto owner=weak.lock())owner->post([owner,peer,key,client,attempt,files]{if(auto p=peer.lock()){
            if(client&&(files?owner->file_peer_:owner->client_peer_)!=p)return;
            try{auto local=p->localDescription();if(!local)return;std::string sdp=*local;validate_sdp(sdp);Json counts=candidate_counts(sdp);Json value{{"type",client?"offer":"answer"},{"sdp",sdp},{"files",files}};if(!attempt.empty())value["attempt"]=attempt;if(!client)value["peer"]=key;owner->signal(value);log(files?"file_peer_candidates_ready":"peer_candidates_ready",{{"session",owner->identity},{"role",owner->role},{"attempt",attempt},{"candidates",counts}});}
            catch(const std::exception&){p->close();log("peer_candidates_rejected",{{"session",owner->identity},{"role",owner->role},{"attempt",attempt}});}
        }});
    });
    pc->onStateChange([weak,peer,client,player,files](rtc::PeerConnection::State state){if(auto owner=weak.lock())owner->post([owner,peer,client,player,state,files]{
        auto p=peer.lock();if(!p||(client&&(files?owner->file_peer_:owner->client_peer_)!=p))return;
        if(files){
            log("file_peer_state",{{"session",owner->identity},{"state",static_cast<int>(state)}});
            if(state==rtc::PeerConnection::State::Connected){rtc::Candidate local,remote;if(p->getSelectedCandidatePair(&local,&remote))log("file_peer_selected_pair",{{"session",owner->identity},{"local",std::string(local)},{"remote",std::string(remote)}});}
            return;
        }
        log("peer_state",{{"session",owner->identity},{"player",player},{"state",static_cast<int>(state)}});
        if(client){owner->direct_=state==rtc::PeerConnection::State::Connected;if(owner->direct_){const bool recovered=owner->peer_retries_>0;owner->peer_negotiating_=false;owner->peer_retry_={};owner->peer_retries_=0;owner->become_ready();if(recovered){log("peer_recovered",{{"session",owner->identity},{"player",player}});owner->event({{"type","bridge"},{"status","直连已恢复，后续连接优先使用直连"}});}}}
        if(state==rtc::PeerConnection::State::Failed||state==rtc::PeerConnection::State::Closed)owner->fail_peer(p,state==rtc::PeerConnection::State::Failed?"peer-failed":"peer-closed",player);
        if(state==rtc::PeerConnection::State::Connected)if(auto p=peer.lock()){rtc::Candidate local,remote;if(p->getSelectedCandidatePair(&local,&remote))log("peer_selected_pair",{{"session",owner->identity},{"player",player},{"local",std::string(local)},{"remote",std::string(remote)}});}
    });});
    pc->onIceStateChange([weak,peer,client,player,attempt,files](rtc::PeerConnection::IceState state){if(auto owner=weak.lock())owner->post([owner,peer,client,player,attempt,state,files]{auto p=peer.lock();if(!p||(client&&(files?owner->file_peer_:owner->client_peer_)!=p))return;log(files?"file_peer_ice_state":"peer_ice_state",{{"session",owner->identity},{"player",player},{"role",owner->role},{"attempt",attempt},{"state",static_cast<int>(state)},{"peer_state",static_cast<int>(p->state())},{"gathering_state",static_cast<int>(p->gatheringState())}});});});
    pc->onDataChannel([weak,peer,player,files](std::shared_ptr<rtc::DataChannel> channel){if(auto owner=weak.lock()){if(!owner->post([owner,peer,channel,player,files]{auto p=peer.lock();if(!p){channel->close();return;}if(!files&&channel->label()=="probe")owner->watch_peer(p,channel,player);else if(!files&&channel->label()=="game"&&owner->role=="host")owner->add_data(channel,invalid_socket,player,p);else if(files&&channel->label()=="mods"&&owner->role=="host"&&owner->mods_)owner->mods_->accept_peer(channel);else channel->close();}))channel->close();}else channel->close();});
    return pc;
}
void Session::add_data(const std::shared_ptr<rtc::DataChannel>& channel,NetSocket socket,std::string player,std::shared_ptr<rtc::PeerConnection> peer){
    if(player.empty())player=player_;
    auto count=std::count_if(streams_.begin(),streams_.end(),[&](const auto& stream){return stream->player==player;});
    if(streams_.size()>=128||count>=8||(game_port_==0&&role=="host")){close_socket(socket);channel->close();return;}
    auto stream=std::make_shared<Stream>();stream->data=channel;stream->peer=peer;stream->player=std::move(player);stream->session=identity;
    try{stream->socket=socket==invalid_socket?connect_game(game_port_,stream->connecting):socket;}catch(...){channel->close();return;}
    std::weak_ptr<Session> weak=shared_from_this();std::weak_ptr<Stream> target=stream;
    channel->onMessage([weak,target](rtc::message_variant value){if(auto owner=weak.lock()){if(!owner->post([target,value=std::move(value)]{if(auto stream=target.lock()){
        try{
            if(auto text=std::get_if<std::string>(&value)){if(*text!="EOF"||stream->remote_eof)throw std::runtime_error("Invalid peer EOF");stream->remote_eof=true;return;}
            auto& bytes=std::get<rtc::binary>(value);if(bytes.empty())throw std::runtime_error("Empty peer frame");
            if(bytes[0]==std::byte{'A'}&&bytes.size()==5){uint32_t amount;std::memcpy(&amount,bytes.data()+1,4);amount=ntohl(amount);if(!amount||amount>stream->inflight)throw std::runtime_error("Invalid receive credit");stream->inflight-=amount;}
            else if(bytes[0]==std::byte{'D'}&&bytes.size()>1&&bytes.size()<=16385&&!stream->remote_eof)copy_into(stream->to_socket,std::string_view(reinterpret_cast<const char*>(bytes.data()+1),bytes.size()-1));
            else throw std::runtime_error("Invalid data channel frame");
        }catch(...){stream->dead=true;}
    }})){if(auto stream=target.lock())stream->data->close();}}});
    channel->onOpen([weak]{if(auto owner=weak.lock())owner->wake_.notify();});
    channel->onClosed([weak,target]{if(auto owner=weak.lock())owner->post([target]{if(auto s=target.lock())s->dead=true;});});
    channel->setBufferedAmountLowThreshold(65536);channel->onBufferedAmountLow([weak]{if(auto owner=weak.lock())owner->wake_.notify();});
    streams_.push_back(stream);log("game_path",{{"session",identity},{"player",stream->player},{"stream",stream->trace},{"path","p2p"},{"sctp_stream",channel->id()?Json(*channel->id()):Json(nullptr)}});
}
void Session::start_peer(){
    if(policy_=="relay")return;
    peer_attempts_supported_=false;
    // Probe only during session setup, before game streams are accepted. A
    // TCP-only relay can use another signed topology entry for UDP STUN.
    // libjuice picks just one server, so supply a single responsive endpoint.
    stun_profiles_.clear();
    for(const auto& candidate:alternatives_)if(stun_responds(candidate)){stun_profiles_.push_back(candidate);break;}
    Json selected{{"session",identity},{"count",stun_profiles_.size()}};
    if(!stun_profiles_.empty()){selected["host"]=stun_profiles_.front().host;selected["port"]=stun_profiles_.front().port;}
    log("peer_stun_servers_ready",std::move(selected));
    try{signal_=ssh_->subsystem("buggcraft-peer-v1");peer_started_=true;peer_deadline_=Clock::now()+std::chrono::seconds(12);}catch(...){log("peer_fallback",{{"session",identity},{"reason","signaling-unavailable"}});}
}
void Session::receive_signal(const Json& value){
    auto type=value.value("type","");
    if(type=="ready"){peer_available_=value.value("available",false);if(role=="host")return;if(!peer_available_){become_ready();return;}
        start_client_peer();
    }else if(type=="answer"&&role=="client"&&value.value("files",false)&&file_peer_){
        auto attempt=value.value("attempt",std::string{});
        if(!attempt.empty()&&attempt!=file_attempt_)return;
        auto sdp=value.at("sdp").get<std::string>();validate_sdp(sdp);
        file_peer_->setRemoteDescription(rtc::Description(sdp,"answer"));
        log("file_peer_answer_received",{{"session",identity},{"attempt",file_attempt_},{"candidates",candidate_counts(sdp)}});
    }else if(type=="answer"&&role=="client"&&client_peer_&&peer_negotiating_){
        auto attempt=value.value("attempt",std::string{});
        // Older signaling nodes strip the optional attempt field. Preserve
        // their retries, but never accept an untagged answer after a tagged one.
        if((!attempt.empty()&&attempt!=peer_attempt_)||(attempt.empty()&&peer_attempts_supported_)){
            log("peer_answer_ignored",{{"session",identity},{"reason",attempt.empty()?"missing-attempt":"stale-attempt"},{"attempt",attempt},{"current_attempt",peer_attempt_}});return;
        }
        if(value.value("files",false))return;
        auto sdp=value.at("sdp").get<std::string>();validate_sdp(sdp);client_peer_->setRemoteDescription(rtc::Description(sdp,"answer"));
        if(!attempt.empty())peer_attempts_supported_=true;
        log("peer_answer_received",{{"session",identity},{"attempt",peer_attempt_},{"legacy",attempt.empty()},{"candidates",candidate_counts(sdp)}});
    }
    else if(type=="answer"&&role=="client"){
        auto attempt=value.value("attempt",std::string{});
        log("peer_answer_ignored",{{"session",identity},{"reason",attempt!=peer_attempt_?"stale-attempt":"not-negotiating"},{"attempt",attempt},{"current_attempt",peer_attempt_}});
    }
    else if(type=="offer"&&role=="host"){
        auto key=value.at("peer").get<std::string>();auto attempt=value.value("attempt",std::string{});const bool files=value.value("files",false);
        if(key.size()>64||attempt.size()>64||attempt.find_first_not_of("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-")!=std::string::npos||(!peers_.contains(key)&&peers_.size()>=128))return;
        auto sdp=value.at("sdp").get<std::string>();validate_sdp(sdp);
        auto& peers=files?file_peers_:peers_;
        if(auto old=peers.find(key);old!=peers.end()){old->second->resetCallbacks();old->second->close();peers.erase(old);}
        auto pc=create_peer(key,false,value.value("player","peer-"+key),attempt,files);peers[key]=pc;pc->setRemoteDescription(rtc::Description(sdp,"offer"));pc->setLocalDescription(rtc::Description::Type::Answer);
    }
    else if(type=="left"&&role=="host"){auto key=value.value("peer","");for(auto* peers:{&peers_,&file_peers_})if(auto it=peers->find(key);it!=peers->end()){it->second->close();peers->erase(it);}}
    else if(type=="unavailable"&&role=="client")become_ready();
}
}
