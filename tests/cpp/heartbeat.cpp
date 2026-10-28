// SPDX-License-Identifier: AGPL-3.0-or-later
#include "../../core/heartbeat.hpp"
#include "../../bridge/peer_heartbeat.hpp"
#include <functional>
#include <stdexcept>
#include <string>
#include <vector>

void add_heartbeat_tests(std::vector<std::pair<std::string,std::function<void()>>>& cases) {
    using H=Heartbeat;
    const auto require=[](bool value){if(!value)throw std::runtime_error("heartbeat deadline mismatch");};
    cases.emplace_back("peer_heartbeat_negotiates_and_survives_idle_with_matching_replies",[require]{
        using P=bridge::PeerHeartbeat;const auto now=P::Clock::now();P first(now),second(now);
        require(first.poll(now)=="BCH1?");
        auto answer=second.receive("BCH1?",now);require(answer=="BCH1!");
        first.receive(*answer,now);require(first.enabled()&&second.enabled());
        for(unsigned i=1;i<=120;++i){
            const auto at=now+std::chrono::seconds(5*i);
            auto ping=first.poll(at);require(ping.has_value());
            auto pong=second.receive(*ping,at);require(pong.has_value());
            first.receive(*pong,at+std::chrono::milliseconds(25));
            require(!first.expired(at+std::chrono::seconds(19)));
        }
        require(first.replies()==120&&first.rtt_ms()==25);
    });
    cases.emplace_back("peer_heartbeat_extends_failure_window_for_high_rtt",[require]{
        using P=bridge::PeerHeartbeat;const auto now=P::Clock::now();P peer(now);
        require(peer.poll(now)=="BCH1?");peer.receive("BCH1!",now);
        auto ping=peer.poll(now+std::chrono::seconds(5));require(ping=="BCP1:1");
        peer.receive("BCA1:1",now+std::chrono::seconds(10));
        require(peer.timeout_window()==std::chrono::seconds(35));
        require(!peer.expired(now+std::chrono::seconds(44)));
        require(peer.expired(now+std::chrono::seconds(45)));
    });
    cases.emplace_back("peer_heartbeat_delayed_pong_survives_next_probe_and_rejects_replay",[require]{
        using P=bridge::PeerHeartbeat;const auto now=P::Clock::now();P peer(now);
        peer.poll(now);peer.receive("BCH1!",now);
        require(peer.poll(now+std::chrono::seconds(5))=="BCP1:1");
        require(peer.poll(now+std::chrono::seconds(10))=="BCP1:2");
        peer.receive("BCA1:1",now+std::chrono::seconds(11));
        require(peer.replies()==1&&peer.rtt_ms()==6000);
        require(peer.timeout_window()==std::chrono::seconds(39));
        peer.receive("BCA1:2",now+std::chrono::seconds(12));
        require(peer.replies()==2&&peer.rtt_ms()==2000);
        peer.receive("BCA1:1",now+std::chrono::seconds(20));
        peer.receive("BCA1:2",now+std::chrono::seconds(21));
        peer.receive("BCA1:99",now+std::chrono::seconds(22));
        require(peer.replies()==2&&peer.age_ms(now+std::chrono::seconds(22))==10000);
        require(peer.expired(now+std::chrono::seconds(35)));
        peer.receive("BCA1:2",now+std::chrono::seconds(36));
        require(peer.expired(now+std::chrono::seconds(36)));
    });
    cases.emplace_back("peer_heartbeat_continuous_six_second_rtt_does_not_fall_back",[require]{
        using P=bridge::PeerHeartbeat;const auto now=P::Clock::now();P peer(now);
        peer.poll(now);peer.receive("BCH1!",now);
        for(unsigned second=1;second<=600;++second){
            const auto at=now+std::chrono::seconds(second);
            peer.poll(at);
            if(second>=11&&second%5==1)
                peer.receive("BCA1:"+std::to_string((second-6)/5),at);
            require(!peer.expired(at));
        }
        require(peer.replies()==118&&peer.rtt_ms()==6000);
        require(peer.expired(now+std::chrono::seconds(636)));
    });
    cases.emplace_back("peer_heartbeat_legacy_negotiation_is_bounded_and_never_false_expires",[require]{
        using P=bridge::PeerHeartbeat;const auto now=P::Clock::now();P peer(now);
        for(unsigned i=0;i<3;++i)require(peer.poll(now+std::chrono::seconds(5*i))=="BCH1?");
        require(!peer.poll(now+std::chrono::hours(1))&&peer.legacy(now+std::chrono::hours(1)));
        require(!peer.enabled()&&!peer.expired(now+std::chrono::hours(1)));
    });
    cases.emplace_back("peer_heartbeat_adaptive_timeout_is_capped_and_dead_peer_cannot_revive",[require]{
        using P=bridge::PeerHeartbeat;const auto now=P::Clock::now();P peer(now);
        peer.poll(now);peer.receive("BCH1!",now);
        peer.poll(now+std::chrono::seconds(5));
        peer.receive("BCA1:1",now+std::chrono::seconds(6));
        peer.poll(now+std::chrono::seconds(10));
        peer.receive("BCA1:2",now+std::chrono::seconds(24));
        require(peer.timeout_window()==std::chrono::seconds(60));
        peer.poll(now+std::chrono::seconds(25));
        require(!peer.expired(now+std::chrono::seconds(83)));
        peer.receive("BCA1:3",now+std::chrono::seconds(84));
        require(peer.replies()==2&&peer.probes()==3&&peer.expired(now+std::chrono::seconds(84)));
    });
    cases.emplace_back("peer_heartbeat_replays_invalid_frames_and_one_way_ping_cannot_hide_loss",[require]{
        using P=bridge::PeerHeartbeat;const auto now=P::Clock::now();P peer(now);
        peer.poll(now);peer.receive("BCH1!",now);
        require(peer.poll(now+std::chrono::seconds(5))=="BCP1:1");
        peer.receive("BCA1:1",now+std::chrono::seconds(6));
        for(const auto* frame:{"BCH1!","BCA1:1","BCA1:2","BCA1:0","BCA1:-1","BCA1:1x","BCA1:18446744073709551616","garbage","BCP1:9"})
            peer.receive(frame,now+std::chrono::seconds(24));
        require(!peer.expired(now+std::chrono::seconds(25)));
        require(peer.expired(now+std::chrono::seconds(26)));
        require(!peer.poll(now+std::chrono::seconds(26))&&peer.replies()==1);
    });
    cases.emplace_back("peer_heartbeat_reply_rate_and_peer_isolation",[require]{
        using P=bridge::PeerHeartbeat;const auto now=P::Clock::now();P first(now),second(now);
        require(first.receive("BCH1?",now)=="BCH1!");
        require(!first.receive("BCH1?",now));
        require(first.receive("BCP1:1",now)=="BCA1:1");
        require(!first.receive("BCP1:2",now));
        require(first.receive("BCP1:2",now+std::chrono::seconds(1))=="BCA1:2");
        second.receive("BCH1?",now+std::chrono::seconds(19));
        require(first.expired(now+std::chrono::seconds(20))&&!second.expired(now+std::chrono::seconds(20)));
    });
    cases.emplace_back("heartbeat_probe_and_timeout_without_reply",[require]{
        const auto now=H::Clock::now();H heartbeat(0,now);
        require(heartbeat.poll(0,now+std::chrono::seconds(4))==H::Action::none);
        require(heartbeat.poll(0,now+std::chrono::seconds(5))==H::Action::probe);
        require(heartbeat.poll(0,now+std::chrono::seconds(5))==H::Action::none);
        require(heartbeat.poll(0,now+std::chrono::seconds(10))==H::Action::probe);
        require(heartbeat.poll(0,now+std::chrono::seconds(20))==H::Action::expired);
    });
    cases.emplace_back("heartbeat_receive_resets_deadline_and_suppresses_busy_probes",[require]{
        const auto now=H::Clock::now();H heartbeat(0,now);
        for(unsigned i=1;i<=1000;++i)
            require(heartbeat.poll(i,now+std::chrono::seconds(i))==H::Action::none);
        require(heartbeat.poll(1000,now+std::chrono::seconds(1019))==H::Action::probe);
        require(heartbeat.poll(1000,now+std::chrono::seconds(1020))==H::Action::expired);
    });
    cases.emplace_back("heartbeat_phase_and_peer_isolation",[require]{
        const auto now=H::Clock::now();H first(0,now),second(500,now);
        require(first.poll(0,now+std::chrono::seconds(5))==H::Action::probe);
        require(second.poll(0,now+std::chrono::seconds(5))==H::Action::none);
        require(second.poll(1,now+std::chrono::seconds(20))==H::Action::none);
        require(first.poll(0,now+std::chrono::seconds(20))==H::Action::expired);
    });
}
