// SPDX-License-Identifier: AGPL-3.0-or-later
#include "../../core/runtime.hpp"
#include "../../bridge/codec.hpp"
#include "journal.hpp"
#include "../../bridge/progress.hpp"
#include "../../bridge/route_score.hpp"
#include <sstream>
#include <random>
#include <functional>
#include <limits>
void add_heartbeat_tests(std::vector<std::pair<std::string,std::function<void()>>>&);
void add_game_wire_tests(std::vector<std::pair<std::string,std::function<void()>>>&);
namespace {
void require(bool condition,const char* message){if(!condition)throw std::runtime_error(message);}
template<class F>void rejected(F action){bool caught=false;try{action();}catch(const std::exception&){caught=true;}require(caught,"invalid input was accepted");}
std::string vuint(size_t value){std::string out;do{auto part=value&127;value>>=7;out+=static_cast<char>(part|(value?128:0));}while(value);return out;}
std::string random_bytes(size_t size){std::mt19937 generator(12345);std::string out(size,'\0');for(auto& byte:out)byte=static_cast<char>(generator());return out;}
std::string decode_frames(bridge::Codec& decoder,std::string_view wire,size_t fragment=65536){
    std::string result,decoded;while(!wire.empty()){size_t count=std::min(fragment,wire.size());decoder.feed(wire.substr(0,count));wire.remove_prefix(count);while(decoder.next(decoded))result+=decoded;}return result;
}
struct Input {std::streambuf* previous;explicit Input(std::istream& source):previous(std::cin.rdbuf(source.rdbuf())){std::cin.clear();}~Input(){std::cin.rdbuf(previous);std::cin.clear();}};
}
int main(){
#ifdef _WIN32
    WSADATA data;if(WSAStartup(MAKEWORD(2,2),&data))return 1;
#endif
    std::vector<std::pair<std::string,std::function<void()>>> cases;
    add_journal_tests(cases);
    add_heartbeat_tests(cases);
    add_game_wire_tests(cases);
    auto add=[&](std::string name,std::function<void()> test){cases.emplace_back(std::move(name),std::move(test));};
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
    for(size_t capacity:{1u,7u,64u,65536u})add("ring_wrap_"+std::to_string(capacity),[capacity]{
        Ring ring(capacity);std::deque<char> model;std::mt19937 generator(93);
        for(unsigned i=0;i<12000;++i){if(generator()%2&&ring.free()){auto count=std::min<size_t>(ring.writable(),1+generator()%31);for(size_t n=0;n<count;++n){char byte=static_cast<char>(generator());ring.write_ptr()[n]=byte;model.push_back(byte);}ring.produced(count);}else if(ring.size()){auto count=std::min<size_t>(ring.readable(),1+generator()%29);for(size_t n=0;n<count;++n){require(ring.read_ptr()[n]==model.front(),"ring data mismatch");model.pop_front();}ring.consumed(count);}require(ring.size()==model.size()&&ring.free()+ring.size()==capacity,"ring accounting mismatch");}
    });
    add("ring_zero_capacity",[]{rejected([]{Ring ring(0);});});
    add("ring_overflow",[]{Ring ring(8);rejected([&]{ring.produced(9);});require(ring.size()==0,"overflow altered ring");});
    add("ring_underflow",[]{Ring ring(8);rejected([&]{ring.consumed(1);});require(ring.size()==0,"underflow altered ring");});
    for(size_t size:{0u,1u,2u,3u,7u,31u,65536u})add("ipc_base64_"+std::to_string(size),[size]{auto bytes=random_bytes(size);require(decode(encode(bytes.data(),bytes.size()))==bytes,"base64 roundtrip mismatch");});
    for(const std::string invalid:{"x","===!","a===","!!!!","A===","Zh==","Zm9=","Zg=A","Zg==Zg==","Zg\n="})add("ipc_invalid_"+invalid,[invalid]{rejected([&]{decode(invalid);});});
    add("ipc_oversized_base64",[]{rejected([]{decode(std::string(max_ipc_frame+4,'A'));});});
    add("ipc_partial_line",[]{std::istringstream source("partial");Input input(source);std::string line;require(!read_line(line),"unterminated input accepted");});
    add("ipc_multiple_lines",[]{std::istringstream source("one\ntwo\n");Input input(source);std::string line;require(read_line(line)&&line=="one","first input mismatch");require(read_line(line)&&line=="two","second input mismatch");require(!read_line(line),"EOF mismatch");});
    add("ipc_maximum_frame",[]{std::istringstream source(std::string(max_ipc_frame,'a')+"\n");Input input(source);std::string line;require(read_line(line)&&line.size()==max_ipc_frame,"maximum frame rejected");});
    add("ipc_unterminated_overflow",[]{std::istringstream source(std::string(max_ipc_frame+1,'a'));Input input(source);std::string line;rejected([&]{read_line(line);});});
    for(size_t size:{1u,255u,256u,1024u,65535u,65536u})for(bool random:{false,true})add("codec_roundtrip_"+std::to_string(size)+(random?"_random":"_repeat"),[size,random]{
        bridge::Codec encoder,decoder;auto payload=random?random_bytes(size):std::string(size,'x');auto result=decode_frames(decoder,"BCZ1"+encoder.encode(payload),17);require(result==payload,"codec bytes mismatch");require(decoder.complete(),"codec left trailing bytes");if(size>=256&&!random)require(encoder.wire_bytes<encoder.raw_bytes,"compressible data grew");
    });
    add("codec_high_expansion_coalesced_frames",[]{bridge::Codec encoder,decoder;std::string wire="BCZ1",expected;for(int i=0;i<64;++i){auto block=std::string(65536,static_cast<char>(i));wire+=encoder.encode(block);expected+=block;}require(decode_frames(decoder,wire)==expected,"coalesced compressed frames mismatch");});
    add("codec_single_byte_fragmentation",[]{bridge::Codec encoder,decoder;auto payload=std::string(65536,'z');require(decode_frames(decoder,"BCZ1"+encoder.encode(payload),1)==payload,"fragmented frame mismatch");});
    add("codec_empty_encode",[]{bridge::Codec encoder;rejected([&]{encoder.encode("");});});
    add("codec_oversized_encode",[]{bridge::Codec encoder;rejected([&]{encoder.encode(std::string(65537,'x'));});});
    add("codec_bad_magic",[]{bridge::Codec decoder;rejected([&]{decode_frames(decoder,"BAD!");});});
    add("codec_truncated_magic",[]{bridge::Codec decoder;decode_frames(decoder,"BCZ");require(!decoder.complete(),"truncated magic accepted");});
    add("codec_truncated_payload",[]{bridge::Codec decoder;decode_frames(decoder,"BCZ1"+vuint(200)+"x");require(!decoder.complete(),"truncated frame accepted");});
    add("codec_overlong_varint",[]{bridge::Codec decoder;rejected([&]{decode_frames(decoder,std::string("BCZ1")+"\x80\x80\x80");});});
    add("codec_oversized_payload",[]{bridge::Codec decoder;rejected([&]{decode_frames(decoder,"BCZ1"+vuint(65537*2));});});
    add("codec_oversized_output",[]{bridge::Codec decoder;rejected([&]{decode_frames(decoder,"BCZ1"+vuint(3)+vuint(65537));});});
    add("codec_corrupt_zstd",[]{bridge::Codec decoder;rejected([&]{decode_frames(decoder,"BCZ1"+vuint(7)+vuint(20)+"bad");});});
    add("codec_receive_budget",[]{bridge::Codec decoder;rejected([&]{decoder.feed(std::string(131101,'x'));});});
    add("codec_raw_output_quantum",[]{bridge::Codec decoder;decoder.feed(std::string("BCZ1\0",5)+std::string(100000,'r'));std::string output,all;while(decoder.next(output)){require(output.size()<=65536,"raw output exceeded one socket quantum");all+=output;}require(all==std::string(100000,'r'),"bounded raw output lost bytes");require(decoder.complete(),"raw output left pending bytes");});
    add("codec_passthrough_marker_fragmentation",[]{bridge::Codec decoder;std::string wire="BCZ1";wire+='\0';wire+="raw bytes";require(decode_frames(decoder,wire,1)=="raw bytes","raw marker mismatch");require(decoder.complete(),"raw marker incomplete");});
    add("codec_small_packet_passthrough",[]{bridge::Codec encoder,decoder;std::string wire="BCZ1",expected;for(int i=0;i<300;++i){wire+=encoder.encode("small");expected+="small";}require(decode_frames(decoder,wire,11)==expected,"small packets changed");require(encoder.wire_bytes<encoder.raw_bytes+100,"small packets kept paying headers");});
    add("codec_random_stream_passthrough",[]{bridge::Codec encoder,decoder;std::string wire="BCZ1",expected;for(int i=0;i<20;++i){auto block=random_bytes(65536);wire+=encoder.encode(block);expected+=block;}require(decode_frames(decoder,wire)==expected,"random stream mismatch");require(encoder.wire_bytes<=encoder.raw_bytes+8,"random stream overhead unbounded");});
    add("codec_reject_concatenated_zstd_frame",[]{auto raw=std::string(1024,'x');std::string compressed(ZSTD_compressBound(raw.size()),'\0');auto size=ZSTD_compress(compressed.data(),compressed.size(),raw.data(),raw.size(),1);compressed.resize(size);auto payload=compressed+compressed;bridge::Codec decoder;rejected([&]{decode_frames(decoder,"BCZ1"+vuint((payload.size()<<1)|1)+vuint(raw.size())+payload);});});
    add("wakeup_nonblocking_and_drain",[]{Wakeup wake;for(int i=0;i<100;++i)wake.notify();PollFd fd{wake.fd(),POLLIN,0};require(net_poll(&fd,1,100)>0,"worker failed to wake");wake.drain();fd.revents=0;require(net_poll(&fd,1,0)==0,"wake drain incomplete");});
    unsigned failures=0;for(auto& [name,test]:cases){try{test();std::cout<<"PASS "<<name<<'\n';}catch(const std::exception& error){++failures;std::cerr<<"FAIL "<<name<<": "<<error.what()<<'\n';}}
    std::cout<<cases.size()<<" native cases; failures="<<failures<<'\n';
#ifdef _WIN32
    WSACleanup();
#endif
    return failures?1:0;
}
