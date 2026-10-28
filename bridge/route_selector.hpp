// SPDX-License-Identifier: AGPL-3.0-or-later
#pragma once
#include "ssh.hpp"
#include "route_score.hpp"
#include <vector>
#include <optional>
namespace bridge {
class RouteSelector {
    struct Candidate {Profile profile;RouteSample sample;Clock::time_point retry{};unsigned failures=0;};
    std::vector<Candidate> candidates_;
    Clock::time_point refreshed_{},switched_{};
    Json events_=Json::array();
    void announce(const Profile&,const Profile&,const char* reason);
public:
    RouteSelector(const Profile&,const std::vector<Profile>&);
    void refresh(Profile&,std::vector<Profile>&,bool force=false);
    void failed(const Profile&);
    void recovered(const Profile&,const Profile&);
    Json status()const;
    bool admissible(const Profile&)const;
    std::optional<double> latency_ms(const Profile&)const;
    Json events();
};
}
