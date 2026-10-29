// SPDX-License-Identifier: AGPL-3.0-or-later
#include "runtime.hpp"
#include "transport_diagnostics.hpp"

void Worker::post(std::function<void()> action) {
    bool notify;
    { std::lock_guard lock(mutex); notify = commands.empty(); commands.push_back(std::move(action)); }
    if (notify) wake.notify();
}
void Worker::start() { thread = std::thread([this] { run(); }); }
void Worker::rpc(const std::shared_ptr<Peer>& peer, Json request, std::function<void(const Json&)> callback) {
    Id id = engine.sequence++;
    request["session"] = peer->id;
    request["worker"] = index;
    request["rid"] = id;
    pending.emplace(id, Pending{peer->id, Clock::now() + std::chrono::seconds(12), std::move(callback)});
    engine.emit(request);
}

void Worker::remove_peer(Id id) {
    auto found = peers.find(id);
    if (found == peers.end()) return;
    found->second->dead = true;
    // Capture the original error before closing channels can change libssh state.
    const auto diagnostic=transport_diagnostics(found->second->session, found->second->traffic, found->second->heartbeat);
    std::vector<Id> closing;
    for (auto& [stream_id, stream] : streams)
        if (stream->client->id == id || stream->owner->id == id) closing.push_back(stream_id);
    for (Id stream : closing) remove_stream(stream);
    for (auto it = pending.begin(); it != pending.end();)
        if (it->second.peer == id) it = pending.erase(it); else ++it;
    const auto& peer = found->second;
    engine.emit({{"op", "disconnected"}, {"session", id}, {"reason", peer->disconnect_reason},
                 {"transport", diagnostic}});
    engine.unroute(id);
    peers.erase(found);
    --engine.connections;
}

void Worker::receive(Json value) {
    auto op = value.value("op", std::string{});
    if (op == "reply") {
        Id id = value.at("rid");
        auto found = pending.find(id);
        if (found != pending.end()) {
            auto callback = std::move(found->second.callback);
            pending.erase(found);
            callback(value.value("result", Json::object()));
        }
        return;
    }
    Id id = value.value("session", Id{0});
    auto found = peers.find(id);
    if (found == peers.end()) return;
    auto& peer = found->second;
    if (op == "disconnect") { peer->dead = true; return; }
    if (op == "stream_close") {
        auto stream = streams.find(value.at("stream").get<Id>());
        if (stream != streams.end()) stream->second->dead = true;
        return;
    }
    auto control = peer->controls.find(value.value("channel", Id{0}));
    if (control == peer->controls.end()) return;
    if (op == "channel_close") control->second->closing = true;
    else if (op == "channel_write") {
        auto bytes = decode(value.at("data").get<std::string>());
        if (bytes.size() + control->second->queued > max_control_buffer) {
            control->second->closing = true;
            control->second->output.clear();
            control->second->queued = 0;
        } else {
            control->second->queued += bytes.size();
            if (!bytes.empty()) control->second->output.push_back(std::move(bytes));
        }
    }
}

void Worker::run() {
    try {
        bool progress = false;
        std::vector<PollFd> fds;
        std::vector<std::shared_ptr<Peer>> current;
        std::vector<Id> closing;
        std::deque<std::function<void()>> actions;
        while (engine.running) {
            fds.clear();
            fds.reserve(peers.size() + 1);
            fds.push_back({wake.fd(), POLLIN, 0});
            current.clear();
            current.reserve(peers.size());
            for (auto& [id, peer] : peers) {
                short events = POLLIN;
                if (ssh_get_poll_flags(peer->session) & SSH_WRITE_PENDING) events |= POLLOUT;
                fds.push_back({ssh_get_fd(peer->session), events, 0});
                current.push_back(peer);
            }
            net_poll(fds.data(), fds.size(), progress ? 0 : 50);
            if (fds.front().revents & POLLIN) wake.drain();
            { std::lock_guard lock(mutex); actions.swap(commands); }
            for (auto& action : actions) action();
            progress = !actions.empty();
            actions.clear();
            for (auto& peer : current) {
                if (peer->worker.load() != this || peer->dead) continue;
                progress |= pump(peer);
            }
            for (auto& [id, stream] : streams) if (!stream->dead) progress |= pump(*stream);
            closing.clear();
            for (auto& [id, stream] : streams) if (stream->dead) closing.push_back(id);
            for (Id id : closing) remove_stream(id);
            closing.clear();
            auto now = Clock::now();
            for (auto& [id, request] : pending) if (request.expires < now) {
                auto peer = peers.find(request.peer);
                if (peer != peers.end() && !peer->second->dead) {
                    peer->second->disconnect_reason = "control-rpc-timeout";
                    peer->second->dead = true;
                }
            }
            for (auto& [id, peer] : peers) if (peer->dead) closing.push_back(id);
            for (Id id : closing) remove_peer(id);
        }
    } catch (const std::exception&) {
        engine.emit({{"op", "fatal"}, {"reason", "network worker failed"}});
        engine.stop();
    }
    while (!peers.empty()) remove_peer(peers.begin()->first);
    streams.clear();
    pending.clear();
}
