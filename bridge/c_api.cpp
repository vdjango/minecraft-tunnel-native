// SPDX-License-Identifier: AGPL-3.0-or-later
#include <buggcraft/bridge.h>
#include "rpc.hpp"
#include "runtime.hpp"
#include <cstdlib>
#include <cstring>
#include <mutex>
#include <memory>
struct buggcraft_bridge {
    std::mutex mutex;
    bridge::Rpc* rpc=nullptr;
};
extern "C" {
uint32_t BUGGCRAFT_CALL buggcraft_bridge_abi_version(void){return 1;}
int32_t BUGGCRAFT_CALL buggcraft_bridge_create(buggcraft_bridge** output){
    if(!output)return BUGGCRAFT_INVALID_ARGUMENT;
    *output=nullptr;
    try{bridge::acquire_runtime();}catch(...){return BUGGCRAFT_INITIALIZATION_FAILED;}
    try{
        auto handle=std::make_unique<buggcraft_bridge>();
        handle->rpc=bridge::create_rpc();
        *output=handle.release();return BUGGCRAFT_OK;
    }catch(...){bridge::release_runtime();return BUGGCRAFT_INTERNAL_ERROR;}
}
int32_t BUGGCRAFT_CALL buggcraft_bridge_call(buggcraft_bridge* handle,
    const char* request,size_t request_size,char** response,size_t* response_size){
    if(response)*response=nullptr;
    if(response_size)*response_size=0;
    if(!handle||!request||!response||!response_size||request_size>8192)return BUGGCRAFT_INVALID_ARGUMENT;
    try{
        std::lock_guard lock(handle->mutex);
        auto result=bridge::exchange_rpc(handle->rpc,std::string_view(request,request_size));
        if(result.empty())return BUGGCRAFT_OK;
        auto bytes=static_cast<char*>(std::malloc(result.size()+1));
        if(!bytes)return BUGGCRAFT_INTERNAL_ERROR;
        std::memcpy(bytes,result.c_str(),result.size()+1);
        *response=bytes;*response_size=result.size();return BUGGCRAFT_OK;
    }catch(...){return BUGGCRAFT_INTERNAL_ERROR;}
}
void BUGGCRAFT_CALL buggcraft_bridge_free(void* response){std::free(response);}
void BUGGCRAFT_CALL buggcraft_bridge_destroy(buggcraft_bridge* handle){
    if(!handle)return;
    try{bridge::destroy_rpc(handle->rpc);}catch(...){}
    delete handle;bridge::release_runtime();
}
}
