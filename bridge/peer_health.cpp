// SPDX-License-Identifier: AGPL-3.0-or-later
#include "session.hpp"
#include <algorithm>

namespace bridge {
void Session::watch_peer(const std::shared_ptr<rtc::PeerConnection>& peer,const std::shared_ptr<rtc::DataChannel>& channel,std::string player){
    if(!peer||probes_.size()>=128||std::any_of(probes_.begin(),probes_.end(),[&](const auto& watch){return !watch->dead&&watch->peer.lock()==peer;})){channel->close();return;}
    auto watch=std::make_shared<PeerWatch>();watch->peer=peer;watch->channel=channel;watch->player=std::move(player);
    probes_.push_back(watch);
    std::weak_ptr<Session> weak=shared_from_this();std::weak_ptr<PeerWatch> target=watch;
    channel->onOpen([weak]{if(auto owner=weak.lock())owner->wake_.notify();});
    channel->onClosed([weak]{if(auto owner=weak.lock())owner->wake_.notify();});
    channel->onMessage([weak,target](rtc::message_variant value){
        auto text=std::get_if<std::string>(&value);if(!text||text->size()>32)return;
        if(auto owner=weak.lock())owner->post([owner,target,text=std::move(*text)]{
            auto watch=target.lock();if(!watch||watch->dead)return;
            const bool enabled=watch->heartbeat.enabled();
            auto reply=watch->heartbeat.receive(text);
            if(!enabled&&watch->heartbeat.enabled())log("peer_heartbeat_enabled",{{"session",owner->identity},{"player",watch->player},{"interval_seconds",5},{"timeout_seconds",20}});
            if(reply&&watch->channel->isOpen()&&watch->channel->bufferedAmount()<256)try{watch->channel->send(*reply);}catch(...){}
        });
    });
}
void Session::fail_peer(const std::shared_ptr<rtc::PeerConnection>& peer,const std::string& reason,const std::string& player){
    if(!peer)return;
    bool changed=false;
    for(auto& watch:probes_)if(!watch->dead&&watch->peer.lock()==peer){watch->dead=true;changed=true;}
    for(auto& stream:streams_)if(stream->peer.lock()==peer)stream->dead=true;
    if(role=="client"&&client_peer_==peer){
        direct_=false;peer_negotiating_=false;
        if(peer_retry_==Clock::time_point{}&&signal_&&peer_available_){
            const auto delay=std::min(2u<<std::min(peer_retries_++,4u),30u);
            peer_retry_=Clock::now()+std::chrono::seconds(delay);
            log("peer_retry_scheduled",{{"session",identity},{"reason",reason},{"delay_seconds",delay}});
            changed=true;
        }
        become_ready();
    }
    peer->resetCallbacks();peer->close();
    if(changed){
        log("peer_fallback",{{"session",identity},{"player",player.empty()?player_:player},{"reason",reason},{"ssh_preserved",true}});
        if(role=="client")event({{"type","bridge"},{"status","直连暂时不可用，已启用中转并尝试恢复"}});
    }
}
void Session::start_client_peer(){
    if(client_peer_){
        for(auto& watch:probes_)if(watch->peer.lock()==client_peer_)watch->dead=true;
        client_peer_->resetCallbacks();client_peer_->close();
    }
    client_peer_=create_peer({},true,player_);
    rtc::DataChannelInit init;init.id=1;
    watch_peer(client_peer_,client_peer_->createDataChannel("probe",init),player_);
    peer_retry_={};peer_negotiating_=true;peer_deadline_=Clock::now()+std::chrono::seconds(12);
    client_peer_->setLocalDescription(rtc::Description::Type::Offer);
}
void Session::advance_peers(){
    const auto now=Clock::now();
    for(auto& watch:probes_){
        if(watch->dead)continue;
        auto peer=watch->peer.lock();if(!peer){watch->dead=true;continue;}
        if(watch->channel->isClosed()){
            if(watch->heartbeat.enabled())fail_peer(peer,"probe-closed",watch->player);
            else watch->dead=true;
            continue;
        }
        if(!watch->channel->isOpen())continue;
        if(watch->heartbeat.expired(now)){
            log("peer_heartbeat_timeout",{{"session",identity},{"player",watch->player},{"timeout_seconds",20},{"last_reply_age_ms",watch->heartbeat.age_ms(now)}});
            fail_peer(peer,"heartbeat-timeout",watch->player);continue;
        }
        if(watch->channel->bufferedAmount()<256)if(auto message=watch->heartbeat.poll(now))try{watch->channel->send(*message);}catch(...){fail_peer(peer,"probe-send-failed",watch->player);}
        if(watch->heartbeat.legacy()&&!watch->legacy_logged){watch->legacy_logged=true;log("peer_heartbeat_legacy",{{"session",identity},{"player",watch->player},{"reason","application-probe-unsupported"}});}
        if(watch->heartbeat.enabled()&&now>=watch->next_sample){
            watch->next_sample=now+std::chrono::seconds(30);
            log("peer_heartbeat_sample",{{"session",identity},{"player",watch->player},{"rtt_ms",watch->heartbeat.rtt_ms()},{"replies",watch->heartbeat.replies()},{"last_reply_age_ms",watch->heartbeat.age_ms(now)}});
        }
    }
    std::erase_if(probes_,[](const auto& watch){return watch->dead;});
    if(peer_negotiating_&&now>=peer_deadline_)fail_peer(client_peer_,"negotiation-timeout");
    if(!online_&&!peer_negotiating_&&now>=peer_deadline_){become_ready();log("peer_fallback",{{"session",identity},{"reason","signaling-timeout"}});}
    if(peer_retry_!=Clock::time_point{}&&now>=peer_retry_&&signal_&&peer_available_){
        log("peer_retry_started",{{"session",identity},{"attempt",peer_retries_}});
        try{start_client_peer();}catch(...){peer_retry_={};fail_peer(client_peer_,"negotiation-failed");}
    }
}
}
