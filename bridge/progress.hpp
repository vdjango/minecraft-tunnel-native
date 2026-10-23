// SPDX-License-Identifier: AGPL-3.0-or-later
#pragma once
#include <chrono>

namespace bridge {
// Each direction has its own clock: receiving must not hide a stalled upload.
class Progress {
public:
    using Clock = std::chrono::steady_clock;
    struct Update {
        bool started = false, ended = false;
        double duration_ms = 0;
    };
private:
    Clock::time_point since_ = Clock::now();
    bool pending_ = false, stalled_ = false;
public:
    Update observe(bool moved, bool pending, Clock::time_point now) {
        Update result;
        const double gap = std::chrono::duration<double, std::milli>(now - since_).count();
        if (moved || !pending) {
            if (stalled_) { result.ended = true; result.duration_ms = gap; }
            since_ = now;
            stalled_ = false;
        } else if (!pending_) {
            since_ = now;
        } else if (!stalled_ && gap >= 250) {
            stalled_ = true;
            result.started = true;
            result.duration_ms = gap;
        }
        pending_ = pending;
        return result;
    }
    double stalled_ms(Clock::time_point now) const {
        return stalled_ ? std::chrono::duration<double, std::milli>(now - since_).count() : 0;
    }
};
}
