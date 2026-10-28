// SPDX-License-Identifier: AGPL-3.0-or-later
#pragma once
#include <charconv>
#include <algorithm>
#include <array>
#include <chrono>
#include <cstdint>
#include <optional>
#include <string>
#include <string_view>

namespace bridge {
// One bounded probe per PeerConnection, independent of game streams and SSH.
// Negotiate first: old bridges ignored the probe channel and must not time out.
class PeerHeartbeat {
public:
    using Clock = std::chrono::steady_clock;
    static constexpr auto interval = std::chrono::seconds(5);
    static constexpr auto timeout = std::chrono::seconds(20);
    static constexpr auto maximum_timeout = std::chrono::seconds(60);
    explicit PeerHeartbeat(Clock::time_point now = Clock::now()) : next_(now) {}
    bool enabled() const { return enabled_; }
    bool legacy(Clock::time_point now = Clock::now()) const { return !enabled_ && hellos_ == 3 && now >= next_; }
    std::chrono::milliseconds timeout_window() const {
        const auto adaptive = std::chrono::milliseconds(static_cast<int64_t>(rtt_ms_ * 4.0 + 15000.0));
        return std::clamp(adaptive, std::chrono::duration_cast<std::chrono::milliseconds>(timeout),
                          std::chrono::duration_cast<std::chrono::milliseconds>(maximum_timeout));
    }
    bool expired(Clock::time_point now = Clock::now()) const { return enabled_ && now - seen_ >= timeout_window(); }
    double age_ms(Clock::time_point now = Clock::now()) const {
        return enabled_ ? std::chrono::duration<double,std::milli>(now-seen_).count() : 0;
    }
    double rtt_ms() const { return rtt_ms_; }
    uint64_t replies() const { return replies_; }
    uint64_t probes() const { return serial_; }
    std::optional<std::string> poll(Clock::time_point now = Clock::now()) {
        if (now < next_ || expired(now) || (!enabled_ && hellos_ >= 3)) return {};
        next_ = now + interval;
        if (!enabled_) {
            ++hellos_;
            return "BCH1?";
        }
        ++serial_;
        pending_[serial_ % pending_.size()] = {serial_, now};
        return "BCP1:" + std::to_string(serial_);
    }
    std::optional<std::string> receive(std::string_view value, Clock::time_point now = Clock::now()) {
        if (value.size() > 32) return {};
        if (value == "BCH1?" || (value == "BCH1!" && hellos_)) {
            if (!enabled_) { enabled_ = true; seen_ = now; next_ = now + interval; }
            // Bound replies even if the authenticated peer floods its probe channel.
            if (value == "BCH1?" && (!hello_reply_ || now-last_hello_reply_ >= interval)) {
                hello_reply_ = true; last_hello_reply_ = now; return "BCH1!";
            }
            return {};
        }
        if (!enabled_ || value.size() <= 5) return {};
        const bool ping = value.starts_with("BCP1:"), pong = value.starts_with("BCA1:");
        if (!ping && !pong) return {};
        uint64_t serial = 0;
        auto [end,error] = std::from_chars(value.data()+5,value.data()+value.size(),serial);
        if (error != std::errc{} || end != value.data()+value.size() || !serial) return {};
        if (ping) {
            if (serial <= remote_serial_ || (ping_reply_ && now-last_ping_reply_ < std::chrono::seconds(1))) return {};
            remote_serial_ = serial; ping_reply_ = true; last_ping_reply_ = now;
            return "BCA1:" + std::to_string(serial);
        }
        // Unsolicited, replayed or stale acknowledgements cannot extend the lease.
        const auto& probe = pending_[serial % pending_.size()];
        if (serial <= acknowledged_ || probe.serial != serial || expired(now) ||
            now < probe.sent || now - probe.sent >= maximum_timeout) return {};
        acknowledged_ = serial; seen_ = now; ++replies_;
        rtt_ms_ = std::chrono::duration<double,std::milli>(now-probe.sent).count();
        return {};
    }
private:
    bool enabled_=false, hello_reply_=false, ping_reply_=false;
    unsigned hellos_=0;
    uint64_t serial_=0, acknowledged_=0, remote_serial_=0, replies_=0;
    struct Probe { uint64_t serial=0; Clock::time_point sent{}; };
    // Keep a bounded history: a valid pong may arrive after the next probe.
    std::array<Probe, 16> pending_{};
    Clock::time_point next_, seen_{}, last_hello_reply_{}, last_ping_reply_{};
    double rtt_ms_=0;
};
}
