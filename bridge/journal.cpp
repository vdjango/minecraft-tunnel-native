// SPDX-License-Identifier: AGPL-3.0-or-later
#include "journal.hpp"
#include <algorithm>
#include <chrono>
#include <cstdlib>
#include <fstream>
#include <iostream>
#include <cctype>
#include <map>
#include <unordered_map>
#ifdef _WIN32
#include <windows.h>
#endif
namespace bridge {
namespace {
bool safe(const std::string& text) {
    return !text.empty() && text.size()<=90 &&
           text.find_first_not_of("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_")==std::string::npos;
}
bool sensitive(std::string key) {
    std::transform(key.begin(), key.end(), key.begin(), [](unsigned char ch) {
        return static_cast<char>(std::tolower(ch));
    });
    static constexpr std::string_view fragments[] = {
        "token", "secret", "invite", "password", "passwd", "payload", "sdp",
        "credential", "authorization", "private_key", "public_key", "ice-pwd",
        "access_code", "room_code", "api_key", "xuid"
    };
    for (const auto fragment : fragments)
        if (key.find(fragment) != std::string::npos) return true;
    return key == "code";
}
void redact(nlohmann::json& value) {
    if (value.is_object()) {
        for (auto it = value.begin(); it != value.end();) {
            if (sensitive(it.key())) it = value.erase(it);
            else { redact(it.value()); ++it; }
        }
    } else if (value.is_array()) {
        for (auto& item : value) redact(item);
    }
}
std::filesystem::path environment(const char* name) {
#ifdef _WIN32
    std::wstring key;for(const char* at=name;*at;++at)key+=static_cast<wchar_t>(*at);
    const auto count=GetEnvironmentVariableW(key.c_str(),nullptr,0);
    if(!count)return {};
    std::wstring value(count,L'\0');GetEnvironmentVariableW(key.c_str(),value.data(),count);
    value.resize(count-1);return value;
#else
    const char* value=std::getenv(name);return value?std::filesystem::path(value):std::filesystem::path{};
#endif
}
std::mutex routes_mutex;
struct Route {std::string player,room;};
std::unordered_map<std::string,Route> routes;
}
std::filesystem::path Journal::default_root(){
    auto override_path=environment("BUGGCRAFT_BRIDGE_LOG_DIR");if(!override_path.empty())return override_path;
#ifdef _WIN32
    auto base=environment("LOCALAPPDATA");if(base.empty())base=std::filesystem::temp_directory_path();
    return base/"Buggcraft"/"logs"/"players";
#elif defined(__APPLE__)
    auto base=environment("HOME");if(base.empty())base=std::filesystem::temp_directory_path();
    return base/"Library"/"Logs"/"Buggcraft"/"players";
#else
    auto base=environment("XDG_STATE_HOME");
    if(base.empty()){
        base=environment("HOME");
        if(base.empty())base=std::filesystem::temp_directory_path();
        else {base/=".local";base/="state";}
    }
    return base/"buggcraft"/"players";
#endif
}
Journal::Journal(std::filesystem::path root,size_t max_bytes,unsigned days,size_t rotate_bytes)
    :root_(std::move(root)),max_bytes_(max_bytes),rotate_bytes_(rotate_bytes),days_(days),
     writer_([this]{run();}){}
Journal::~Journal(){
    {std::lock_guard lock(mutex_);stopping_=true;}changed_.notify_one();if(writer_.joinable())writer_.join();
}
bool Journal::write(std::string player,std::string session,std::string event,nlohmann::json fields){
    if(!safe(player)||!safe(session)||!safe(event)||!fields.is_object()){++dropped_;return false;}
    redact(fields);
    fields["at"]=std::chrono::duration<double>(std::chrono::system_clock::now().time_since_epoch()).count();
    fields["event"]=std::move(event);fields["player"]=player;fields["session"]=session;
    std::string row=fields.dump(-1,' ',true)+"\n";
    std::lock_guard lock(mutex_);
    if(stopping_||row.size()>8192||queue_.size()>=2048||queued_bytes_+row.size()>2*1024*1024){++dropped_;return false;}
    queued_bytes_+=row.size();queue_.push_back({std::move(player),std::move(session),std::move(row)});
    changed_.notify_one();return true;
}
void Journal::append(const Row& row){
    auto directory=root_/row.player;
    if(std::filesystem::is_symlink(root_)||std::filesystem::is_symlink(directory))throw std::runtime_error("symlink journal directory");
    std::filesystem::create_directories(directory);
#ifndef _WIN32
    std::filesystem::permissions(root_,std::filesystem::perms::owner_all,std::filesystem::perm_options::replace);
    std::filesystem::permissions(directory,std::filesystem::perms::owner_all,std::filesystem::perm_options::replace);
#endif
    auto path=directory/(row.session+".jsonl");
    if(std::filesystem::is_symlink(path))throw std::runtime_error("symlink journal file");
    if(std::filesystem::exists(path)&&!std::filesystem::is_regular_file(path))throw std::runtime_error("invalid journal file");
    uintmax_t file_size=std::filesystem::exists(path)?std::filesystem::file_size(path):0;
    const auto previous=directory/(row.session+".1.jsonl");
    std::ofstream output;
    size_t offset=0;
    while(offset<row.text.size()){
        const auto end=row.text.find('\n',offset);
        const auto count=(end==std::string::npos?row.text.size():end+1)-offset;
        if(file_size&&file_size+count>rotate_bytes_){
            if(output.is_open()){
                output.close();
                if(!output)throw std::runtime_error("journal flush failed");
                output.clear();
            }
            std::error_code error;
            std::filesystem::remove(previous,error);
            std::filesystem::rename(path,previous);
            file_size=0;
        }
        if(!output.is_open()){
            output.open(path,std::ios::app|std::ios::binary);
            if(!output)throw std::runtime_error("journal open failed");
#ifndef _WIN32
            std::filesystem::permissions(path,std::filesystem::perms::owner_read|std::filesystem::perms::owner_write,
                                        std::filesystem::perm_options::replace);
#endif
        }
        output.write(row.text.data()+offset,static_cast<std::streamsize>(count));
        if(!output)throw std::runtime_error("journal write failed");
        file_size+=count;
        offset+=count;
    }
    output.close();
    if(!output)throw std::runtime_error("journal write failed");
}
void Journal::prune(){
    if(!std::filesystem::exists(root_)||std::filesystem::is_symlink(root_))return;
    struct File {std::filesystem::path path;std::filesystem::file_time_type time;uintmax_t size;};
    std::vector<File> files;uintmax_t total=0;
    for(const auto& folder:std::filesystem::directory_iterator(root_)){
        if(folder.is_symlink()||!folder.is_directory())continue;
        for(const auto& file:std::filesystem::directory_iterator(folder)){
            if(file.is_symlink()||!file.is_regular_file()||file.path().extension()!=".jsonl")continue;
            auto size=file.file_size();total+=size;files.push_back({file.path(),file.last_write_time(),size});
        }
    }
    std::sort(files.begin(),files.end(),[](const File& a,const File& b){return a.time<b.time;});
    const auto cutoff=std::filesystem::file_time_type::clock::now()-std::chrono::hours(24*days_);
    size_t remaining=files.size();
    for(const auto& file:files)if(total>max_bytes_||file.time<cutoff||remaining>4096){std::filesystem::remove(file.path);total-=file.size;--remaining;}
    for(const auto& folder:std::filesystem::directory_iterator(root_))
        if(!folder.is_symlink()&&folder.is_directory()&&std::filesystem::is_empty(folder))std::filesystem::remove(folder);
}
void Journal::run(){
    auto next_prune=std::chrono::steady_clock::now();
    for(;;){
        std::deque<Row> batch;
        {std::unique_lock lock(mutex_);changed_.wait_for(lock,std::chrono::milliseconds(250),[this]{return stopping_||!queue_.empty();});
         batch.swap(queue_);queued_bytes_=0;if(stopping_&&batch.empty())break;}
        std::map<std::pair<std::string,std::string>,Row> grouped;
        for(auto& row:batch){
            auto key=std::make_pair(row.player,row.session);
            auto found=grouped.try_emplace(key,Row{row.player,row.session,{}}).first;
            found->second.text+=row.text;
        }
        for(const auto& entry:grouped)try{append(entry.second);}catch(...){
            const auto failures=++errors_;
            if(failures==1||failures%100==0)std::cerr<<"bridge journal write failed ("<<failures<<"); game forwarding continues\n";
        }
        if(std::chrono::steady_clock::now()>=next_prune){try{prune();}catch(...){
            const auto failures=++errors_;
            if(failures==1||failures%100==0)std::cerr<<"bridge journal cleanup failed ("<<failures<<"); game forwarding continues\n";
        }next_prune=std::chrono::steady_clock::now()+std::chrono::seconds(30);}
    }
    try{prune();}catch(...){++errors_;}
}
Journal& journal(){static Journal instance(Journal::default_root());return instance;}
void bind_log(const std::string& session,const std::string& player,const std::string& room){
    std::lock_guard lock(routes_mutex);routes[session]={player,room};
}
void unbind_log(const std::string& session){std::lock_guard lock(routes_mutex);routes.erase(session);}
void log(const std::string& event,const nlohmann::json& fields){
    try {
        std::string player="bridge",session="runtime";auto details=fields;
        if(fields.contains("session")&&fields["session"].is_string()){
            session=fields["session"].get<std::string>();std::lock_guard lock(routes_mutex);
            if(auto found=routes.find(session);found!=routes.end()){player=found->second.player;details["room"]=found->second.room;}
        }
        if(fields.contains("player")&&fields["player"].is_string())player=fields["player"].get<std::string>();
        journal().write(std::move(player),std::move(session),event,std::move(details));
    } catch (...) {
        // Diagnostics must never tear down a player's transport.
    }
}
}
