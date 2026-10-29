// SPDX-License-Identifier: AGPL-3.0-or-later
#include "runtime.hpp"

void Worker::handle(const std::shared_ptr<Peer>& peer, const Msg& holder) {
    auto message = holder->value;
    int type = ssh_message_type(message), subtype = ssh_message_subtype(message);
    if (type == SSH_REQUEST_SERVICE) { ssh_message_service_reply_success(message); return; }
    if (type == SSH_REQUEST_AUTH && !peer->authenticated) {
        if (peer->auth_pending || peer->auth_attempts >= 6) { peer->dead = true; return; }
        if (subtype != SSH_AUTH_METHOD_PASSWORD && subtype != SSH_AUTH_METHOD_PUBLICKEY) {
            ssh_message_auth_set_methods(message, SSH_AUTH_METHOD_PASSWORD | SSH_AUTH_METHOD_PUBLICKEY);
            ssh_message_reply_default(message);
            return;
        }
        std::string username = ssh_message_auth_user(message);
        if (username.size() > 160) { peer->dead = true; return; }
        size_t assigned = engine.affinity(username);
        if (assigned != index) {
            peers.erase(peer->id);
            auto* destination = engine.workers[assigned].get();
            peer->worker.store(destination);
            engine.route(peer->id, assigned);
            destination->post([peer, holder, destination] {
                destination->peers.emplace(peer->id, peer);
                destination->handle(peer, holder);
            });
            return;
        }
        peer->username = username;
        Json request{{"op", "auth"}, {"username", username}};
        bool signed_key = true;
        if (subtype == SSH_AUTH_METHOD_PASSWORD) {
            const char* password = ssh_message_auth_password(message);
            if (!password || std::strlen(password) > 16384) { ssh_message_reply_default(message); return; }
            request["method"] = "password";
            request["password"] = password;
        } else {
            auto state = ssh_message_auth_publickey_state(message);
            auto key = ssh_message_auth_pubkey(message);
            if (!key || (state != SSH_PUBLICKEY_STATE_NONE && state != SSH_PUBLICKEY_STATE_VALID)) {
                ssh_message_reply_default(message); return;
            }
            char* exported = nullptr;
            if (ssh_pki_export_pubkey_base64(key, &exported) != SSH_OK) { peer->dead = true; return; }
            request["method"] = "publickey";
            request["key"] = std::string(ssh_key_type_to_char(ssh_key_type(key))) + " " + exported;
            ssh_string_free_char(exported);
            signed_key = state == SSH_PUBLICKEY_STATE_VALID;
        }
        request["signed"] = signed_key;
        peer->auth_pending = true;
        ++peer->auth_attempts;
        rpc(peer, request, [peer, holder, signed_key](const Json& result) {
            peer->auth_pending = false;
            if (result.value("allowed", false)) {
                if (signed_key) {
                    if (ssh_message_auth_reply_success(holder->value, 0) != SSH_OK) peer->dead = true;
                    else peer->authenticated = true;
                } else ssh_message_auth_reply_pk_ok_simple(holder->value);
            } else {
                ssh_message_auth_set_methods(holder->value, SSH_AUTH_METHOD_PASSWORD | SSH_AUTH_METHOD_PUBLICKEY);
                ssh_message_reply_default(holder->value);
            }
        });
        return;
    }
    if (!peer->authenticated) { ssh_message_reply_default(message); return; }
    if (type == SSH_REQUEST_GLOBAL) {
        if (subtype == SSH_GLOBAL_REQUEST_KEEPALIVE) { ssh_message_global_request_reply_success(message, 0); return; }
        if (subtype == SSH_GLOBAL_REQUEST_TCPIP_FORWARD || subtype == SSH_GLOBAL_REQUEST_CANCEL_TCPIP_FORWARD) {
            std::string host = ssh_message_global_request_address(message);
            int port = ssh_message_global_request_port(message);
            rpc(peer, {{"op", subtype == SSH_GLOBAL_REQUEST_TCPIP_FORWARD ? "forward" : "cancel_forward"},
                       {"host", host}, {"port", port}}, [holder](const Json& result) {
                if (result.value("allowed", false)) ssh_message_global_request_reply_success(holder->value, result.value("port", 0));
                else ssh_message_reply_default(holder->value);
            });
            return;
        }
    } else if (type == SSH_REQUEST_CHANNEL_OPEN) {
        if (subtype == SSH_CHANNEL_SESSION && peer->controls.size() < 4) {
            ssh_channel channel = ssh_message_channel_request_open_reply_accept(message);
            if (channel) {
                Id id = engine.sequence++;
                peer->controls.emplace(id, std::make_unique<Control>(id, channel));
            }
            return;
        }
        if (subtype == SSH_CHANNEL_DIRECT_TCPIP && streams.size() < engine.config.at("max_streams").get<size_t>()) {
            std::string host = ssh_message_channel_request_open_destination(message);
            int port = ssh_message_channel_request_open_destination_port(message);
            rpc(peer, {{"op", "stream_open"}, {"host", host}, {"port", port}}, [this, peer, holder](const Json& result) {
                Id id = result.value("stream", Id{0});
                auto target = peers.find(result.value("owner", Id{0}));
                if (!result.value("allowed", false) || target == peers.end() || target->second->dead) {
                    ssh_message_reply_default(holder->value);
                    if (id) engine.emit({{"op", "stream_closed"}, {"stream", id}});
                    return;
                }
                streams.emplace(id, std::make_unique<Stream>(id, peer, target->second, holder,
                    result.at("host").get<std::string>(), result.at("port").get<int>(),
                    engine.config.at("buffer_bytes").get<size_t>(),result.value("player","127.0.0.1")));
            });
            return;
        }
    } else if (type == SSH_REQUEST_CHANNEL) {
        auto channel = ssh_message_channel_request_channel(message);
        for (auto& [id, control] : peer->controls) {
            if (control->channel != channel) continue;
            if (subtype == SSH_CHANNEL_REQUEST_SUBSYSTEM && !control->started && !control->inflight) {
                control->inflight = true;
                std::string name = ssh_message_channel_request_subsystem(message);
                rpc(peer, {{"op", "subsystem"}, {"channel", id}, {"name", name}}, [peer, id, holder](const Json& result) {
                    auto found = peer->controls.find(id);
                    if (found == peer->controls.end()) return;
                    found->second->inflight = false;
                    if (result.value("allowed", false)) {
                        found->second->started = true;
                        ssh_message_channel_request_reply_success(holder->value);
                    } else {
                        ssh_message_reply_default(holder->value);
                        found->second->closing = true;
                    }
                });
                return;
            }
        }
    }
    ssh_message_reply_default(message);
}

