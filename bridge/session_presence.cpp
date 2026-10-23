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
            if(probe.port==port){game_available_=probe.available;game_status_=std::move(probe.status);}
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
    next_presence_=Clock::now()+std::chrono::seconds(2);
    auto queue=[this](Json value,bool describe){
        ControlRequest request;request.value=std::move(value);request.presence=describe;request.background=true;
        request.result=std::make_shared<std::promise<Json>>();
        request.output=request.value.dump()+"\n";requests_.push_back(std::move(request));
    };
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
    bool diagnostics=false;
    {std::lock_guard guard(mutex_);diagnostics=room_info_.value("diagnostics",false);}
    if(diagnostics){
        auto latency=game_status_.is_object()?game_status_.value("latency_ms",Json(nullptr)):Json(nullptr);
        queue({{"op","diagnostics"},{"report",{{"control_rtt_ms",control_rtt_ms_},{"game_rtt_ms",latency},
            {"path",role=="host"?(peer_started_?"auto":"relay"):(direct_?"p2p":"relay")}}}},false);
    }
}
}
