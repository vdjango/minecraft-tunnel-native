// SPDX-License-Identifier: AGPL-3.0-or-later
#include "session.hpp"
#include "local_game.hpp"
#include <algorithm>

namespace bridge {
void Session::advance_presence(){
    const int port=role=="host"?game_port_:listen_port_.load();
    if(game_probe_.valid()&&game_probe_.wait_for(std::chrono::milliseconds(0))==std::future_status::ready){
        try{
            auto probe=game_probe_.get();
            if(probe.port==port){
                game_available_=probe.available;game_status_=std::move(probe.status);
                auto latency=game_status_.is_object()?game_status_.value("latency_ms",Json(nullptr)):Json(nullptr);
                event({{"type","latency"},{"latency_ms",latency},{"port",port},
                       {"state",latency.is_number_integer()?"ready":probe.available?"timeout":"unavailable"},
                       {"method","minecraft-status-ping"}});
            }
        }catch(...){game_status_=nullptr;}
    }
    if(online_&&port>0&&!game_probe_.valid()&&Clock::now()>=next_game_probe_){
        next_game_probe_=Clock::now()+std::chrono::seconds(2);
        // Status responses, including unresponsive modded worlds, never block forwarding.
        game_probe_=std::async(std::launch::async,[port]{
            GameProbe probe;probe.port=port;probe.available=game_reachable(port);
            if(probe.available)try{probe.status=game_status(port,1000);}catch(...){}
            return probe;
        });
    }
    if(!online_||Clock::now()<next_presence_||requests_.size()>4||!control_ready(true))return;
    next_presence_=Clock::now()+std::chrono::seconds(4);
    auto queue=[this](Json value,bool describe,bool members=false){
        ControlRequest request;request.value=std::move(value);request.presence=describe;request.members=members;request.background=true;
        request.result=std::make_shared<std::promise<Json>>();
        request.output=request.value.dump()+"\n";requests_.push_back(std::move(request));
    };
    queue({{"op","ping"}},false);
    if(role=="host"){
        queue({{"op","availability"},{"available",game_available_}},true);
        Json players=nullptr,maximum=nullptr;
        if(game_status_.is_object()){
            auto counts=game_status_.value("players",Json::object());
            if(counts.is_object()){
                auto count=counts.value("online",Json(nullptr)),limit=counts.value("max",Json(nullptr));
                if(count.is_number_integer()&&count>=0&&count<=100000)
                    players=std::max(0,count.get<int>()-int(game_chat_&&game_chat_->ready()));
                if(limit.is_number_integer()&&limit>=0&&limit<=100000)maximum=limit;
            }
        }
        queue({{"op","presence"},{"players",players},{"maximum",maximum}},true);
    }else queue({{"op","describe"}},true);
    if(lobby_supported_){
        Json latency=role=="host"?Json(nullptr):control_rtt_ms_;
        std::string kind=role=="host"?"local":"node";
        for(const auto& watch:probes_)if(role=="client"&&direct_&&watch->heartbeat.replies()>0){
            latency=watch->heartbeat.rtt_ms();kind="game";break;
        }
        if(role=="client"&&game_status_.is_object()&&game_status_.value("latency_ms",Json(nullptr)).is_number()){
            latency=game_status_["latency_ms"];kind="game";
        }
        queue({{"op","members"},{"path",role=="host"?"local":direct_?"p2p":"relay"},
            {"rtt_ms",latency},{"rtt_kind",kind}},false,true);
    }
    bool diagnostics=false;
    {std::lock_guard guard(mutex_);diagnostics=room_info_.value("diagnostics",false);}
    if(diagnostics){
        auto latency=game_status_.is_object()?game_status_.value("latency_ms",Json(nullptr)):Json(nullptr);
        Json peer_latency=nullptr;
        double peer_latency_total=0;
        size_t peer_latency_count=0;
        for(const auto& watch:probes_)if(watch->heartbeat.enabled()&&watch->heartbeat.replies()>0){
            peer_latency_total+=watch->heartbeat.rtt_ms();++peer_latency_count;
        }
        if(peer_latency_count)peer_latency=peer_latency_total/static_cast<double>(peer_latency_count);
        std::string path=role=="host"?(peer_started_?"auto":"relay"):(direct_?"p2p":"relay");
        if(role=="host"){
            bool p2p=false,relay=false;
            for(const auto& stream:streams_){p2p=p2p||bool(stream->data);relay=relay||bool(stream->channel);}
            if(p2p&&relay)path="mixed";else if(p2p)path="p2p";else if(relay)path="relay";
        }
        queue({{"op","diagnostics"},{"report",{{"control_rtt_ms",control_rtt_ms_},{"game_rtt_ms",latency},
            {"peer_rtt_ms",peer_latency},{"path",path}}}},false);
    }
}
}
