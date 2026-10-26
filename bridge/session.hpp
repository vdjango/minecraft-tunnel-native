// SPDX-License-Identifier: AGPL-3.0-or-later
#pragma once
#include "ssh.hpp"
#include "codec.hpp"
#include "journal.hpp"
#include "progress.hpp"
#include "modpack.hpp"
#include "game_chat.hpp"
#include "peer_heartbeat.hpp"
#include <rtc/rtc.hpp>
#include <atomic>
#include <deque>
#include <future>
#include <mutex>
#include <thread>
#include <condition_variable>
#include <unordered_map>
namespace bridge {
struct Stream {
    std::string trace=random_id(9),player,session;
    NetSocket socket = invalid_socket;
    ssh_channel channel = nullptr;
    std::shared_ptr<rtc::DataChannel> data;
    std::weak_ptr<rtc::PeerConnection> peer;
    // Keep a full WAN bandwidth-delay product in flight on slower routes.
    Ring to_socket{1048576}, to_wire{1048576};
    std::unique_ptr<Codec> codec;
    std::string encoded_block, decoded_block;
    bool connecting=false, local_eof=false, remote_eof=false;
    bool sent_eof=false, socket_eof=false, dead=false;
    bool opening=false;
    bool handshake_validated=false;
    int relay_port=25565;
    size_t inflight=0;
    uint64_t sent=0, received=0, sampled_sent=0,sampled_received=0;
    Clock::time_point sampled=Clock::now();
    Progress upload_progress, download_progress;
    double max_stall_ms=0;
    Clock::time_point created=Clock::now();
    ~Stream();
};
class Session:public std::enable_shared_from_this<Session> {
    Profile profile_;
    std::string policy_, compression_;
    int game_port_;
    Json settings_;
    std::string player_,chat_name_;
    bool game_chat_enabled_=false;
    std::string game_chat_name_;
    std::string game_chat_status_;
    std::unique_ptr<GameChat> game_chat_;
    std::vector<Profile> alternatives_;
    std::vector<Profile> stun_profiles_;
    size_t route_index_=0;
    std::condition_variable changed_;
    Json room_info_=Json::object();
    int requested_listen_port_=0;
    bool announce_=true;
    std::shared_ptr<ModTransfers> mods_;
    struct GameProbe {int port=0;bool available=false;Json status;};
    std::future<GameProbe> game_probe_;
    Json game_status_;
    Json control_rtt_ms_;
    bool game_available_=false;
    Clock::time_point next_game_probe_=Clock::now();
    std::unique_ptr<Ssh> ssh_;
    NetSocket listener_=invalid_socket;
    std::thread thread_;
    Wakeup wake_;
    mutable std::mutex mutex_;
    std::deque<std::function<void()>> commands_;
    std::vector<std::shared_ptr<Stream>> streams_;
    std::unordered_map<std::string,std::shared_ptr<rtc::PeerConnection>> peers_;
    struct PeerWatch {
        std::weak_ptr<rtc::PeerConnection> peer;
        std::shared_ptr<rtc::DataChannel> channel;
        PeerHeartbeat heartbeat;
        std::string player;
        bool dead=false, legacy_logged=false;
        Clock::time_point next_sample=Clock::now()+std::chrono::seconds(30);
    };
    std::vector<std::shared_ptr<PeerWatch>> probes_;
    std::shared_ptr<rtc::PeerConnection> client_peer_;
    std::string peer_attempt_;
    bool peer_attempts_supported_=false;
    ssh_channel signal_=nullptr, chat_=nullptr;
    unsigned chat_stage_=0;
    Clock::time_point next_chat_=Clock::now();
    struct ChatRequest {std::shared_ptr<std::promise<Json>> result;Clock::time_point deadline;};
    std::unordered_map<std::string,ChatRequest> chat_pending_;
    std::string signal_input_,signal_output_,chat_input_;
    std::deque<Json> chat_events_;
    std::string chat_output_;
    std::atomic<bool> running_{true}, online_{false}, direct_{false}, peer_started_{false};
    std::atomic<int> listen_port_{0};
    std::string state_="connecting",reason_;
    std::string stop_reason_="local-close";
    bool transport_failed_=false;
    std::promise<Json> ready_;
    bool ready_sent_=false,peer_available_=false,zstd_=false;
    int raw_forward_=0,zstd_forward_=0,mods_forward_=0;
    Clock::time_point peer_deadline_;
    Clock::time_point peer_retry_{};
    bool peer_negotiating_=false;
    unsigned peer_retries_=0;
    Clock::time_point next_presence_=Clock::now();
    Clock::time_point next_announce_=Clock::now();
    struct ControlRequest {
        Json value;
        std::shared_ptr<std::promise<Json>> result;
        ssh_channel channel=nullptr;
        unsigned stage=0;
        size_t offset=0;
        std::string output,input;
        bool presence=false,background=false,budget_waited=false;
        Clock::time_point started=Clock::now();
        Clock::time_point deadline=Clock::now()+std::chrono::seconds(14);
    };
    std::deque<ControlRequest> requests_;
    std::deque<Clock::time_point> control_calls_;
    bool control_ready(bool background);
    void run();
    void setup();
    void setup_with_fallback();
    void next_route();
    void tick();
    void start_peer();
    void start_client_peer();
    void advance_peers();
    void watch_peer(const std::shared_ptr<rtc::PeerConnection>&,const std::shared_ptr<rtc::DataChannel>&,std::string player);
    void fail_peer(const std::shared_ptr<rtc::PeerConnection>&,const std::string& reason,const std::string& player={});
    void receive_signal(const Json&);
    std::shared_ptr<rtc::PeerConnection> create_peer(const std::string& key,bool client,std::string player={},std::string attempt={});
    void add_data(const std::shared_ptr<rtc::DataChannel>&,NetSocket socket=invalid_socket,std::string player={},std::shared_ptr<rtc::PeerConnection> peer={});
    void accept_game();
    void accept_forward();
    void pump(Stream&);
    void sample_stream(Stream&, bool upload_moved, bool download_moved);
    void read_controls();
    void advance_chat();
    void start_game_chat();
    void publish_game_chat_status(std::string status);
    void advance_requests();
    void advance_presence();
    void flush(ssh_channel channel,std::string& value);
    void signal(Json value);
    void become_ready();
    void cleanup();
    void monitor();
    void event(Json value);
public:
    const std::string identity=random_id(), role, secret, room;
    Session(Profile profile,std::string role,std::string secret,std::string policy,int game_port,Json settings,std::string compression,
            std::vector<Profile> alternatives={},int listen_port=0,bool announce=true,std::string game_directory={},std::string chat_name={},bool game_chat_enabled=false,std::string game_chat_name={});
    ~Session();
    Json start();
    void stop();
    bool post(std::function<void()>);
    Json status() const;
    Json request(Json value);
    Json chat(Json value);
    Json events();
    Json retarget(int port,Json game);
    Json configure(Json settings);
    Json mods(const std::string& method,const Json& params);
};
void copy_into(Ring& ring,std::string_view data);
NetSocket connect_game(int port,bool& connecting);
void half_close(NetSocket socket);
void validate_sdp(const std::string& sdp);
Json candidate_counts(const std::string& sdp);
}
