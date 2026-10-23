#pragma once
#include "net.hpp"
#include "heartbeat.hpp"
#include <libssh/libssh.h>
#include <libssh/server.h>
#include <openssl/evp.h>
#include <nlohmann/json.hpp>
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <csignal>
#include <deque>
#include <functional>
#include <iostream>
#include <memory>
#include <mutex>
#include <thread>
#include <unordered_map>

using Json = nlohmann::json;
using Clock = std::chrono::steady_clock;
using Id = uint64_t;
constexpr size_t max_ipc_frame = 2 * 1024 * 1024;
constexpr size_t max_control_buffer = 1024 * 1024;
constexpr size_t io_quantum = 65536;

std::string encode(const char* bytes, size_t length);
std::string decode(const std::string& text);
bool read_line(std::string& result);

struct Engine;
struct Worker;
struct Message {
    ssh_message value;
    explicit Message(ssh_message message) : value(message) {}
    ~Message() { ssh_message_free(value); }
};
using Msg = std::shared_ptr<Message>;

struct Control {
    Id id;
    ssh_channel channel;
    bool started = false, inflight = false, eof = false, closing = false;
    std::deque<std::string> output;
    size_t offset = 0, queued = 0;
    Control(Id identity, ssh_channel value) : id(identity), channel(value) {}
    ~Control() { ssh_channel_free(channel); }
};
struct Peer {
    Id id;
    ssh_session session;
    std::atomic<Worker*> worker;
    bool kex = false, authenticated = false, dead = false, auth_pending = false;
    int auth_attempts = 0;
    std::string username;
    std::string disconnect_reason = "transport-closed";
    Clock::time_point created = Clock::now();
    ssh_counter_struct traffic{};
    Heartbeat heartbeat;
    std::unordered_map<Id, std::unique_ptr<Control>> controls;
    Peer(Id identity, ssh_session value, Worker* assigned)
        : id(identity), session(value), worker(assigned), heartbeat(identity * 2654435761ULL) {
        ssh_set_counters(session, nullptr, &traffic);
    }
    ~Peer() {
        controls.clear();
        ssh_disconnect(session);
        ssh_free(session);
    }
};
struct Direction {
    Ring pending;
    bool eof = false, sent_eof = false, blocked = false;
    uint64_t total = 0, reported = 0;
    Clock::time_point blocked_at;
    explicit Direction(size_t capacity) : pending(capacity) {}
};
struct Stream {
    Id id;
    std::shared_ptr<Peer> client, owner;
    ssh_channel visitor = nullptr, host = nullptr;
    Msg request;
    std::string host_name;
    std::string client_name;
    int host_port;
    bool open = false, dead = false;
    Clock::time_point created = Clock::now(), sampled = Clock::now();
    Direction up, down;
    Stream(Id identity, std::shared_ptr<Peer> from, std::shared_ptr<Peer> to, Msg message,
           std::string name, int port, size_t capacity, std::string player) : id(identity), client(std::move(from)),
           owner(std::move(to)), request(std::move(message)), host_name(std::move(name)),
           client_name(std::move(player)), host_port(port), up(capacity), down(capacity) {
        host = ssh_channel_new(owner->session);
        if (!host) throw std::runtime_error("SSH channel allocation failed");
    }
    ~Stream() {
        if (visitor) { ssh_channel_close(visitor); ssh_channel_free(visitor); }
        if (host) { ssh_channel_close(host); ssh_channel_free(host); }
    }
};
struct Pending {
    Id peer;
    Clock::time_point expires;
    std::function<void(const Json&)> callback;
};

struct Worker {
    Engine& engine;
    size_t index;
    Wakeup wake;
    std::mutex mutex;
    std::deque<std::function<void()>> commands;
    std::thread thread;
    std::unordered_map<Id, std::shared_ptr<Peer>> peers;
    std::unordered_map<Id, std::unique_ptr<Stream>> streams;
    std::unordered_map<Id, Pending> pending;
    Worker(Engine& parent, size_t number) : engine(parent), index(number) {}
    void post(std::function<void()> action);
    void start();
    void run();
    void handle(const std::shared_ptr<Peer>& peer, const Msg& message);
    void rpc(const std::shared_ptr<Peer>& peer, Json request, std::function<void(const Json&)> callback);
    void receive(Json value);
    bool pump(const std::shared_ptr<Peer>& peer);
    bool pump(Control& control, const std::shared_ptr<Peer>& peer);
    bool pump(Stream& stream);
    bool transfer(Stream& stream, Direction& direction, ssh_channel from, ssh_channel to, bool upload);
    void sample(Stream& stream);
    void remove_stream(Id id);
    void remove_peer(Id id);
};

struct Engine {
    Json config;
    std::atomic<bool> running{true};
    std::atomic<Id> sequence{1};
    std::atomic<size_t> connections{0};
    std::vector<std::unique_ptr<Worker>> workers;
    std::mutex routes_mutex;
    std::unordered_map<Id, size_t> routes;
    std::mutex output_mutex;
    std::condition_variable output_ready;
    std::deque<std::string> output;
    size_t output_bytes = 0;
    bool writer_running = true;
    std::thread reader, writer;
    Wakeup wake;
    ssh_bind binding = nullptr;
    NetSocket udp = invalid_socket;
    std::unordered_map<std::string, std::pair<int64_t,int>> stun_buckets;
    int64_t stun_second = 0;
    int stun_count = 0;
    explicit Engine(Json value) : config(std::move(value)) {}
    ~Engine() { close_socket(udp); if (binding) ssh_bind_free(binding); }
    void emit(const Json& value) {
        auto text = value.dump(-1, ' ', true) + '\n';
        std::lock_guard lock(output_mutex);
        if (output_bytes + text.size() > 8 * 1024 * 1024) {
            // Reserve a terminal notification even when ordinary control
            // output is saturated, so the supervisor can restart the service.
            output.clear();
            output.push_back("{\"op\":\"fatal\",\"reason\":\"control output limit exceeded\"}\n");
            output_bytes = output.front().size();
            output_ready.notify_one();
            stop();
            return;
        }
        output_bytes += text.size();
        output.push_back(std::move(text));
        output_ready.notify_one();
    }
    void stop() {
        running = false;
        wake.notify();
        for (auto& worker : workers) worker->wake.notify();
    }
    void route(Id id, size_t worker) { std::lock_guard lock(routes_mutex); routes[id] = worker; }
    void unroute(Id id) { std::lock_guard lock(routes_mutex); routes.erase(id); }
    size_t affinity(const std::string& user) {
        auto colon = user.find(':');
        auto key = colon == std::string::npos ? user : user.substr(colon + 1);
        uint64_t hash = 14695981039346656037ULL;
        for (unsigned char byte : key) hash = (hash ^ byte) * 1099511628211ULL;
        return static_cast<size_t>(hash % workers.size());
    }
    void stun();
    int run();
};
