// SPDX-License-Identifier: AGPL-3.0-or-later
#include "route_selector.hpp"
#include <future>
#include <cmath>
namespace bridge {
namespace {
bool same(const Profile& a,const Profile& b){return a.key==b.key&&a.host==b.host&&a.port==b.port;}
}
RouteSelector::RouteSelector(const Profile& profile,const std::vector<Profile>& alternatives){
    candidates_.push_back(Candidate{profile,RouteSample{},Clock::time_point{},0});
    for(const auto& p:alternatives){if(candidates_.size()==6)break;
        if(std::none_of(candidates_.begin(),candidates_.end(),[&](const Candidate& c){return same(c.profile,p);}))candidates_.push_back(Candidate{p,RouteSample{},Clock::time_point{},0});}
}
void RouteSelector::failed(const Profile& profile){
    for(auto& c:candidates_)if(same(c.profile,profile)){c.sample.reachable=false;
        c.failures=std::min(5u,c.failures+1);c.retry=Clock::now()+std::chrono::seconds(1u<<c.failures);}
    refreshed_={};
}
void RouteSelector::announce(const Profile& from,const Profile& to,const char* reason){
    if(same(from,to))return;
    switched_=Clock::now();
    events_.push_back({{"type","node_route_changed"},{"from",from.name},{"to",to.name},{"reason",reason},
        {"message","已切换到 "+to.name+"，后续连接使用新线路"},{"severity","info"}});
    if(events_.size()>16)events_.erase(events_.begin());
    log("node_route_changed",{{"reason",reason}});
}
void RouteSelector::recovered(const Profile& from,const Profile& to){
    announce(from,to,"unavailable");
}
void RouteSelector::refresh(Profile& selected,std::vector<Profile>& alternatives,bool force){
    const auto now=Clock::now();
    if(!force&&refreshed_!=Clock::time_point{}&&now-refreshed_<std::chrono::seconds(30))return;
    refreshed_=now;
    // At most six known, pinned endpoints, three probes at a time. No per-peer
    // all-network fanout and no game-payload benchmark in the background.
    for(size_t first=0;first<candidates_.size();first+=3){
        std::vector<std::future<void>> work;
        for(size_t i=first;i<std::min(first+3,candidates_.size());++i){auto* c=&candidates_[i];
            if(c->retry>now)continue;
            work.push_back(std::async(std::launch::async,[c]{
                try{Ssh ssh(c->profile,"directory:rooms",{},2);const auto started=Clock::now();
                    const auto value=ssh.request({{"op","ping"}},2);
                    const double elapsed=std::chrono::duration<double,std::milli>(Clock::now()-started).count();
                    c->sample.jitter_ms=c->sample.reachable?c->sample.jitter_ms*.75+std::abs(c->sample.rtt_ms-elapsed)*.25:0;
                    c->sample.rtt_ms=c->sample.reachable?c->sample.rtt_ms*.65+elapsed*.35:elapsed;
                    auto health=value.value("routing",Json::object());
                    c->sample.utilization=health.contains("utilization")&&health["utilization"].is_number()?health["utilization"].get<double>():-1;
                    c->sample.remaining=health.contains("remaining_visitors")&&health["remaining_visitors"].is_number_integer()?health["remaining_visitors"].get<int>():-1;
                    c->sample.accepting=health.value("accepting",true);c->sample.reachable=true;
                    c->profile.name=value.value("name",std::string("Node"));c->failures=0;c->retry={};
                }catch(...){c->sample.reachable=false;c->failures=std::min(5u,c->failures+1);
                    c->retry=Clock::now()+std::chrono::seconds(1u<<c->failures);}
            }));}
        for(auto& probe:work)probe.get();
    }
    Candidate* current=nullptr;Candidate* best=nullptr;
    for(auto& c:candidates_){if(same(c.profile,selected))current=&c;
        if(!best||route_score(c.sample)<route_score(best->sample))best=&c;}
    // Directory lookups remain usable when the entire network is full.
    // Admission for hosting is checked separately by the caller.
    if(!best||!std::isfinite(route_score(best->sample))){
        best=nullptr;
        for(auto& c:candidates_)if(c.sample.reachable&&(!best||c.sample.rtt_ms<best->sample.rtt_ms))best=&c;
        if(!best)throw Failure(-32000,"No reachable node");
    }
    const bool cooldown=switched_!=Clock::time_point{}&&now-switched_<std::chrono::seconds(90);
    if(!current||!current->sample.reachable||route_improves(route_score(current->sample),route_score(best->sample),cooldown)){
        const auto old=selected;selected=best->profile;
        const auto reason=!current||!current->sample.reachable?"unavailable":
            !std::isfinite(route_score(current->sample))?"capacity_unavailable":"better_capacity_and_latency";
        announce(old,selected,reason);
    }
    std::vector<Candidate*> ranked;for(auto& c:candidates_)if(!same(c.profile,selected)&&c.sample.reachable)ranked.push_back(&c);
    std::stable_sort(ranked.begin(),ranked.end(),[](auto* a,auto* b){return route_score(a->sample)<route_score(b->sample);});
    alternatives.clear();for(auto* c:ranked){alternatives.push_back(c->profile);if(alternatives.size()==3)break;}
}
Json RouteSelector::status()const{auto rows=Json::array();for(const auto& c:candidates_)rows.push_back({{"name",c.profile.name},
    {"reachable",c.sample.reachable},{"rtt_ms",c.sample.rtt_ms},{"utilization",c.sample.utilization},
    {"remaining_visitors",c.sample.remaining},{"accepting",c.sample.accepting},
    {"score",std::isfinite(route_score(c.sample))?Json(route_score(c.sample)):Json(nullptr)}});return rows;}
Json RouteSelector::events(){auto value=std::move(events_);events_=Json::array();return value;}
std::optional<double> RouteSelector::latency_ms(const Profile& profile)const{
    if(refreshed_==Clock::time_point{}||Clock::now()-refreshed_>std::chrono::seconds(60))return {};
    for(const auto& c:candidates_)if(same(c.profile,profile)&&c.sample.reachable)return c.sample.rtt_ms;
    return {};
}
bool RouteSelector::admissible(const Profile& profile)const{
    for(const auto& c:candidates_)if(same(c.profile,profile))return std::isfinite(route_score(c.sample));
    return false;
}
}
