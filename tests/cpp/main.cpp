// SPDX-License-Identifier: AGPL-3.0-or-later
#include "../../core/runtime.hpp"
#include "journal.hpp"
#include "../../bridge/progress.hpp"
#include "../../bridge/route_score.hpp"
#include "../../include/buggcraft/minecraft_handshake.hpp"
#include <sstream>
#include <random>
#include <functional>
#include <limits>
void add_codec_tests(std::vector<std::pair<std::string,std::function<void()>>>&);
void add_heartbeat_tests(std::vector<std::pair<std::string,std::function<void()>>>&);
void add_game_wire_tests(std::vector<std::pair<std::string,std::function<void()>>>&);
namespace {
void require(bool condition,const char* message){if(!condition)throw std::runtime_error(message);}
template<class F>void rejected(F action){bool caught=false;try{action();}catch(const std::exception&){caught=true;}require(caught,"invalid input was accepted");}
std::string random_bytes(size_t size){std::mt19937 generator(12345);std::string out(size,'\0');for(auto& byte:out)byte=static_cast<char>(generator());return out;}
struct Input {std::streambuf* previous;explicit Input(std::istream& source):previous(std::cin.rdbuf(source.rdbuf())){std::cin.clear();}~Input(){std::cin.rdbuf(previous);std::cin.clear();}};
}
int main(){
#ifdef _WIN32
    WSADATA data;if(WSAStartup(MAKEWORD(2,2),&data))return 1;
#endif
    std::vector<std::pair<std::string,std::function<void()>>> cases;
    add_journal_tests(cases);
    add_codec_tests(cases);
    add_heartbeat_tests(cases);
    add_game_wire_tests(cases);
    auto add=[&](std::string name,std::function<void()> test){cases.emplace_back(std::move(name),std::move(test));};
    add("minecraft_handshake_split_and_reject",[]{
        const std::string valid{char(15),char(0),char(47),char(9),'l','o','c','a','l','h','o','s','t',char(99),char(221),char(2)};
        for(size_t i=0;i<valid.size();++i)require(buggcraft::minecraft_handshake(std::string_view(valid).substr(0,i))==buggcraft::HandshakeState::partial,"split handshake rejected");
        require(buggcraft::minecraft_handshake(valid)==buggcraft::HandshakeState::valid,"Minecraft handshake rejected");
        auto non_handshake=valid;non_handshake[1]='\x01';require(buggcraft::minecraft_handshake(non_handshake)==buggcraft::HandshakeState::invalid,"non-handshake packet accepted");
        auto invalid=valid;invalid.back()='\x03';require(buggcraft::minecraft_handshake(invalid)==buggcraft::HandshakeState::invalid,"invalid next state accepted");
    });
    add("route_bandwidth_beats_small_latency_advantage",[]{
        bridge::RouteSample full{8,0,.98,1,true,true},free{30,0,.1,40,true,true};
        require(bridge::route_score(free)<bridge::route_score(full),"saturated node preferred");});
    add("route_lower_latency_wins_at_equal_capacity",[]{bridge::RouteSample a{10,0,.2,20,true,true},b{150,0,.2,20,true,true};require(bridge::route_score(a)<bridge::route_score(b),"latency ignored");});
    add("route_jitter_penalty",[]{bridge::RouteSample a{25,0,.2,20,true,true},b{25,100,.2,20,true,true};require(bridge::route_score(a)<bridge::route_score(b),"jitter ignored");});
    add("route_reject_unreachable_full_and_maintenance",[]{bridge::RouteSample a{1,0,0,10,false,true};require(!std::isfinite(bridge::route_score(a)),"dead accepted");a.reachable=true;a.remaining=0;require(!std::isfinite(bridge::route_score(a)),"full accepted");a.remaining=10;a.accepting=false;require(!std::isfinite(bridge::route_score(a)),"maintenance accepted");});
    add("route_unknown_capacity_is_not_free_capacity",[]{bridge::RouteSample a{20,0,-1,-1,true,true},b{20,0,.1,30,true,true};require(bridge::route_score(b)<bridge::route_score(a),"unknown treated as free");});
    add("route_hysteresis_rejects_small_improvement",[]{require(!bridge::route_improves(.4,.38,false),"flapping");require(bridge::route_improves(.6,.3,false),"large improvement ignored");});
    add("route_cooldown_and_emergency_failover",[]{require(!bridge::route_improves(.6,.2,true),"cooldown ignored");require(bridge::route_improves(INFINITY,.2,true),"dead node pinned by cooldown");});
    add("route_nan_latency_rejected",[]{bridge::RouteSample a{NAN,0,.1,10,true,true};require(!std::isfinite(bridge::route_score(a)),"NaN accepted");});
    add("progress_idle_is_not_a_stall",[]{
        bridge::Progress progress;const auto now=bridge::Progress::Clock::now();
        require(!progress.observe(false,false,now+std::chrono::hours(1)).started,"idle reported stalled");
        require(!progress.observe(false,true,now+std::chrono::hours(1)).started,"new data inherited idle time");
        require(!progress.observe(false,true,now+std::chrono::hours(1)+std::chrono::milliseconds(249)).started,"stall threshold too early");
        require(progress.observe(false,true,now+std::chrono::hours(1)+std::chrono::milliseconds(250)).started,"pending data stall missing");
    });
    add("progress_direction_isolation_recovery_and_still_pending_close",[]{
        bridge::Progress upload,download;const auto now=bridge::Progress::Clock::now();
        upload.observe(false,true,now);download.observe(false,true,now);
        for(int i=1;i<=5;++i)download.observe(true,true,now+std::chrono::milliseconds(i*50));
        require(upload.observe(false,true,now+std::chrono::milliseconds(250)).started,"download hid upload stall");
        require(upload.stalled_ms(now+std::chrono::milliseconds(350))>=350,"active stall duration lost");
        const auto recovered=upload.observe(true,true,now+std::chrono::milliseconds(400));
        require(recovered.ended&&recovered.duration_ms>=400,"stall recovery duration missing");
        require(!upload.observe(true,true,now+std::chrono::milliseconds(450)).ended,"recovery emitted twice");
        require(!download.stalled_ms(now+std::chrono::milliseconds(400)),"flowing download stalled");
    });
    for(size_t capacity:{1u,7u,64u,65536u,131100u})add("ring_wrap_"+std::to_string(capacity),[capacity]{
        Ring ring(capacity);std::deque<char> model;std::mt19937 generator(93);
        for(unsigned i=0;i<12000;++i){if(generator()%2&&ring.free()){auto count=std::min<size_t>(ring.writable(),1+generator()%31);for(size_t n=0;n<count;++n){char byte=static_cast<char>(generator());ring.write_ptr()[n]=byte;model.push_back(byte);}ring.produced(count);}else if(ring.size()){auto count=std::min<size_t>(ring.readable(),1+generator()%29);for(size_t n=0;n<count;++n){require(ring.read_ptr()[n]==model.front(),"ring data mismatch");model.pop_front();}ring.consumed(count);}require(ring.size()==model.size()&&ring.free()+ring.size()==capacity,"ring accounting mismatch");}
    });
    add("ring_zero_capacity",[]{rejected([]{Ring ring(0);});});
    add("ring_full_wrap_and_empty_reuse",[]{
        Ring ring(7);const auto initial=ring.write_ptr();
        ring.produced(7);require(!ring.free()&&ring.write_ptr()==initial,"full tail did not wrap");
        ring.consumed(3);ring.produced(3);ring.consumed(7);
        require(!ring.size()&&ring.writable()==4,"empty ring lost wrap position");
        ring.produced(0);ring.consumed(0);
        ring.produced(4);ring.consumed(4);
        require(ring.write_ptr()==initial&&ring.read_ptr()==initial&&ring.writable()==7,"wrapped reuse failed");
    });
    add("ring_overflow",[]{Ring ring(8);rejected([&]{ring.produced(9);});require(ring.size()==0,"overflow altered ring");});
    add("ring_underflow",[]{Ring ring(8);rejected([&]{ring.consumed(1);});require(ring.size()==0,"underflow altered ring");});
    for(size_t size:{0u,1u,2u,3u,7u,31u,65536u})add("ipc_base64_"+std::to_string(size),[size]{auto bytes=random_bytes(size);require(decode(encode(bytes.data(),bytes.size()))==bytes,"base64 roundtrip mismatch");});
    for(const std::string invalid:{"x","===!","a===","!!!!","A===","Zh==","Zm9=","Zg=A","Zg==Zg==","Zg\n="})add("ipc_invalid_"+invalid,[invalid]{rejected([&]{decode(invalid);});});
    add("ipc_oversized_base64",[]{rejected([]{decode(std::string(max_ipc_frame+4,'A'));});});
    add("ipc_partial_line",[]{std::istringstream source("partial");Input input(source);std::string line;require(!read_line(line),"unterminated input accepted");});
    add("ipc_multiple_lines",[]{std::istringstream source("one\ntwo\n");Input input(source);std::string line;require(read_line(line)&&line=="one","first input mismatch");require(read_line(line)&&line=="two","second input mismatch");require(!read_line(line),"EOF mismatch");});
    add("ipc_maximum_frame",[]{std::istringstream source(std::string(max_ipc_frame,'a')+"\n");Input input(source);std::string line;require(read_line(line)&&line.size()==max_ipc_frame,"maximum frame rejected");});
    add("ipc_unterminated_overflow",[]{std::istringstream source(std::string(max_ipc_frame+1,'a'));Input input(source);std::string line;rejected([&]{read_line(line);});});
    add("wakeup_nonblocking_and_drain",[]{Wakeup wake;for(int i=0;i<100;++i)wake.notify();PollFd fd{wake.fd(),POLLIN,0};require(net_poll(&fd,1,100)>0,"worker failed to wake");wake.drain();fd.revents=0;require(net_poll(&fd,1,0)==0,"wake drain incomplete");});
    unsigned failures=0;for(auto& [name,test]:cases){try{test();std::cout<<"PASS "<<name<<'\n';}catch(const std::exception& error){++failures;std::cerr<<"FAIL "<<name<<": "<<error.what()<<'\n';}}
    std::cout<<cases.size()<<" native cases; failures="<<failures<<'\n';
#ifdef _WIN32
    WSACleanup();
#endif
    return failures?1:0;
}
