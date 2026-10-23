// SPDX-License-Identifier: AGPL-3.0-or-later
#include "runtime.hpp"

bool Worker::transfer(Stream& stream, Direction& direction, ssh_channel from, ssh_channel to, bool upload) {
    bool progress = false;
    if (!direction.eof && direction.pending.free()) {
        size_t count = std::min(direction.pending.writable(), io_quantum);
        int received = ssh_channel_read_nonblocking(from, direction.pending.write_ptr(), static_cast<uint32_t>(count), 0);
        if (received > 0) { direction.pending.produced(static_cast<size_t>(received)); progress = true; }
        else if (received == SSH_ERROR) { stream.dead = true; return progress; }
        if (ssh_channel_is_eof(from)) direction.eof = true;
    }
    auto destination = upload ? stream.owner->session : stream.client->session;
    if (direction.pending.size() && !(ssh_get_status(destination) & SSH_WRITE_PENDING)) {
        size_t count = std::min({direction.pending.readable(), io_quantum,
                                static_cast<size_t>(ssh_channel_window_size(to))});
        if (count) {
            int written = ssh_channel_write(to, direction.pending.read_ptr(), static_cast<uint32_t>(count));
            if (written == SSH_ERROR) { stream.dead = true; return progress; }
            if (written > 0) {
                direction.pending.consumed(static_cast<size_t>(written));
                direction.total += static_cast<uint64_t>(written);
                progress = true;
            }
        }
    }
    bool blocked = !direction.pending.free();
    if (blocked != direction.blocked) {
        direction.blocked = blocked;
        auto now=Clock::now();
        if(blocked)direction.blocked_at=now;
        engine.emit({{"op", "stream_event"}, {"stream", stream.id},
                     {"event", blocked ? "backpressure_start" : "backpressure_end"},
                     {"duration_ms", blocked?0:std::chrono::duration<double,std::milli>(now-direction.blocked_at).count()},
                     {"direction", upload ? "upload" : "download"}});
    }
    if (direction.eof && !direction.pending.size() && !direction.sent_eof) {
        const int status = ssh_channel_send_eof(to);
        if (status == SSH_AGAIN) return progress;
        if (status != SSH_OK) { stream.dead = true; return progress; }
        direction.sent_eof = true;
        engine.emit({{"op", "stream_event"}, {"stream", stream.id}, {"event", "stream_eof"},
                     {"direction", upload ? "upload" : "download"}});
        progress = true;
    }
    return progress;
}

bool Worker::pump(Stream& stream) {
    if (stream.client->dead || stream.owner->dead) { stream.dead = true; return false; }
    if (!stream.open) {
        int status = ssh_channel_open_reverse_forward(stream.host, stream.host_name.c_str(), stream.host_port,
                                                     stream.client_name.c_str(), 0);
        if (status == SSH_AGAIN && Clock::now() - stream.created < std::chrono::seconds(10)) return false;
        if (status != SSH_OK) {
            ssh_message_reply_default(stream.request->value);
            stream.request.reset();
            stream.dead = true;
            return true;
        }
        stream.visitor = ssh_message_channel_request_open_reply_accept(stream.request->value);
        stream.request.reset();
        if (!stream.visitor) { stream.dead = true; return true; }
        stream.open = true;
    }
    bool progress = transfer(stream, stream.up, stream.visitor, stream.host, true);
    progress |= transfer(stream, stream.down, stream.host, stream.visitor, false);
    if ((stream.up.sent_eof && stream.down.sent_eof) ||
        (ssh_channel_is_closed(stream.visitor) && !stream.up.pending.size()) ||
        (ssh_channel_is_closed(stream.host) && !stream.down.pending.size())) stream.dead = true;
    if (Clock::now() - stream.sampled >= std::chrono::seconds(1)) sample(stream);
    return progress;
}

void Worker::sample(Stream& stream) {
    auto up = stream.up.total - stream.up.reported, down = stream.down.total - stream.down.reported;
    if (up || down) engine.emit({{"op", "stream_stats"}, {"stream", stream.id}, {"upload", up}, {"download", down}});
    stream.up.reported = stream.up.total;
    stream.down.reported = stream.down.total;
    stream.sampled = Clock::now();
}
void Worker::remove_stream(Id id) {
    auto found = streams.find(id);
    if (found == streams.end()) return;
    sample(*found->second);
    engine.emit({{"op", "stream_closed"}, {"stream", id}});
    streams.erase(found);
}
