// SPDX-License-Identifier: AGPL-3.0-or-later
#pragma once
#include <chrono>
#include <cstdint>

// Count authenticated SSH packets, not TCP ACKs. Busy connections need no
// additional probes. A lost probe never creates an unbounded retry queue.
class Heartbeat {
public:
    using Clock = std::chrono::steady_clock;
    enum class Action { none, probe, expired };
    static constexpr auto interval = std::chrono::seconds(5);
    static constexpr auto timeout = std::chrono::seconds(20);
    explicit Heartbeat(uint64_t phase = 0, Clock::time_point now = Clock::now())
        : seen_(now), next_(now + interval + std::chrono::milliseconds(phase % 1000)),
          phase_(phase % 1000) {}
    Action poll(uint64_t packets, Clock::time_point now = Clock::now()) {
        if (packets != packets_) {
            packets_ = packets;
            seen_ = now;
            next_ = now + interval + std::chrono::milliseconds(phase_);
        }
        if (now - seen_ >= timeout) return Action::expired;
        if (now < next_) return Action::none;
        next_ = now + interval + std::chrono::milliseconds(phase_);
        return Action::probe;
    }
private:
    uint64_t packets_ = 0;
    Clock::time_point seen_, next_;
    uint64_t phase_;
};
