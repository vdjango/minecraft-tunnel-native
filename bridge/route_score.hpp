// SPDX-License-Identifier: AGPL-3.0-or-later
#pragma once
#include <algorithm>
#include <cmath>
#include <limits>
namespace bridge {
struct RouteSample {
    double rtt_ms=0,jitter_ms=0,utilization=-1;
    int remaining=-1;
    bool reachable=false,accepting=true;
};
inline double route_score(const RouteSample& s) {
    if(!s.reachable||!s.accepting||s.remaining==0||!std::isfinite(s.rtt_ms)||s.rtt_ms<0)
        return std::numeric_limits<double>::infinity();
    const double latency=std::min(s.rtt_ms/150.,4.);
    const double load=s.utilization<0?.65:std::clamp(s.utilization,0.,1.);
    const double headroom=s.remaining<0?.65:1./(1.+s.remaining/4.);
    return .45*latency+.30*load*load+.15*headroom+.10*std::min(s.jitter_ms/50.,2.);
}
inline bool route_improves(double previous,double candidate,bool cooling_down) {
    if(!std::isfinite(candidate))return false;
    if(!std::isfinite(previous))return true;
    return !cooling_down&&candidate+.08<previous&&candidate<previous*.80;
}
}
