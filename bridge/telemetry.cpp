// SPDX-License-Identifier: AGPL-3.0-or-later
#include "session.hpp"
namespace bridge {
void Session::sample_stream(Stream& stream, bool upload_moved, bool download_moved) {
    const auto now = Clock::now();
    const auto observe = [&](Progress& progress, bool moved, bool pending, const char* direction) {
        const auto update = progress.observe(moved, pending, now);
        const auto duration = std::max(update.duration_ms, progress.stalled_ms(now));
        stream.max_stall_ms = std::max(stream.max_stall_ms, duration);
        if (!update.started && !update.ended) return;
        log(update.started ? "stream_stall_start" : "stream_stall_end", {
            {"session", identity}, {"player", stream.player}, {"stream", stream.trace},
            {"direction", direction}, {"duration_ms", update.duration_ms},
            {"queued_bytes", stream.to_socket.size() + stream.to_wire.size()},
            {"inflight_bytes", stream.inflight}});
    };
    observe(stream.upload_progress, upload_moved, stream.to_wire.size() || stream.inflight, "upload");
    observe(stream.download_progress, download_moved, stream.to_socket.size(), "download");
    if (now - stream.sampled < std::chrono::seconds(1)) return;
    const double seconds = std::chrono::duration<double>(now - stream.sampled).count();
    log("stream_sample", {
        {"session", identity}, {"player", stream.player}, {"stream", stream.trace},
        {"sent_bytes", stream.sent}, {"received_bytes", stream.received},
        {"sent_delta", stream.sent - stream.sampled_sent},
        {"received_delta", stream.received - stream.sampled_received},
        {"interval_seconds", seconds}, {"max_stall_ms", stream.max_stall_ms},
        {"queued_upload_bytes", stream.to_wire.size()}, {"queued_download_bytes", stream.to_socket.size()},
        {"inflight_bytes", stream.inflight}, {"log_dropped", journal().dropped()},
        {"log_write_errors", journal().errors()}});
    stream.sampled = now;
    stream.sampled_sent = stream.sent;
    stream.sampled_received = stream.received;
}
}
