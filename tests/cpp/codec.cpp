// SPDX-License-Identifier: AGPL-3.0-or-later
#include "../../bridge/codec.hpp"
#include <algorithm>
#include <functional>
#include <random>
#include <stdexcept>
#include <vector>
namespace {
void require(bool condition,const char* message){if(!condition)throw std::runtime_error(message);}
template<class F>void rejected(F action){bool caught=false;try{action();}catch(const std::exception&){caught=true;}require(caught,"invalid input was accepted");}
std::string vuint(size_t value){std::string out;do{auto part=value&127;value>>=7;out+=static_cast<char>(part|(value?128:0));}while(value);return out;}
std::string random_bytes(size_t size){std::mt19937 generator(12345);std::string out(size,'\0');for(auto& byte:out)byte=static_cast<char>(generator());return out;}
std::string decode_frames(bridge::Codec& decoder,std::string_view wire,size_t fragment=65536){
    std::string result,decoded;while(!wire.empty()){size_t count=std::min(fragment,wire.size());decoder.feed(wire.substr(0,count));wire.remove_prefix(count);while(decoder.next(decoded))result+=decoded;}return result;
}
}
void add_codec_tests(std::vector<std::pair<std::string,std::function<void()>>>& cases){
    auto add=[&](std::string name,std::function<void()> test){cases.emplace_back(std::move(name),std::move(test));};
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
    add("codec_reused_output_replaces_previous_bytes",[]{
        bridge::Codec encoder,decoder;std::string output(100000,'!'),wire="BCZ1",expected;
        for(size_t size:{65536u,1u,1024u,255u,65536u}){
            const auto payload=std::string(size,'v');encoder.encode(payload,output);wire+=output;expected+=payload;
        }
        require(decode_frames(decoder,wire,29)==expected,"reused output retained previous block bytes");
    });
    add("codec_coalesced_frames_refill_at_receive_budget",[]{
        bridge::Codec decoder;const std::string payload(32,'a'),frame=vuint(64)+payload;
        std::string wire="BCZ1";for(unsigned i=0;i<3000;++i)wire+=frame;decoder.feed(wire);
        std::string output;require(decoder.next(output)&&output==payload,"initial frame mismatch");
        const auto remaining=decoder.buffered();require(remaining==wire.size()-4-frame.size(),"consumed bytes counted as pending");
        // Reclaim a consumed prefix even when fewer than half the frames were read.
        const auto additional=(decoder.receive_capacity()/frame.size())*frame.size();
        std::string refill;for(size_t i=0;i<additional/frame.size();++i)refill+=frame;decoder.feed(refill);
        require(decoder.buffered()==remaining+additional,"refill budget mismatch");
        rejected([&]{decoder.feed(std::string(decoder.receive_capacity()+1,'x'));});
        size_t count=1;while(decoder.next(output)){require(output==payload,"compaction corrupted frame");++count;}
        require(count==3000+additional/frame.size()&&decoder.complete(),"compaction lost frames");
    });
    add("codec_fragmented_refill_keeps_partial_header",[]{
        bridge::Codec encoder,decoder;const auto raw=random_bytes(65536);
        const auto first=encoder.encode(std::string(65536,'c')),second=encoder.encode(raw);
        decoder.feed("BCZ1"+first+second.substr(0,1));std::string output;
        require(decoder.next(output)&&output==std::string(65536,'c'),"compressed prefix mismatch");
        require(!decoder.next(output)&&decoder.buffered()==1,"partial header consumed");
        decoder.feed(second.substr(1));require(decoder.next(output)&&output==raw,"refill corrupted partial header");
        require(decoder.complete(),"partial refill left bytes");
    });
    add("codec_reject_concatenated_zstd_frame",[]{auto raw=std::string(1024,'x');std::string compressed(ZSTD_compressBound(raw.size()),'\0');auto size=ZSTD_compress(compressed.data(),compressed.size(),raw.data(),raw.size(),1);compressed.resize(size);auto payload=compressed+compressed;bridge::Codec decoder;rejected([&]{decode_frames(decoder,"BCZ1"+vuint((payload.size()<<1)|1)+vuint(raw.size())+payload);});});
}