bool Worker::pump(const std::shared_ptr<Peer>& peer) {
    if (!peer->kex) {
        int status = ssh_handle_key_exchange(peer->session);
        if (status == SSH_ERROR) { peer->dead = true; return false; }
        if (status == SSH_OK) peer->kex = true;
        else return false;
    }
    bool progress = false;
    for (int count = 0; count < 16; ++count) {
        auto message = ssh_message_get(peer->session);
        if (!message) break;
        handle(peer, std::make_shared<Message>(message));
        progress = true;
        if (peer->worker.load() != this || peer->dead) return progress;
    }
    if (!ssh_is_connected(peer->session) || (ssh_get_status(peer->session) & SSH_CLOSED_ERROR)) peer->dead = true;
    if (!peer->authenticated && Clock::now() - peer->created > std::chrono::seconds(15)) peer->dead = true;
    if (peer->dead) return progress;
    if (peer->authenticated) {
        const auto action = peer->heartbeat.poll(peer->traffic.in_packets);
        if (action == Heartbeat::Action::expired) {
            peer->disconnect_reason = "heartbeat-timeout";
            peer->dead = true;
            return progress;
        }
        if (action == Heartbeat::Action::probe) ssh_send_keepalive(peer->session);
    }
    for (auto it = peer->controls.begin(); it != peer->controls.end();) {
        progress |= pump(*it->second, peer);
        auto& control = *it->second;
        if ((control.closing && !control.queued) || ssh_channel_is_closed(control.channel)) {
            ssh_channel_send_eof(control.channel);
            ssh_channel_close(control.channel);
            if (control.started) engine.emit({{"op", "channel_closed"}, {"session", peer->id}, {"channel", control.id}});
            it = peer->controls.erase(it);
        } else ++it;
    }
    return progress;
}

bool Worker::pump(Control& control, const std::shared_ptr<Peer>& peer) {
    bool progress = false;
    if (!control.started) return false;
    if (!control.output.empty() && !(ssh_get_status(peer->session) & SSH_WRITE_PENDING)) {
        auto& front = control.output.front();
        size_t count = std::min({front.size() - control.offset, io_quantum,
                               static_cast<size_t>(ssh_channel_window_size(control.channel))});
        if (count) {
            int written = ssh_channel_write(control.channel, front.data() + control.offset, static_cast<uint32_t>(count));
            if (written < 0) control.closing = true;
            if (written > 0) {
                auto size = static_cast<size_t>(written);
                control.offset += size;
                control.queued -= size;
                if (control.offset == front.size()) { control.output.pop_front(); control.offset = 0; }
                engine.emit({{"op", "channel_written"}, {"session", peer->id}, {"channel", control.id}, {"length", size}});
                progress = true;
            }
        }
    }
    if (!control.inflight && !control.eof && !control.closing && !control.read_paused) {
        std::array<char, 16384> bytes;
        int count = ssh_channel_read_nonblocking(control.channel, bytes.data(), static_cast<uint32_t>(bytes.size()), 0);
        if (count > 0) {
            control.inflight = true;
            Id id = control.id;
            rpc(peer, {{"op", "channel_data"}, {"channel", id}, {"data", encode(bytes.data(), static_cast<size_t>(count))}},
                [peer, id](const Json& result) {
                    auto found = peer->controls.find(id);
                    if (found != peer->controls.end()) {
                        found->second->inflight = false;
                        if (!result.value("allowed", false)) found->second->closing = true;
                    }
                });
            progress = true;
        } else if (count == SSH_ERROR) control.closing = true;
        if (count <= 0 && ssh_channel_is_eof(control.channel)) {
            control.eof = true;
            engine.emit({{"op", "channel_eof"}, {"session", peer->id}, {"channel", control.id}});
        }
    }
    return progress;
}
