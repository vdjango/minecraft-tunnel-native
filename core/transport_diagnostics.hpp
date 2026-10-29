// SPDX-License-Identifier: AGPL-3.0-or-later
#pragma once
#include "heartbeat.hpp"
#include <libssh/libssh.h>
#include <nlohmann/json.hpp>
#include <string_view>

// Never copy libssh error text: a peer's disconnect message is arbitrary text.
inline const char* ssh_error_kind(std::string_view message) {
    if (message.empty()) return "none";
    if (message == "Socket error: disconnected") return "socket-eof";
    if (message == "Socket error: connection reset by peer") return "socket-reset";
    if (message == "Socket error: connection timed out") return "socket-timeout";
    if (message == "Socket error: connection aborted") return "socket-aborted";
    if (message == "Socket error: network down" || message == "Socket error: network unreachable" ||
        message == "Socket error: network reset" || message == "Socket error: host unreachable")
        return "network-unreachable";
    if (message.starts_with("Received SSH_MSG_DISCONNECT: ")) return "remote-disconnect";
    if (message == "HMAC error") return "packet-integrity";
    if (message.starts_with("read_packet(): Packet len too high(")) return "packet-length";
    if (message.starts_with("Writing packet: error on socket (or connection closed): ")) return "socket-write";
    return "unclassified";
}

inline nlohmann::json transport_diagnostics(ssh_session session, const ssh_counter_struct& traffic,
                                          const Heartbeat& heartbeat) {
    const auto now = Heartbeat::Clock::now();
    const char* message = ssh_get_error(session);
    return {{"schema", 1}, {"ssh_error_code", ssh_get_error_code(session)},
            {"ssh_error_kind", ssh_error_kind(message ? message : "")},
            {"ssh_status", ssh_get_status(session)}, {"ssh_connected", ssh_is_connected(session) != 0},
            {"ssh_rx_packets", traffic.in_packets}, {"ssh_tx_packets", traffic.out_packets},
            {"ssh_rx_bytes", traffic.in_bytes}, {"ssh_tx_bytes", traffic.out_bytes},
            {"rx_age_ms", heartbeat.receive_age_ms(traffic.in_packets, now)},
            {"poll_gap_ms", heartbeat.poll_gap_ms(now)},
            {"max_poll_gap_ms", heartbeat.max_poll_gap_ms(now)},
            {"probe_attempts", heartbeat.probes()}};
}
