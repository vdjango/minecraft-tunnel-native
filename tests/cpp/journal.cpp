// SPDX-License-Identifier: AGPL-3.0-or-later
#include "journal.hpp"
#include "../../bridge/journal.hpp"
#include "../../bridge/webrtc_diagnostics.hpp"
#include <fstream>
#include <random>
#include <stdexcept>
#include <tuple>

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
std::vector<Json> rows(const fs::path& root, bool critical=false) {
    std::vector<Json> result;
    for (const auto& item : fs::recursive_directory_iterator(root)) {
        if (item.is_symlink() || !item.is_regular_file() || item.path().extension() != ".jsonl") continue;
        if((item.path().filename().string().find(".events.")!=std::string::npos)!=critical)continue;
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
    add("incident_survives_sample_rotation_and_flush_reports_disk_state", [] {
        Temporary temporary;
        bridge::Journal journal(temporary.root,1024*1024,7,700);
        require(journal.write("player","session","endpoint_disconnected",{{"reason","transport-closed"}}),"incident rejected");
        for(unsigned i=0;i<25;++i)
            require(journal.write("player","session","sample",{{"index",i},{"padding",std::string(200,'x')}}),"sample rejected");
        const auto status=journal.snapshot();
        require(status["flushed"]==true&&status["pending_events"]==0&&status["write_errors"]==0,"flush did not finish");
        auto incident=rows(temporary.root,true);
        require(incident.size()==1&&incident[0]["reason"]=="transport-closed","samples erased incident");
        require(incident[0]["sequence"].is_number_unsigned()&&incident[0]["monotonic_ms"].is_number_integer(),"clock/order evidence missing");
        for(const auto& row:rows(temporary.root))require(row["event"]=="sample","rotation fixture did not overwrite ordinary incident");
        Temporary failed;
        std::ofstream(failed.root/"blocked")<<"file";
        bridge::Journal blocked(failed.root/"blocked");
        require(blocked.write("player","session","endpoint_disconnected"),"producer performed disk IO");
        require(blocked.snapshot()["write_errors"].get<uint64_t>()>0,"export concealed disk failure");
    });
    add("webrtc_warning_stages_and_numeric_error", [] {
        for (const auto& [message, component, reason] : std::vector<std::tuple<std::string, std::string, std::string>>{
                {"rtc::impl::IceTransport::LogCallback@99: STUN server address resolution failed", "stun", "dns-failed"},
                {"rtc::impl::IceTransport::timeout@832: ICE timeout", "ice", "timeout"},
                {"rtc::impl::DtlsTransport::run@373: Handshake timeout", "dtls", "handshake-timeout"},
                {"rtc::impl::SctpTransport::send@536: SCTP sending failed, errno=10054", "sctp", "send-failed"},
                {"rtc::impl::IceTransport::addRemoteCandidate@761: Rejected ICE candidate: private", "ice", "candidate-rejected"}}) {
            const auto fields = bridge::webrtc_warning_fields(3, message);
            require(fields["component"] == component && fields["reason"] == reason, "warning stage lost");
            require(fields["level"] == "warning" && fields["scope"] == "runtime", "warning scope/severity lost");
            require(fields["source_line"].is_number_unsigned(), "source location lost");
        }
        require(bridge::webrtc_warning_fields(2, "SCTP sending failed, errno=10054")["system_error"] == 10054,
                "numeric socket error lost");
        require(!bridge::webrtc_warning_fields(3, "errno=10054secret").contains("system_error"), "partial errno accepted");
        require(!bridge::webrtc_warning_fields(3, "errno=123456789").contains("system_error"), "unbounded errno accepted");
    });
    add("webrtc_warning_secrets_and_unknown_messages_not_persisted", [] {
        Temporary temporary;
        {
            bridge::Journal journal(temporary.root);
            for (const std::string message : {
                    "rtc::impl::IceTransport::LogCallback@72: STUN integrity check failed, password=private-secret",
                    "rtc::impl::IceTransport::addRemoteCandidate@761: Rejected ICE candidate: candidate:private-secret a=ice-pwd:private-secret",
                    "rtc::impl::IceTransport::LogCallback@72: STUN username invalid, username=private-secret",
                    "unknown@oops: Authorization: Bearer private-secret\na=ice-pwd:private-secret"})
                require(journal.write("bridge", "runtime", "webrtc_warning", bridge::webrtc_warning_fields(2, message)),
                        "warning write failed");
        }
        const auto output = rows(temporary.root);
        require(output.size() == 4, "warning rows lost");
        for (const auto& row : output) {
            require(row.dump().find("private-secret") == std::string::npos, "warning leaked ICE/authorization secret");
            require(row["level"] == "error", "error severity discarded");
        }
        require(output.back()["reason"] == "unclassified" && !output.back().contains("source_line"),
                "unknown warning was invented or copied");
        const auto huge = bridge::webrtc_warning_fields(3, std::string(100000, 'x') + "private-secret");
        require(huge.dump().size() < 256 && huge["message_bytes"] == 100014, "oversized warning not bounded");
    });
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
                require(journal.write("player", "session", "sample", {{"index", i}, {"padding", std::string(200, 'x')}}),
                        "rotation write failed");
        }
        require(fs::is_regular_file(temporary.root / "player/session.1.jsonl"), "rotated file missing");
        auto output = rows(temporary.root);
        require(output.size() == 2, "rotation did not bound files");
        bool newest = false;
        for (const auto& row : output) newest |= row["index"] == 9;
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
