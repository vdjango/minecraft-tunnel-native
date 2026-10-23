// SPDX-License-Identifier: AGPL-3.0-or-later
#pragma once
#include <nlohmann/json.hpp>
#include <atomic>
#include <condition_variable>
#include <deque>
#include <filesystem>
#include <mutex>
#include <thread>
namespace bridge {
// The network thread only enqueues bounded metadata. One writer owns disk I/O.
class Journal {
    struct Row { std::string player, session, text; };
    std::filesystem::path root_;
    std::mutex mutex_;
    std::condition_variable changed_;
    std::deque<Row> queue_;
    size_t queued_bytes_=0;
    bool stopping_=false;
    const size_t max_bytes_, rotate_bytes_;
    const unsigned days_;
    std::atomic<uint64_t> dropped_{0},errors_{0};
    std::thread writer_;
    void run();
    void append(const Row&);
    void prune();
public:
    explicit Journal(std::filesystem::path root, size_t max_bytes=256*1024*1024,
                     unsigned days=7, size_t rotate_bytes=4*1024*1024);
    ~Journal();
    Journal(const Journal&)=delete;
    bool write(std::string player,std::string session,std::string event,
               nlohmann::json fields=nlohmann::json::object());
    uint64_t dropped() const { return dropped_; }
    uint64_t errors() const { return errors_; }
    static std::filesystem::path default_root();
};
Journal& journal();
void bind_log(const std::string& session,const std::string& player,const std::string& room);
void unbind_log(const std::string& session);
}
