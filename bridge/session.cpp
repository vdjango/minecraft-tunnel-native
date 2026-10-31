// SPDX-License-Identifier: AGPL-3.0-or-later
#include "session.hpp"
#include "local_game.hpp"
#include "game_wire.hpp"
#include <algorithm>
#include <utility>
namespace bridge {
namespace {
uint32_t mod_upload_limit(const Json& settings){
    if(!settings.contains("mod_transfer_limit_kib"))return 512;
    const auto& value=settings["mod_transfer_limit_kib"];
    if(!value.is_number_integer())throw Failure(-32602,"Invalid mod upload limit");
    if(value.is_number_unsigned()){
        const auto limit=value.get<uint64_t>();
        if(limit<64||limit>10240)throw Failure(-32602,"Mod upload limit must be between 64 and 10240 KiB/s");
        return static_cast<uint32_t>(limit);
    }
    const auto limit=value.get<int64_t>();
    if(limit<64||limit>10240)throw Failure(-32602,"Mod upload limit must be between 64 and 10240 KiB/s");
    return static_cast<uint32_t>(limit);
}
}
void Session::event(Json value){
    std::lock_guard guard(mutex_);
    chat_events_.push_back(std::move(value));
    while(chat_events_.size()>200)chat_events_.pop_front();
}
Json Session::events(){
    std::lock_guard guard(mutex_);auto values=Json::array();
    for(auto& value:chat_events_)values.push_back(value);
    chat_events_.clear();return {{"events",values}};
}
Json Session::retarget(int port,Json game){
    if(role!="host"||port<1||port>65535)throw Failure(-32602,"Invalid game port");
    if(!running_)throw Failure(-32004,"Session stopped");
    if(!game.is_object())throw Failure(-32602,"Invalid game metadata");
    auto applied=std::make_shared<std::promise<Json>>();auto future=applied->get_future();
    Json detected;
    try{detected=game_status(port,3000);}catch(...){throw Failure(-32020,"Minecraft world probe failed");}
    const auto expected=game.value("protocol",Json(nullptr));
    if(!expected.is_null()&&detected.value("protocol",Json(nullptr))!=expected)throw Failure(-32602,"Minecraft version does not match this room");
    game["protocol"]=detected.value("protocol",Json(nullptr));
    if(game.value("version",std::string{}).empty())game["version"]=detected.value("version",std::string{});
    if(!post([this,port,game,applied]() mutable {try{game.update(mods_->metadata());game_port_=port;settings_["game"]=game;game_chat_.reset();start_game_chat();applied->set_value(std::move(game));}catch(...){applied->set_exception(std::current_exception());}}))throw Failure(-32004,"Session stopped");
    if(future.wait_for(std::chrono::seconds(2))!=std::future_status::ready)throw Failure(-32000,"World update timed out");
    game=future.get();
    auto info=request(Json{{"op","configure"},{"game",game}});
    request(Json{{"op","availability"},{"available",true}});{std::lock_guard guard(mutex_);room_info_=info;}return game;
}
Json Session::configure(Json settings){
    if(!settings.is_object())throw Failure(-32602,"Invalid room settings");
    if(role!="host"){
        for(const auto* key:{"game","allow_mod_transfer","mod_transfer_limit_kib"})if(settings.contains(key))throw Failure(-32602,"World provider settings required");
        settings["op"]="configure";return request(std::move(settings));
    }
    if(!running_)throw Failure(-32004,"Session stopped");
    if(settings.contains("game")&&!settings["game"].is_object())throw Failure(-32602,"Invalid game metadata");
    if(settings.contains("allow_mod_transfer")&&!settings["allow_mod_transfer"].is_boolean())throw Failure(-32602,"Invalid mod transfer permission");
    const auto upload_limit=mod_upload_limit(settings);
    auto applied=std::make_shared<std::promise<Json>>();auto future=applied->get_future();
    if(!post([this,settings,applied,upload_limit]() mutable {try{
        auto metadata=mods_->metadata();
        if(!metadata.empty()){auto game=settings.value("game",Json::object());game.update(metadata);settings["game"]=std::move(game);}
        mods_->set_allowed(settings.value("allow_mod_transfer",false),upload_limit);
        settings_=settings;applied->set_value({{"settings",std::move(settings)},{"waiting",!game_port_}});
    }catch(...){applied->set_exception(std::current_exception());}}))throw Failure(-32004,"Session stopped");
    if(future.wait_for(std::chrono::seconds(2))!=std::future_status::ready)throw Failure(-32000,"Room update timed out");
    auto effective=future.get();settings=std::move(effective["settings"]);
    auto update=settings;update["op"]="configure";auto info=request(std::move(update));
    if(effective["waiting"].get<bool>())request(Json{{"op","availability"},{"available",false}});
    {std::lock_guard guard(mutex_);room_info_=info;}
    return info;
}
Session::Session(Profile profile,std::string selected_role,std::string selected_secret,std::string policy,int game_port,Json settings,std::string compression,std::vector<Profile> alternatives,int listen_port,bool announce,std::string game_directory,std::string chat_name,bool game_chat_enabled,std::string game_chat_name,std::string ice_family)
 :profile_(std::move(profile)),policy_(std::move(policy)),compression_(std::move(compression)),ice_family_(std::move(ice_family)),game_port_(game_port),settings_(std::move(settings)),chat_name_(std::move(chat_name)),game_chat_enabled_(game_chat_enabled),game_chat_name_(std::move(game_chat_name)),alternatives_(std::move(alternatives)),requested_listen_port_(listen_port),announce_(announce),role(std::move(selected_role)),secret(std::move(selected_secret)),room(room_id(secret)){
    if(ice_family_!="auto"&&ice_family_!="ipv4"&&ice_family_!="ipv6")throw Failure(-32602,"Invalid ICE address family");
    if(alternatives_.size()>5)alternatives_.resize(5);
    alternatives_.erase(std::remove_if(alternatives_.begin(),alternatives_.end(),[this](const Profile& item){return item.host==profile_.host&&item.port==profile_.port;}),alternatives_.end());
    alternatives_.insert(alternatives_.begin(),profile_);
    mods_=std::make_shared<ModTransfers>();if(role=="host"){
        mods_->configure(game_directory);
        mods_->set_allowed(settings_.contains("allow_mod_transfer")&&settings_["allow_mod_transfer"].is_boolean()&&settings_["allow_mod_transfer"].get<bool>(),mod_upload_limit(settings_));
    }
}
Session::~Session(){stop();}
Json Session::start(){auto future=ready_.get_future();thread_=std::thread([this]{run();});if(future.wait_for(std::chrono::seconds(30))!=std::future_status::ready){stop();throw Failure(-32021,"Room connection timed out");}return future.get();}
void Session::stop(){running_=false;changed_.notify_all();wake_.notify();if(thread_.joinable()&&thread_.get_id()!=std::this_thread::get_id())thread_.join();}
bool Session::post(std::function<void()> action){std::lock_guard guard(mutex_);if(!running_||commands_.size()>=512)return false;const bool notify=commands_.empty();commands_.push_back(std::move(action));if(notify)wake_.notify();return true;}
Json Session::status()const{std::lock_guard guard(mutex_);return {{"session",identity},{"role",role},{"room",room},{"state",state_},{"reason",reason_},{"info",room_info_},{"transport",role=="host"?(peer_started_?"auto":"relay"):(direct_?"p2p":"relay")},{"address",role=="client"?Json("127.0.0.1:"+std::to_string(listen_port_.load())):Json(nullptr)}};}
void Session::become_ready(){
    if(online_)return;online_=true;{std::lock_guard guard(mutex_);state_="online";reason_.clear();}
    auto result=status();if(role=="host")result["invite"]="bugg1-"+secret;if(!ready_sent_){ready_sent_=true;ready_.set_value(result);}
    log("endpoint_online",{{"session",identity},{"role",role},{"transport",direct_?"p2p":"relay"}});
}
void Session::setup(){
    ssh_=std::make_unique<Ssh>(profile_,role+":"+room,secret,4,member_token_);
    control_calls_.clear();
    auto initial_request=[this](Json value){
        auto result=ssh_->request(value);control_calls_.push_back(Clock::now());return result;
    };
    auto ping=initial_request({{"op","ping"}});
    auto capabilities=ping.value("capabilities",Json::array());
    lobby_supported_=std::find(capabilities.begin(),capabilities.end(),"room-lobby-v1")!=capabilities.end();
    player_=ping.value("diagnostic_player","anonymous-"+identity);
    bind_log(identity,player_,room);
    log("player_connected",{{"session",identity},{"role",role},{"native_version",BUGGCRAFT_VERSION},{"ice_family",ice_family_}});
    if(role=="host"){
        game_available_=game_reachable(game_port_);
        raw_forward_=ssh_->forward("127.0.0.1");
        mods_forward_=ssh_->forward("buggcraft-mods");
        if(compression_!="off"){auto capabilities=ping.value("capabilities",Json::array());if(std::find(capabilities.begin(),capabilities.end(),"relay-zstd-v1")!=capabilities.end()){zstd_forward_=ssh_->forward("buggcraft-zstd");zstd_=true;}}
        auto info=initial_request({{"op","describe"}});
        const auto mod_metadata=mods_->metadata();
        if(!settings_.empty()||!mod_metadata.empty()){
            auto config=settings_;
            if(!mod_metadata.empty()){
                Json game=config.value("game",Json::object());game.update(mod_metadata);config["game"]=std::move(game);
            }
            config["op"]="configure";info=initial_request(config);config.erase("op");settings_=std::move(config);
        }
        if(!game_available_)info=initial_request({{"op","availability"},{"available",false}});
        {std::lock_guard guard(mutex_);room_info_=info;}
    }else{
        auto info=initial_request({{"op","describe"}});if(info.value("availability","")=="closed")throw Failure(-32020,"Room closed");
        zstd_=compression_!="off"&&info.value("relay_compression",Json(nullptr))=="zstd-1";
        {std::lock_guard guard(mutex_);room_info_=info;}
        if(listener_==invalid_socket){listener_=socket(AF_INET,SOCK_STREAM,0);if(listener_==invalid_socket)throw std::runtime_error("Local listener unavailable");
        sockaddr_in address{};address.sin_family=AF_INET;address.sin_addr.s_addr=htonl(INADDR_LOOPBACK);
        address.sin_port=htons(static_cast<uint16_t>(listen_port_?listen_port_.load():requested_listen_port_));
        if(bind(listener_,reinterpret_cast<sockaddr*>(&address),sizeof(address))||listen(listener_,16))throw std::runtime_error("Local listener failed");
        SockLen length=sizeof(address);getsockname(listener_,reinterpret_cast<sockaddr*>(&address),&length);listen_port_=ntohs(address.sin_port);nonblocking(listener_);}
    }
    start_peer();
    if(chat_name_.empty())chat_name_="Player";
    if(!game::plain_text(chat_name_,32))throw Failure(-32602,"Invalid chat name");
    if(lobby_supported_){
        Json info;{std::lock_guard guard(mutex_);info=room_info_;}
        initial_request({{"op","member_update"},{"name",chat_name_},{"seq",0},{"task",""},
            {"state",role=="host"?"ready":"idle"},{"config_id",info.value("config_id",std::string{})}});
        auto members=initial_request({{"op","members"}});
        {std::lock_guard guard(mutex_);room_info_["lobby"]=members;}
        event({{"type","members"},{"snapshot",members}});
    }
    next_chat_=Clock::now();start_game_chat();
    if(role=="host"||!signal_)become_ready();
}
void Session::next_route(){
    if(alternatives_.size()<2)return;
    route_index_=(route_index_+1)%alternatives_.size();profile_=alternatives_[route_index_];
    game_available_=false;
    log("node_route_changed",{{"session",identity},{"role",role},{"node",profile_.name.empty()?profile_.host:profile_.name},{"route_index",route_index_}});
    event({{"type","bridge"},{"status","房间连接已切换到备用节点"},{"node",profile_.name.empty()?profile_.host:profile_.name}});
}
void Session::setup_with_fallback(){
    for(size_t attempt=0;;++attempt){
        try{setup();return;}
        catch(const Failure& failure){
            transport_failed_=true;cleanup();transport_failed_=false;
            if(failure.code==-32020||attempt+1>=alternatives_.size())throw;
            next_route();
        }
        catch(...){
            transport_failed_=true;cleanup();transport_failed_=false;
            if(attempt+1>=alternatives_.size())throw;
            next_route();
        }
    }
}
void Session::accept_game(){
    auto socket=accept(listener_,nullptr,nullptr);if(socket==invalid_socket)return;nonblocking(socket);tune_tcp(socket);
    if(streams_.size()>=8){close_socket(socket);log("stream_rejected",{{"session",identity},{"player",player_},{"reason","stream-limit"}});return;}
    if(direct_&&client_peer_){
        // Let SCTP allocate and reuse negotiated stream IDs after channel reset.
        // A monotonically increasing ID exhausts the peer after 511 game/status connections.
        std::shared_ptr<rtc::DataChannel> channel;
        try{channel=client_peer_->createDataChannel("game");}
        catch(const std::exception& error){log("peer_stream_fallback",{{"session",identity},{"player",player_},{"reason","data-channel-create"},{"detail",error.what()}});}
        catch(...){log("peer_stream_fallback",{{"session",identity},{"player",player_},{"reason","data-channel-create"}});}
        if(channel){add_data(channel,socket,{},client_peer_);return;}
        // Keep established P2P streams; only this new socket falls back to relay.
    }
    auto stream=std::make_shared<Stream>();stream->socket=socket;stream->player=player_;stream->session=identity;
    try{
        stream->channel=ssh_channel_new(ssh_->get());if(!stream->channel)throw std::bad_alloc();
        stream->opening=true;stream->relay_port=zstd_?25566:25565;
        streams_.push_back(stream);
    }catch(...){log("stream_open_failed",{{"session",identity}});}
}
void Session::accept_forward(){
    int port=0;char* origin=nullptr;auto channel=ssh_channel_open_forward_port(ssh_->get(),0,&port,&origin,nullptr);
    std::string remote_player=origin?origin:"";ssh_string_free_char(origin);if(!channel)return;
    if(mods_forward_&&port==mods_forward_){mods_->accept(channel);return;}
    if(streams_.size()>=128||(port!=raw_forward_&&port!=zstd_forward_)||(role=="host"&&!game_port_)){free_channel(channel);return;}
    auto stream=std::make_shared<Stream>();stream->channel=channel;stream->session=identity;
    stream->player=remote_player.starts_with("account-")||remote_player.starts_with("anonymous-")?remote_player:"peer-"+stream->trace;
    try{stream->socket=connect_game(game_port_,stream->connecting);if(port==zstd_forward_&&zstd_forward_){stream->codec=std::make_unique<Codec>();copy_into(stream->to_wire,"BCZ1");}streams_.push_back(stream);log("game_path",{{"session",identity},{"player",stream->player},{"stream",stream->trace},{"path","relay"},{"compression",bool(stream->codec)}});}catch(...){log("host_game_unavailable",{{"session",identity},{"player",stream->player}});}
}
void Session::flush(ssh_channel channel,std::string& value){if(!channel||value.empty())return;int count=ssh_channel_write(channel,value.data(),static_cast<uint32_t>(std::min<size_t>(value.size(),65536)));if(count>0)value.erase(0,static_cast<size_t>(count));else if(count==SSH_ERROR)throw std::runtime_error("Control channel write failed");}
void Session::read_controls(){
    char buffer[4096];
    if(signal_&&signal_stage_==2)try{
        flush(signal_,signal_output_);int count=ssh_channel_read_nonblocking(signal_,buffer,sizeof(buffer),0);
        if(count>0){signal_input_.append(buffer,static_cast<size_t>(count));if(signal_input_.size()>32768)throw std::runtime_error("Peer signal limit");size_t newline;while((newline=signal_input_.find('\n'))!=std::string::npos){auto line=signal_input_.substr(0,newline);signal_input_.erase(0,newline+1);try{receive_signal(Json::parse(line));}catch(...){log("peer_signal_rejected",{{"session",identity}});if(role=="client")become_ready();}}}
        else if(count==SSH_ERROR||ssh_channel_is_eof(signal_))throw std::runtime_error("Peer signaling closed");
    }catch(...){
        // A signaling subsystem failure is not an SSH transport failure.
        // Keep room/chat/relay channels and every unrelated player's streams.
        peer_started_=false;peer_negotiating_=false;peer_retry_={};
        free_channel(signal_);signal_input_.clear();signal_output_.clear();
        signal_stage_=0;next_signal_=Clock::now()+std::chrono::seconds(2);
        const bool preserved=client_peer_&&direct_&&client_peer_->state()==rtc::PeerConnection::State::Connected;
        if(client_peer_&&!preserved)fail_peer(client_peer_,"signaling-closed");
        become_ready();log("peer_signal_closed",{{"session",identity},{"ssh_preserved",true},{"p2p_preserved",preserved}});
    }
    if(chat_&&chat_stage_==2)try{
        flush(chat_,chat_output_);int count=ssh_channel_read_nonblocking(chat_,buffer,sizeof(buffer),0);
        if(count>0){chat_input_.append(buffer,static_cast<size_t>(count));if(chat_input_.size()>65536)throw std::runtime_error("Chat frame limit");size_t newline;while((newline=chat_input_.find('\n'))!=std::string::npos){auto value=Json::parse(chat_input_.substr(0,newline));chat_input_.erase(0,newline+1);
            auto type=value.value("type",std::string{});
            if(type=="removed"){stop_reason_="removed";running_=false;changed_.notify_all();}
            if(type=="ready"&&role=="host"&&!game_chat_status_.empty())value["bridge"]=game_chat_status_;
            if(type=="message"&&(!game::plain_text(value.at("text").get<std::string>())||!game::plain_text(value.at("sender").get<std::string>(),32)))throw Failure(-32602,"Invalid chat event");
            auto request=value.value("request",Json(nullptr));if(request.is_string()){
                auto found=chat_pending_.find(request.get<std::string>());if(found!=chat_pending_.end()){
                    if(type=="error")found->second.result->set_exception(std::make_exception_ptr(Failure(-32000,"Node rejected chat message")));
                    else if(type=="message")found->second.result->set_value({{"sent",true}});
                    if(type=="error"||type=="message")chat_pending_.erase(found);
                }
            }
            if(type=="room"&&value.value("state","")=="closed"){stop_reason_="room-closed";running_=false;changed_.notify_all();log("room_closed",{{"session",identity}});}
            if(type=="message"&&value.value("source","")=="lobby"&&!value.value("replay",false)&&game_chat_)try{game_chat_->send(value.at("sender").get<std::string>(),value.at("text").get<std::string>());}catch(...){event({{"type","bridge"},{"status","游戏聊天未同步，消息已送达大厅"}});}
            value.erase("request");event(std::move(value));}}
        else if(count==SSH_ERROR||ssh_channel_is_eof(chat_))throw Failure(-32000,"Chat channel closed");
    }catch(...){free_channel(chat_);chat_stage_=0;chat_input_.clear();chat_output_.clear();next_chat_=Clock::now()+std::chrono::seconds(1);for(auto& [id,pending]:chat_pending_)pending.result->set_exception(std::make_exception_ptr(Failure(-32000,"Chat disconnected; delivery not confirmed")));chat_pending_.clear();event({{"type","offline"},{"error","聊天通道断开，正在恢复"}});log("chat_reconnecting",{{"session",identity}});}
}
void Session::tick(){
    std::deque<std::function<void()>> commands;{std::lock_guard guard(mutex_);commands.swap(commands_);}for(auto& action:commands)action();
    advance_chat();read_controls();advance_requests();if(mods_)mods_->tick(*ssh_);
    if(!ssh_->alive()){stop_reason_="transport-closed";throw std::runtime_error("SSH disconnected");}
    if(!ssh_->heartbeat()){stop_reason_="heartbeat-timeout";log("heartbeat_timeout",{{"session",identity},{"timeout_seconds",20},{"transport",ssh_->diagnostics()}});throw std::runtime_error("SSH heartbeat timed out");}
    advance_presence();
    if(online_&&role=="client"&&announce_&&Clock::now()>=next_announce_){
        next_announce_=Clock::now()+std::chrono::milliseconds(1500);
        announce_world(static_cast<int>(listen_port_.load()),settings_.value("title",std::string("Buggcraft Room")));
    }
    if(role=="host")accept_forward();else if(online_)accept_game();
    advance_peers();
    for(auto& stream:streams_)if(!stream->dead){
        const auto before_sent=stream->sent, before_received=stream->received;
        const auto before_wire=stream->to_wire.size(), before_inflight=stream->inflight;
        try{pump(*stream);}catch(...){stream->dead=true;log("stream_failed",{{"session",identity},{"player",stream->player},{"stream",stream->trace},{"reason","transport-or-frame-error"}});}
        sample_stream(*stream, stream->sent!=before_sent||stream->to_wire.size()<before_wire||stream->inflight<before_inflight,
                      stream->received!=before_received);
    }
    std::erase_if(streams_,[](auto& stream){return stream->dead;});
}
void Session::run(){
    std::vector<PollFd> descriptors;descriptors.reserve(131);
    unsigned retries=0;
    while(running_){
    transport_failed_=false;
    try{setup_with_fallback();retries=0;while(running_){tick();
        descriptors.clear();descriptors.push_back({wake_.fd(),POLLIN,0});
        descriptors.push_back({ssh_->fd(),static_cast<short>(POLLIN|((ssh_get_poll_flags(ssh_->get())&SSH_WRITE_PENDING)?POLLOUT:0)),0});
        if(listener_!=invalid_socket)descriptors.push_back({listener_,POLLIN,0});
        bool buffered=false;for(auto& stream:streams_){
            if(stream->opening)continue;
            short events=0;if(stream->connecting||stream->to_socket.size())events|=POLLOUT;
            if(!stream->connecting&&!stream->local_eof&&(stream->data?stream->inflight<262144&&stream->data->isOpen()&&stream->data->bufferedAmount()<262144:stream->to_wire.free()>=65600))events|=POLLIN;
            if(events)descriptors.push_back({stream->socket,events,0});
            if(stream->channel&&stream->to_socket.free()>=131100&&ssh_channel_poll(stream->channel,0)>0)buffered=true;
        }
        net_poll(descriptors.data(),descriptors.size(),buffered?0:50);
        if(descriptors.front().revents&POLLIN)wake_.drain();
    }}catch(...){
        try{throw;}catch(const Failure& error){if(error.code==-32020)stop_reason_="room-closed";}catch(...){}
        transport_failed_=true;
        if(stop_reason_=="local-close")stop_reason_="transport-failed";
        if(!ready_sent_){ready_sent_=true;ready_.set_exception(std::current_exception());running_=false;}
        else log("endpoint_disconnected",{{"session",identity},{"reason",stop_reason_},{"transport",ssh_?ssh_->diagnostics():Json::object()}});
    }
    online_=false;
    const bool retry=running_&&transport_failed_&&stop_reason_!="room-closed";
    if(retry){
        {std::lock_guard guard(mutex_);state_="reconnecting";reason_=stop_reason_;}
        event({{"type","offline"},{"reason",stop_reason_}});
    }
    cleanup();
    if(!retry)break;
    if(retries>=6){
        stop_reason_="backup-unavailable";
        event({{"type","offline"},{"reason",stop_reason_},{"error","所有缓存节点均不可用，请重新探测或更新接入码"}});
        break;
    }
    next_route();
    const unsigned wait_seconds=std::min(1u<<std::min(retries++,4u),10u);
    log("endpoint_reconnecting",{{"session",identity},{"attempt",retries},{"delay_seconds",wait_seconds}});
    std::unique_lock guard(mutex_);
    changed_.wait_for(guard,std::chrono::seconds(wait_seconds),[this]{return !running_.load();});
    }
    running_=false;online_=false;
    {std::lock_guard guard(mutex_);state_="stopped";reason_=stop_reason_;}
    log("endpoint_stopped",{{"session",identity},{"reason",stop_reason_}});
}
void Session::cleanup(){
    game_chat_.reset();
    for(auto& [id,pending]:chat_pending_)pending.result->set_exception(std::make_exception_ptr(Failure(-32004,"Chat disconnected")));chat_pending_.clear();chat_stage_=0;
    if(!transport_failed_&&stop_reason_!="removed"&&ssh_&&ssh_->alive()&&(role=="host"||lobby_supported_))try{ssh_->request({{"op",lobby_supported_?"leave":"close"}});}catch(...){}
    close_socket(listener_);listener_=invalid_socket;streams_.clear();
    for(auto& request:requests_){free_channel(request.channel);try{request.result->set_exception(std::make_exception_ptr(Failure(-32004,"Session stopped")));}catch(...){}}
    requests_.clear();
    for(auto& [key,peer]:peers_){peer->resetCallbacks();peer->close();}peers_.clear();
    for(auto& [key,peer]:file_peers_){peer->resetCallbacks();peer->close();}file_peers_.clear();
    if(client_peer_){client_peer_->resetCallbacks();client_peer_->close();client_peer_.reset();}
    if(file_peer_){file_peer_->resetCallbacks();file_peer_->close();file_peer_.reset();}
    file_channel_.reset();
    probes_.clear();free_channel(signal_);free_channel(chat_);peer_started_=false;direct_=false;zstd_=false;
    peer_available_=false;peer_negotiating_=false;peer_retry_={};peer_retries_=0;file_negotiating_=false;file_deadline_={};
    signal_stage_=0;next_signal_={};
    signal_input_.clear();signal_output_.clear();chat_input_.clear();chat_output_.clear();
    if(mods_)mods_->disconnect();raw_forward_=zstd_forward_=mods_forward_=0;ssh_.reset();{std::lock_guard guard(mutex_);commands_.clear();}unbind_log(identity);
}
bool Session::control_ready(bool background){
    const auto cutoff=Clock::now()-std::chrono::milliseconds(10250);
    while(!control_calls_.empty()&&control_calls_.front()<cutoff)control_calls_.pop_front();
    // Node policy is 32 requests / 10 s. Keep timing/close headroom and let
    // foreground operations use the budget reserved from background telemetry.
    return control_calls_.size()<(background?24u:28u);
}
void Session::advance_requests(){
    if(requests_.empty())return;auto& request=requests_.front();
    if(request.stage==0&&!request.channel&&!control_ready(request.background)){
        if(request.background){requests_.pop_front();}
        else if(!request.budget_waited){request.budget_waited=true;log("control_budget_wait",{{"session",identity}});}
        return;
    }
    try{
        if(Clock::now()>request.deadline)throw Failure(-32000,"Room operation timed out");
        int result=SSH_OK;
        if(request.stage==0){if(!request.channel){request.channel=ssh_channel_new(ssh_->get());if(!request.channel)throw std::bad_alloc();}result=ssh_channel_open_session(request.channel);}
        else if(request.stage==1)result=ssh_channel_request_subsystem(request.channel,"buggcraft-rooms-v1");
        else if(request.stage==2){if(!request.offset)request.started=Clock::now();result=ssh_channel_write(request.channel,request.output.data()+request.offset,static_cast<uint32_t>(request.output.size()-request.offset));if(result>0){if(!request.offset)control_calls_.push_back(Clock::now());request.offset+=static_cast<size_t>(result);if(request.offset<request.output.size())return;result=SSH_OK;}else if(result==0)return;}
        else{
            char buffer[4096];result=ssh_channel_read_nonblocking(request.channel,buffer,sizeof(buffer),0);
            if(result>0)request.input.append(buffer,static_cast<size_t>(result));
            if(request.input.size()>65536)throw Failure(-32000,"Room response exceeds limit");
            auto end=request.input.find('\n');
            if(end==std::string::npos){if(result==SSH_ERROR||ssh_channel_is_eof(request.channel))throw Failure(-32000,"Room response failed");return;}
            auto response=Json::parse(request.input.substr(0,end));
            if(!response.is_object()||!response.value("ok",false)||!response.contains("result")){
                const auto reason=response.is_object()&&response.contains("error")&&response["error"].is_string()
                    ?response["error"].get<std::string>():std::string{};
                throw Failure(-32000,reason.empty()||reason.size()>200?"Room operation rejected":"Room operation rejected: "+reason);
            }
            if(request.value.value("op",std::string{})=="ping"){
                control_rtt_ms_=std::chrono::duration<double,std::milli>(Clock::now()-request.started).count();
                event({{"type","node-latency"},{"latency_ms",control_rtt_ms_},{"method","application-echo"}});
            }
            if(request.members){auto value=response["result"];
                {std::lock_guard guard(mutex_);room_info_["lobby"]=value;}
                event({{"type","members"},{"snapshot",value}});
            }else if(request.presence){auto value=response["result"];if(value.is_object()){
                    if(value.value("availability","")=="closed"){stop_reason_="room-closed";running_=false;}
                    {std::lock_guard guard(mutex_);if(room_info_.contains("lobby"))value["lobby"]=room_info_["lobby"];room_info_=value;}
                    event({{"type","room"},{"state",value.value("availability","ready")},{"reason",value.value("reason","")},{"title",value.value("title","")},{"game",value.value("game",Json::object())},{"members",value.value("members",0)},{"capacity",value.value("capacity",0)},{"game_players",value.value("game_players",0)},{"transport",direct_?"p2p":"relay"}});
                }}else request.result->set_value(response["result"]);free_channel(request.channel);requests_.pop_front();return;
        }
        if(result==SSH_AGAIN)return;if(result!=SSH_OK)throw Failure(-32000,"Room operation failed");++request.stage;
        wake_.notify();
    }catch(...){if(!request.presence)request.result->set_exception(std::current_exception());free_channel(request.channel);requests_.pop_front();}
}
Json Session::request(Json value){
    if(!value.is_object()||value.dump().size()>8192)throw Failure(-32602,"Invalid room request");
    auto result=std::make_shared<std::promise<Json>>();auto future=result->get_future();
    if(!post([this,result,value]{if(!online_){result->set_exception(std::make_exception_ptr(Failure(-32004,"Session reconnecting")));return;}if(requests_.size()>=8){result->set_exception(std::make_exception_ptr(Failure(-32010,"Room request limit")));return;}ControlRequest request;request.value=value;request.result=result;request.output=value.dump()+"\n";requests_.push_back(std::move(request));}))throw Failure(-32004,"Session stopped");
    if(future.wait_for(std::chrono::seconds(15))!=std::future_status::ready)throw Failure(-32000,"Room operation timed out");return future.get();
}
Json Session::mods(const std::string& method,const Json& params){
    std::shared_ptr<ModTransfers> configured;
    Json mod_metadata=Json::object();
    if(method=="mods.configure"){
        configured=std::make_shared<ModTransfers>();
        configured->configure(params.at("directory").get<std::string>());
        mod_metadata=configured->metadata();
    }
    auto result=std::make_shared<std::promise<Json>>();auto future=result->get_future();
    if(!post([this,result,method,params,configured=std::move(configured)]() mutable {try{
        if(!online_)throw Failure(-32004,"Session reconnecting");
        if(method=="mods.configure"){
            if(role!="host"||game_port_)throw Failure(-32602,"Only a waiting host can change its mod directory");
            configured->set_allowed(settings_.value("allow_mod_transfer",false),mod_upload_limit(settings_));
            mods_=std::move(configured);result->set_value({{"configured",true}});
        }else if(mods_){
            std::shared_ptr<rtc::DataChannel> peer_channel;
            if(role=="client"&&direct_&&(method=="mods.manifest"||method=="mods.fetch")){
                if(method=="mods.fetch"&&(!file_peer_||file_peer_->state()!=rtc::PeerConnection::State::Connected))
                    start_file_peer();
                if(file_peer_&&file_peer_->state()==rtc::PeerConnection::State::Connected){
                    try{
                        if(file_channel_&&file_channel_->isOpen())peer_channel=std::exchange(file_channel_,{});
                        else peer_channel=file_peer_->createDataChannel("mods");
                    }catch(...){peer_channel.reset();}
                }
            }
            result->set_value(mods_->command(method,params,room,role=="host",std::move(peer_channel)));
        }
        else throw Failure(-32601,"Mod transfer is only available to the host");
    }catch(...){result->set_exception(std::current_exception());}}))throw Failure(-32004,"Session stopped");
    if(future.wait_for(std::chrono::seconds(30))!=std::future_status::ready)throw Failure(-32000,"Mod operation timed out");
    auto response=future.get();
    if(method=="mods.configure"){
        if(!mod_metadata.empty()){
            auto current=std::make_shared<std::promise<Json>>();auto current_future=current->get_future();
            if(!post([this,current]{current->set_value(settings_);}))throw Failure(-32004,"Session stopped");
            if(current_future.wait_for(std::chrono::seconds(2))!=std::future_status::ready)throw Failure(-32000,"Room settings unavailable");
            auto settings=current_future.get();Json game=settings.value("game",Json::object());
            game.update(mod_metadata);settings["game"]=std::move(game);configure(std::move(settings));
        }
        response["metadata"]=std::move(mod_metadata);
    }
    return response;
}

}
