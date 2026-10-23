// SPDX-License-Identifier: AGPL-3.0-or-later
#include "journal.hpp"
#include "../../bridge/journal.hpp"
#include <fstream>
#include <random>
#include <stdexcept>

namespace {
using Json = nlohmann::json;
namespace fs = std::filesystem;
void require(bool condition, const char* message) {
    if (!condition) throw std::runtime_error(message);
}
struct Temporary {
    fs::path root;
    Temporary() {
        std::random_device random;
        root = fs::temp_directory_path() /
               ("buggcraft-journal-test-" + std::to_string(random()) + "-" + std::to_string(random()));
        fs::create_directory(root);
    }
    ~Temporary() {
        std::error_code error;
        fs::remove_all(root, error);
    }
};
std::vector<Json> rows(const fs::path& root) {
    std::vector<Json> result;
    for (const auto& item : fs::recursive_directory_iterator(root)) {
        if (item.is_symlink() || !item.is_regular_file() || item.path().extension() != ".jsonl") continue;
        std::ifstream input(item.path());
        std::string line;
        while (std::getline(input, line)) result.push_back(Json::parse(line));
    }
    return result;
}
}

void add_journal_tests(std::vector<std::pair<std::string, std::function<void()>>>& cases) {
    auto add = [&](std::string name, std::function<void()> test) {
        cases.emplace_back("journal_" + std::move(name), std::move(test));
    };
    add("player_session_isolation_and_shutdown_flush", [] {
        Temporary temporary;
        {
            bridge::Journal journal(temporary.root);
            require(journal.write("account-one", "session-one", "connected"), "first write failed");
            require(journal.write("account-one", "session-two", "reconnected"), "second write failed");
            require(journal.write("anonymous-two", "session-three", "connected"), "third write failed");
        }
        require(fs::is_regular_file(temporary.root / "account-one/session-one.jsonl"), "first player missing");
        require(fs::is_regular_file(temporary.root / "account-one/session-two.jsonl"), "reconnect lost identity");
        require(fs::is_regular_file(temporary.root / "anonymous-two/session-three.jsonl"), "second player missing");
        require(rows(temporary.root).size() == 3, "shutdown lost rows");
    });
    add("recursive_secret_redaction_and_identity_override", [] {
        Temporary temporary;
        {
            bridge::Journal journal(temporary.root);
            require(journal.write("account-one", "session-one", "connected", {
                {"token", "private"}, {"session", "../evil"}, {"player", "evil"}, {"event", "evil"},
                {"details", {{"Authorization", "private"}, {"refresh_token", "private"},
                             {"items", Json::array({{{"SDP", "private"}, {"sent_bytes", 123}}})}}}
            }), "redacted row rejected");
        }
        auto output = rows(temporary.root);
        require(output.size() == 1, "missing redacted row");
        const auto& row = output.front();
        require(row.dump().find("private") == std::string::npos, "nested credentials retained");
        require(row["player"] == "account-one" && row["session"] == "session-one" &&
                row["event"] == "connected" && row["at"].is_number(), "caller replaced identity");
        require(row["details"]["items"][0]["sent_bytes"] == 123, "safe counters removed");
    });
    add("invalid_paths_and_oversized_rows", [] {
        Temporary temporary;
        {
            bridge::Journal journal(temporary.root);
            require(!journal.write("../escape", "session", "connected"), "path traversal accepted");
            require(!journal.write("player", "../escape", "connected"), "session traversal accepted");
            require(!journal.write("player", "session", "bad\nname"), "invalid event accepted");
            require(!journal.write("player", "session", "connected", Json::array()), "array accepted");
            require(!journal.write("player", "session", "sample", {{"large", std::string(8192, 'x')}}),
                    "oversized row accepted");
            require(journal.dropped() == 5, "dropped rows not counted");
        }
        require(rows(temporary.root).empty(), "invalid rows reached disk");
    });
    add("rotation_preserves_newest_rows", [] {
        Temporary temporary;
        {
            bridge::Journal journal(temporary.root, 1024 * 1024, 7, 512);
            for (unsigned i = 0; i < 10; ++i)
                require(journal.write("player", "session", "sample", {{"sequence", i}, {"padding", std::string(200, 'x')}}),
                        "rotation write failed");
        }
        require(fs::is_regular_file(temporary.root / "player/session.1.jsonl"), "rotated file missing");
        auto output = rows(temporary.root);
        require(output.size() == 2, "rotation did not bound files");
        bool newest = false;
        for (const auto& row : output) newest |= row["sequence"] == 9;
        require(newest, "rotation discarded newest record");
    });
    add("retention_age_and_total_bytes", [] {
        Temporary temporary;
        fs::create_directory(temporary.root / "player");
        const auto old = temporary.root / "player/old.jsonl";
        std::ofstream(old) << "{\"old\":true}\n";
        fs::last_write_time(old, fs::file_time_type::clock::now() - std::chrono::hours(48));
        {
            bridge::Journal journal(temporary.root, 1024, 1);
            require(journal.write("player", "recent", "connected"), "recent row failed");
        }
        require(!fs::exists(old), "expired file survived");
        require(fs::exists(temporary.root / "player/recent.jsonl"), "recent file removed");
        {
            bridge::Journal journal(temporary.root, 32, 1);
            require(journal.write("player", "oversize", "sample"), "budget row rejected");
        }
        require(rows(temporary.root).empty(), "total byte cap not enforced at shutdown");
    });
    add("concurrent_producers", [] {
        Temporary temporary;
        {
            bridge::Journal journal(temporary.root);
            std::atomic<unsigned> accepted{0};
            std::vector<std::thread> writers;
            for (unsigned player = 0; player < 4; ++player) writers.emplace_back([&, player] {
                for (unsigned i = 0; i < 32; ++i)
                    if (journal.write("player-" + std::to_string(player), "session", "sample", {{"sequence", i}}))
                        ++accepted;
            });
            for (auto& writer : writers) writer.join();
            require(accepted == 128 && journal.dropped() == 0, "concurrent rows dropped");
        }
        require(rows(temporary.root).size() == 128, "concurrent journal lost records");
    });
    add("disk_failure_does_not_throw_on_producer", [] {
        Temporary temporary;
        std::ofstream(temporary.root / "blocked") << "regular file";
        bridge::Journal journal(temporary.root / "blocked");
        require(journal.write("player", "session", "sample"), "producer blocked on disk failure");
        const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(2);
        while (!journal.errors() && std::chrono::steady_clock::now() < deadline)
            std::this_thread::yield();
        require(journal.errors() != 0, "disk failure not counted");
    });
#ifndef _WIN32
    add("reject_symlink_directory_and_file", [] {
        Temporary temporary;
        const auto external = temporary.root / "external";
        fs::create_directory(external);
        const auto root = temporary.root / "logs";
        fs::create_directory_symlink(external, root);
        { bridge::Journal journal(root); journal.write("player", "session", "sample"); }
        require(fs::is_empty(external), "journal followed root symlink");
        fs::remove(root);
        fs::create_directories(root / "player");
        std::ofstream(external / "target") << "untouched";
        fs::create_symlink(external / "target", root / "player/session.jsonl");
        { bridge::Journal journal(root); journal.write("player", "session", "sample"); }
        std::ifstream input(external / "target");
        std::string value; input >> value;
        require(value == "untouched", "journal followed file symlink");
    });
#endif
}
