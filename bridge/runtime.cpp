// SPDX-License-Identifier: AGPL-3.0-or-later
#include "runtime.hpp"
#include "ssh.hpp"
#include <rtc/rtc.hpp>
#include <mutex>
#include <thread>
namespace bridge {
namespace {std::mutex runtime_mutex;size_t runtime_users=0;}
void acquire_runtime(){
    std::lock_guard lock(runtime_mutex);
    if(runtime_users){++runtime_users;return;}
#ifdef _WIN32
    WSADATA data;
    if(WSAStartup(MAKEWORD(2,2),&data))throw std::runtime_error("Socket initialization failed");
#endif
    if(ssh_init()!=SSH_OK){
#ifdef _WIN32
        WSACleanup();
#endif
        throw std::runtime_error("SSH initialization failed");
    }
    try{
        rtc::SetThreadPoolSize(std::clamp(std::thread::hardware_concurrency(),2u,4u));
        rtc::InitLogger(rtc::LogLevel::Warning,[](rtc::LogLevel,const std::string&){log("webrtc_warning");});
        runtime_users=1;
    }catch(...){ssh_finalize();
#ifdef _WIN32
        WSACleanup();
#endif
        throw;
    }
}
void release_runtime() noexcept{
    std::lock_guard lock(runtime_mutex);
    if(!runtime_users||--runtime_users)return;
    try{rtc::Cleanup().wait();}catch(...){}
    ssh_finalize();
#ifdef _WIN32
    WSACleanup();
#endif
}
}
