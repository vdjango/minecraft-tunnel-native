#include "rpc.hpp"
#include "runtime.hpp"
#include <iostream>
#include <csignal>
int main(int argc,char** argv){
    if(argc==2&&std::string(argv[1])=="--version"){std::cout<<"Buggcraft native bridge " BUGGCRAFT_VERSION " protocol=1 C++20\n";return 0;}
#ifndef _WIN32
    std::signal(SIGPIPE,SIG_IGN);
#endif
    std::ios::sync_with_stdio(false);std::cin.tie(nullptr);int result=0;
    try{bridge::acquire_runtime();try{bridge::run_rpc();}catch(...){bridge::release_runtime();throw;}bridge::release_runtime();}catch(...){std::cerr<<"[bridge] bridge_fatal\n";result=1;}
    return result;
}
