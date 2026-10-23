// SPDX-License-Identifier: AGPL-3.0-or-later
#include "../../core/heartbeat.hpp"
#include <functional>
#include <stdexcept>
#include <string>
#include <vector>

void add_heartbeat_tests(std::vector<std::pair<std::string,std::function<void()>>>& cases) {
    using H=Heartbeat;
    const auto require=[](bool value){if(!value)throw std::runtime_error("heartbeat deadline mismatch");};
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
