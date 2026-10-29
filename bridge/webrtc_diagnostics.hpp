// SPDX-License-Identifier: AGPL-3.0-or-later
#pragma once
#include <nlohmann/json.hpp>
#include <charconv>
#include <string_view>
#include <utility>

namespace bridge {
// libdatachannel/juice warnings can embed SDP, ICE passwords and usernames.
// Emit only fixed categories and numeric metadata, including for unknown errors.
// The library callback is process-wide: never guess a player/session identity.
inline nlohmann::json webrtc_warning_fields(int level, std::string_view message) {
    using namespace std::literals;
    const auto original_size = message.size();
    message = message.substr(0, 4096);
    const char* component = "unknown";
    const auto at = message.find('@');
    const auto colon = at == message.npos ? message.npos : message.find(": ", at);
    const auto source = colon == message.npos ? std::string_view{} : message.substr(0, at);
    for (const auto& [name, category] : {
            std::pair{"IceTransport"sv, "ice"}, {"DtlsTransport"sv, "dtls"},
            {"SctpTransport"sv, "sctp"}, {"DataChannel"sv, "data-channel"},
            {"PeerConnection"sv, "peer"}, {"PollInterrupter"sv, "socket"},
            {"TcpServer"sv, "socket"}, {"TcpTransport"sv, "socket"}}) {
        if (source.find(name) != source.npos) { component = category; break; }
    }
    nlohmann::json result{{"warning_schema", 1}, {"scope", "runtime"},
        {"level", level == 1 ? "fatal" : level == 2 ? "error" : level == 3 ? "warning" : "unknown"},
        {"component", component}, {"reason", "unclassified"}, {"message_bytes", original_size}};
    if (colon != message.npos) {
        unsigned line = 0;
        const auto number = message.substr(at + 1, colon - at - 1);
        const auto parsed = std::from_chars(number.data(), number.data() + number.size(), line);
        if (parsed.ec == std::errc{} && parsed.ptr == number.data() + number.size() && line <= 1000000)
            result["source_line"] = line;
        message.remove_prefix(colon + 2);
    }
    struct Rule { std::string_view text; const char* component; const char* reason; };
    static constexpr Rule rules[] = {
        {"Unable to resolve STUN server address", "stun", "dns-failed"},
        {"STUN server address resolution failed", "stun", "dns-failed"},
        {"STUN message send failed", "stun", "send-failed"},
        {"STUN integrity check failed", "stun", "integrity-failed"},
        {"STUN local ufrag check failed", "stun", "local-ufrag-mismatch"},
        {"STUN remote ufrag check failed", "stun", "remote-ufrag-mismatch"},
        {"STUN username invalid", "stun", "username-invalid"},
        {"Got STUN Binding error response", "stun", "binding-rejected"},
        {"No local host candidates gathered", "ice", "no-local-candidates"},
        {"Failed to gather local host candidates", "ice", "gathering-failed"},
        {"Rejected ICE candidate", "ice", "candidate-rejected"},
        {"Failed to parse remote SDP", "ice", "remote-description-invalid"},
        {"Missing ICE user fragment", "ice", "remote-ufrag-missing"},
        {"Missing ICE password", "ice", "remote-password-missing"},
        {"ICE timeout", "ice", "timeout"},
        {"ICE role conflict", "ice", "role-conflict"},
        {"Lost connectivity", "ice", "connectivity-lost"},
        {"Sending keepalive failed", "ice", "keepalive-send-failed"},
        {"Only TURN servers are supported as additional ICE servers", "ice", "additional-stun-unsupported"},
        {"TURN transports TCP and TLS are not supported with libjuice", "ice", "turn-transport-unsupported"},
        {"DTLS handshake failed", "dtls", "handshake-failed"},
        {"DTLS alert", "dtls", "alert"},
        {"DTLS recv", "dtls", "receive-failed"},
        {"SCTP connection failed", "sctp", "connection-failed"},
        {"SCTP sending failed", "sctp", "send-failed"},
        {"SCTP shutdown failed", "sctp", "shutdown-failed"},
        {"SCTP flush", "sctp", "flush-failed"},
        {"Writing to interrupter socket failed", "socket", "wakeup-write-failed"},
        {"TCP server socket binding", "socket", "bind-failed"},
        {"TCP server socket listening failed", "socket", "listen-failed"},
        {"Uncaught exception in callback", "callback", "exception"},
    };
    for (const auto& rule : rules) if (message.find(rule.text) != message.npos) {
        result["component"] = rule.component;
        result["reason"] = rule.reason;
        break;
    }
    if (result["component"] == "dtls" && message.find("Handshake timeout") != message.npos)
        result["reason"] = "handshake-timeout";
    // Never copy a suffix after errno: some log messages append credentials.
    if (const auto offset = message.find("errno="); offset != message.npos) {
        const auto number = message.substr(offset + 6);
        int code = 0;
        const auto parsed = std::from_chars(number.data(), number.data() + number.size(), code);
        if (parsed.ec == std::errc{} && parsed.ptr != number.data() && code >= 0 && code <= 65535 &&
            (parsed.ptr == number.data() + number.size() || *parsed.ptr == ' ' || *parsed.ptr == ','))
            result["system_error"] = code;
    }
    return result;
}
}
