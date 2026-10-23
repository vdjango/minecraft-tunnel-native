// SPDX-License-Identifier: AGPL-3.0-or-later
#include "runtime.hpp"

int Engine::run() {
    int count = config.value("workers", 1);
    size_t capacity = config.value("buffer_bytes", size_t{262144});
    if (count < 1 || count > 64 || capacity < 32768 || capacity > 4 * 1024 * 1024 ||
        config.value("max_connections", 0) < 1 || config.value("max_streams", 0) < 1)
        throw std::runtime_error("invalid native resource limits");
    binding = ssh_bind_new();
    if (!binding) throw std::runtime_error("SSH listener allocation failed");
    int no = 0, port = config.at("port").get<int>();
    std::string host = config.at("host");
    ssh_bind_options_set(binding, SSH_BIND_OPTIONS_PROCESS_CONFIG, &no);
    int key_status;
    if (config.contains("host_key_data")) {
        key_status = ssh_bind_options_set(binding, SSH_BIND_OPTIONS_IMPORT_KEY_STR,
                                         config.at("host_key_data").get_ref<const std::string&>().c_str());
        config.erase("host_key_data");
    } else {
        key_status = ssh_bind_options_set(binding, SSH_BIND_OPTIONS_HOSTKEY,
                                         config.at("host_key").get_ref<const std::string&>().c_str());
    }
    if (ssh_bind_options_set(binding, SSH_BIND_OPTIONS_BINDADDR, host.c_str()) != SSH_OK ||
        ssh_bind_options_set(binding, SSH_BIND_OPTIONS_BINDPORT, &port) != SSH_OK ||
        key_status != SSH_OK || ssh_bind_listen(binding) != SSH_OK)
        throw std::runtime_error("SSH listener or host key unavailable");
    ssh_bind_set_blocking(binding, 0);
    auto listener = ssh_bind_get_fd(binding);
    nonblocking(listener);
    sockaddr_storage address{};
    SockLen length = sizeof(address);
    getsockname(listener, reinterpret_cast<sockaddr*>(&address), &length);
    port = ntohs(address.ss_family == AF_INET ? reinterpret_cast<sockaddr_in*>(&address)->sin_port :
                 reinterpret_cast<sockaddr_in6*>(&address)->sin6_port);
    udp = socket(address.ss_family, SOCK_DGRAM, 0);
    if (udp != invalid_socket && !bind(udp, reinterpret_cast<sockaddr*>(&address), length)) nonblocking(udp);
    else { close_socket(udp); udp = invalid_socket; std::cerr << "stun_unavailable relay_available=true\n"; }
    for (int i = 0; i < count; ++i) workers.push_back(std::make_unique<Worker>(*this, static_cast<size_t>(i)));
    writer = std::thread([this] {
        while (true) {
            std::unique_lock lock(output_mutex);
            output_ready.wait(lock, [this] { return !output.empty() || !writer_running; });
            if (output.empty()) break;
            auto line = std::move(output.front());
            output_bytes -= line.size(); output.pop_front();
            lock.unlock();
            std::cout << line << std::flush;
            if (!std::cout) { stop(); break; }
        }
    });
    for (auto& worker : workers) worker->start();
    reader = std::thread([this] {
        try {
            std::string line;
            while (running && read_line(line)) {
                auto value = Json::parse(line);
                std::string op = value.value("op", "");
                if (op == "shutdown") break;
                size_t worker = 0;
                if (op == "reply") worker = value.at("worker").get<size_t>();
                else {
                    std::lock_guard lock(routes_mutex);
                    auto found = routes.find(value.value("session", Id{0}));
                    if (found == routes.end()) continue;
                    worker = found->second;
                }
                if (worker >= workers.size()) throw std::runtime_error("invalid worker route");
                workers[worker]->post([this, worker, value=std::move(value)]() mutable {
                    workers[worker]->receive(std::move(value));
                });
            }
        } catch (const std::exception&) { std::cerr << "control_input_closed\n"; }
        stop();
    });
    emit({{"op", "ready"}, {"port", port}, {"workers", count}, {"stun", udp != invalid_socket},
          {"engine", "libssh-0.12.2"}, {"protocol", 1}});
    size_t assigned = 0;
    while (running) {
        std::array<PollFd, 3> fds{{{listener, POLLIN, 0}, {wake.fd(), POLLIN, 0}, {udp, POLLIN, 0}}};
        net_poll(fds.data(), udp == invalid_socket ? 2 : 3, 100);
        wake.drain();
        if (udp != invalid_socket && fds[2].revents) stun();
        if (!fds[0].revents) continue;
        NetSocket fd = accept(listener, nullptr, nullptr);
        if (fd == invalid_socket) continue;
        if (connections >= config.at("max_connections").get<size_t>()) { close_socket(fd); continue; }
        tune_tcp(fd);
        auto session = ssh_new();
        if (!session || ssh_bind_accept_fd(binding, session, fd) != SSH_OK) {
            if (session) ssh_free(session);
            close_socket(fd); continue;
        }
        ssh_set_blocking(session, 0);
        nonblocking(fd);
        // Explicitly retain standard, uncompressed SSH negotiation. Optional
        // game compression is per stream at endpoints, never shared with auth.
        ssh_options_set(session, SSH_OPTIONS_COMPRESSION_C_S, "none");
        ssh_options_set(session, SSH_OPTIONS_COMPRESSION_S_C, "none");
        auto& worker = workers[assigned++ % workers.size()];
        Id id = sequence++;
        auto peer = std::make_shared<Peer>(id, session, worker.get());
        ++connections;
        route(id, worker->index);
        worker->post([peer] { auto* target = peer->worker.load(); target->peers.emplace(peer->id, peer); });
    }
    for (auto& worker : workers) { worker->wake.notify(); if (worker->thread.joinable()) worker->thread.join(); }
    if (reader.joinable()) reader.join();
    { std::lock_guard lock(output_mutex); writer_running = false; }
    output_ready.notify_one();
    if (writer.joinable()) writer.join();
    return 0;
}
