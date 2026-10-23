// SPDX-License-Identifier: AGPL-3.0-or-later
#include "session.hpp"
#include <sstream>
namespace bridge {
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
std::shared_ptr<rtc::PeerConnection> Session::create_peer(const std::string& key,bool client,std::string player){
    rtc::Configuration config;config.disableAutoNegotiation=true;config.maxMessageSize=16385;
    // libjuice gathers usable host addresses and uses the OS route for STUN.
    if(profile_.host!="127.0.0.1"&&profile_.host!="::1"&&profile_.host!="localhost")config.iceServers.emplace_back(profile_.host,static_cast<uint16_t>(profile_.port));
    auto pc=std::make_shared<rtc::PeerConnection>(config);std::weak_ptr<Session> weak=shared_from_this();std::weak_ptr<rtc::PeerConnection> peer=pc;
    pc->onGatheringStateChange([weak,peer,key,client](rtc::PeerConnection::GatheringState state){
        if(state!=rtc::PeerConnection::GatheringState::Complete)return;
        if(auto owner=weak.lock())owner->post([owner,peer,key,client]{if(auto p=peer.lock()){try{auto local=p->localDescription();if(!local)return;std::string sdp=*local;validate_sdp(sdp);Json value{{"type",client?"offer":"answer"},{"sdp",sdp}};if(!client)value["peer"]=key;owner->signal(value);log("peer_candidates_ready",{{"session",owner->identity},{"role",owner->role}});}catch(const std::exception&){p->close();log("peer_candidates_rejected",{{"session",owner->identity}});}}});
    });
    pc->onStateChange([weak,peer,client,player](rtc::PeerConnection::State state){if(auto owner=weak.lock())owner->post([owner,peer,client,player,state]{
        auto p=peer.lock();if(!p||(client&&owner->client_peer_!=p))return;
        log("peer_state",{{"session",owner->identity},{"player",player},{"state",static_cast<int>(state)}});
        if(client){owner->direct_=state==rtc::PeerConnection::State::Connected;if(owner->direct_){const bool recovered=owner->peer_retries_>0;owner->peer_negotiating_=false;owner->peer_retry_={};owner->peer_retries_=0;owner->become_ready();if(recovered){log("peer_recovered",{{"session",owner->identity},{"player",player}});owner->event({{"type","bridge"},{"status","直连已恢复，后续连接优先使用直连"}});}}}
        if(state==rtc::PeerConnection::State::Failed||state==rtc::PeerConnection::State::Closed)owner->fail_peer(p,state==rtc::PeerConnection::State::Failed?"peer-failed":"peer-closed",player);
        if(state==rtc::PeerConnection::State::Connected)if(auto p=peer.lock()){rtc::Candidate local,remote;if(p->getSelectedCandidatePair(&local,&remote))log("peer_selected_pair",{{"session",owner->identity},{"player",player},{"local",std::string(local)},{"remote",std::string(remote)}});}
    });});
    pc->onDataChannel([weak,peer,player](std::shared_ptr<rtc::DataChannel> channel){if(auto owner=weak.lock()){if(!owner->post([owner,peer,channel,player]{auto p=peer.lock();if(!p){channel->close();return;}if(channel->label()=="probe")owner->watch_peer(p,channel,player);else if(channel->label()=="game"&&owner->role=="host")owner->add_data(channel,invalid_socket,player,p);else channel->close();}))channel->close();}else channel->close();});
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
    try{signal_=ssh_->subsystem("buggcraft-peer-v1");peer_started_=true;peer_deadline_=Clock::now()+std::chrono::seconds(12);}catch(...){log("peer_fallback",{{"session",identity},{"reason","signaling-unavailable"}});}
}
void Session::receive_signal(const Json& value){
    auto type=value.value("type","");
    if(type=="ready"){peer_available_=value.value("available",false);if(role=="host")return;if(!peer_available_){become_ready();return;}
        start_client_peer();
    }else if(type=="answer"&&role=="client"&&client_peer_&&peer_negotiating_){auto sdp=value.at("sdp").get<std::string>();validate_sdp(sdp);client_peer_->setRemoteDescription(rtc::Description(sdp,"answer"));}
    else if(type=="offer"&&role=="host"){auto key=value.at("peer").get<std::string>();if(key.size()>64||(!peers_.contains(key)&&peers_.size()>=128))return;auto sdp=value.at("sdp").get<std::string>();validate_sdp(sdp);if(auto old=peers_.find(key);old!=peers_.end()){fail_peer(old->second,"peer-renegotiation",value.value("player","peer-"+key));peers_.erase(old);}auto pc=create_peer(key,false,value.value("player","peer-"+key));peers_[key]=pc;pc->setRemoteDescription(rtc::Description(sdp,"offer"));pc->setLocalDescription(rtc::Description::Type::Answer);}
    else if(type=="left"&&role=="host"){auto key=value.value("peer","");if(auto it=peers_.find(key);it!=peers_.end()){it->second->close();peers_.erase(it);}}
    else if(type=="unavailable"&&role=="client")become_ready();
}
}
