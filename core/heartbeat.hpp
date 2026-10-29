// SPDX-License-Identifier: AGPL-3.0-or-later
#pragma once
#include <chrono>
#include <cstdint>
#include <algorithm>

// Count authenticated SSH packets, not TCP ACKs. Busy connections need no
// additional probes. A lost probe never creates an unbounded retry queue.
class Heartbeat {
public:
    using Clock = std::chrono::steady_clock;
    enum class Action { none, probe, expired };
    static constexpr auto interval = std::chrono::seconds(5);
    static constexpr auto timeout = std::chrono::seconds(20);
    explicit Heartbeat(uint64_t phase = 0, Clock::time_point now = Clock::now())
        : seen_(now), next_(now + interval + std::chrono::milliseconds(phase % 1000)), polled_(now),
          phase_(phase % 1000) {}
    Action poll(uint64_t packets, Clock::time_point now = Clock::now()) {
        last_gap_ = now - polled_;
        max_gap_ = std::max(max_gap_, last_gap_);
        polled_ = now;
        if (packets != packets_) {
            packets_ = packets;
            seen_ = now;
            next_ = now + interval + std::chrono::milliseconds(phase_);
        }
        if (now - seen_ >= timeout) return Action::expired;
        if (now < next_) return Action::none;
        next_ = now + interval + std::chrono::milliseconds(phase_);
        ++probes_;
        return Action::probe;
    }
    uint64_t probes() const { return probes_; }
    int64_t receive_age_ms(uint64_t packets, Clock::time_point now = Clock::now()) const {
        return packets != packets_ ? 0 : milliseconds(now - seen_);
    }
    int64_t poll_gap_ms(Clock::time_point now = Clock::now()) const {
        return milliseconds(std::max(last_gap_, now - polled_));
    }
    int64_t max_poll_gap_ms(Clock::time_point now = Clock::now()) const {
        return milliseconds(std::max(max_gap_, now - polled_));
    }
private:
    static int64_t milliseconds(Clock::duration value) {
        return std::chrono::duration_cast<std::chrono::milliseconds>(value).count();
    }
    uint64_t packets_ = 0;
    uint64_t probes_ = 0;
    Clock::time_point seen_, next_, polled_;
    Clock::duration last_gap_{}, max_gap_{};
    uint64_t phase_;
};
