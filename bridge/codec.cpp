// SPDX-License-Identifier: AGPL-3.0-or-later
#define ZSTD_STATIC_LINKING_ONLY
#include "codec.hpp"
#include <stdexcept>
#include <array>
namespace bridge {
namespace {
std::string varint(size_t value){std::string result;do{auto byte=value&127;value>>=7;result+=static_cast<char>(byte|(value?128:0));}while(value);return result;}
bool length(const std::string& data,size_t& offset,size_t& value){value=0;for(unsigned i=0;i<3;++i){if(offset>=data.size())return false;auto byte=static_cast<unsigned char>(data[offset++]);value|=static_cast<size_t>(byte&127)<<(i*7);if(!(byte&128))return true;}throw std::runtime_error("Invalid compression length");}
}
Codec::Codec(){if(!compressor_||!decompressor_)throw std::bad_alloc();ZSTD_DCtx_setParameter(decompressor_,ZSTD_d_windowLogMax,16);}
Codec::~Codec(){ZSTD_freeCCtx(compressor_);ZSTD_freeDCtx(decompressor_);}
std::string Codec::encode(std::string_view data){
    if(data.empty()||data.size()>65536)throw std::runtime_error("Invalid compression block");
    std::string result;
    if(send_raw_||((raw_bytes>=2048||blocks_>=64)&&wire_bytes>=raw_bytes)){if(!send_raw_)result+='\0';send_raw_=true;result+=data;}
    else{
        std::string payload(data);bool compressed=false;
        if(skip_)--skip_;else if(data.size()>=256){
            std::string candidate(ZSTD_compressBound(data.size()),'\0');const auto size=ZSTD_compressCCtx(compressor_,candidate.data(),candidate.size(),data.data(),data.size(),1);
            if(ZSTD_isError(size))throw std::runtime_error("Compression failed");
            if(size+32<data.size()){candidate.resize(size);payload=std::move(candidate);compressed=true;misses_=0;++compressed_blocks;}else if(++misses_>=4)skip_=31;
        }
        result=varint((payload.size()<<1)|static_cast<size_t>(compressed));if(compressed)result+=varint(data.size());result+=payload;++blocks_;
    }raw_bytes+=data.size();wire_bytes+=result.size();return result;
}
void Codec::feed(std::string_view data){if(data.size()>receive_capacity())throw std::runtime_error("Compression receive budget exceeded");pending_+=data;}
bool Codec::next(std::string& output){
    output.clear();if(!magic_){if(pending_.size()<4)return false;if(pending_.substr(0,4)!="BCZ1")throw std::runtime_error("Invalid compression handshake");pending_.erase(0,4);magic_=true;}
    if(receive_raw_){if(pending_.empty())return false;if(pending_.size()<=65536)output.swap(pending_);else{output.assign(pending_,0,65536);pending_.erase(0,65536);}return true;}
    if(pending_.empty())return false;size_t offset=0,header=0;if(!length(pending_,offset,header))return false;
    if(!header){pending_.erase(0,offset);receive_raw_=true;return next(output);}
    const size_t size=header>>1;const bool compressed=(header&1)!=0;size_t decoded=size;
    if(!size||size>65536)throw std::runtime_error("Invalid compression block length");
    if(compressed&&!length(pending_,offset,decoded))return false;if(!decoded||decoded>65536)throw std::runtime_error("Invalid decompression size");
    if(pending_.size()<offset+size)return false;
    if(compressed){
        ZSTD_frameHeader frame{};const auto result=ZSTD_getFrameHeader(&frame,pending_.data()+offset,size);
        if(result!=0||frame.windowSize>65536||frame.frameContentSize!=decoded||ZSTD_findFrameCompressedSize(pending_.data()+offset,size)!=size)throw std::runtime_error("Invalid compressed frame");
        output.resize(decoded);const auto count=ZSTD_decompressDCtx(decompressor_,output.data(),output.size(),pending_.data()+offset,size);
        if(ZSTD_isError(count)||count!=decoded)throw std::runtime_error("Invalid compressed content");
    }else output.assign(pending_,offset,size);pending_.erase(0,offset+size);return true;
}
bool Codec::complete() const{return magic_&&pending_.empty();}
}
