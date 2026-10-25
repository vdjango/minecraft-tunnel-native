// SPDX-License-Identifier: AGPL-3.0-or-later
#include "modpack.hpp"
#include <algorithm>
#include <array>
#include <regex>

namespace bridge {
namespace {
constexpr uint64_t maximum_file=2ull*1024*1024*1024;
constexpr size_t maximum_manifest=512*1024;
std::string digest_file(const std::filesystem::path& path){
    std::ifstream input(path,std::ios::binary);
    if(!input)throw Failure(-32602,"Mod file cannot be opened");
    auto context=std::unique_ptr<EVP_MD_CTX,decltype(&EVP_MD_CTX_free)>(EVP_MD_CTX_new(),EVP_MD_CTX_free);
    if(!context||EVP_DigestInit_ex(context.get(),EVP_sha256(),nullptr)!=1)throw std::runtime_error("SHA256 unavailable");
    std::array<char,131072> bytes{};
    while(input.read(bytes.data(),bytes.size())||input.gcount())
        if(EVP_DigestUpdate(context.get(),bytes.data(),static_cast<size_t>(input.gcount()))!=1)throw std::runtime_error("SHA256 failed");
    if(!input.eof())throw Failure(-32602,"Mod file read failed");
    unsigned char hash[32];unsigned count=0;
    if(EVP_DigestFinal_ex(context.get(),hash,&count)!=1)throw std::runtime_error("SHA256 failed");
    static constexpr char hex[]="0123456789abcdef";std::string result;
    for(unsigned i=0;i<count;++i){result+=hex[hash[i]>>4];result+=hex[hash[i]&15];}
    return result;
}
bool safe_name(const std::string& name){
    return !name.empty()&&name.size()<=240&&name.find_first_of("/\\:\r\n\t") == std::string::npos
        &&name.find('\0')==std::string::npos&&name!="."&&name!="..";
}
Json page(const Json& items,const Json& params){
    auto offset=params.value("offset",0);
    if(offset<0||static_cast<size_t>(offset)>items.size())throw Failure(-32602,"Invalid manifest offset");
    Json values=Json::array();
    for(size_t i=static_cast<size_t>(offset);i<std::min(items.size(),static_cast<size_t>(offset)+50);++i)values.push_back(items[i]);
    return {{"items",values},{"total",items.size()},{"offset",offset}};
}
}
struct ModTransfers::Transfer {
    ssh_channel channel=nullptr;
    std::string id=random_id(12),room,state="opening",error,input,pending,digest;
    std::filesystem::path target,temporary;
    std::ifstream source;
    std::ofstream output;
    std::unique_ptr<EVP_MD_CTX,decltype(&EVP_MD_CTX_free)> hash{nullptr,EVP_MD_CTX_free};
    uint64_t size=0,received=0;
    int stage=0;
    bool server=false,manifest=false,header=false;
    Clock::time_point deadline=Clock::now()+std::chrono::seconds(30);
    Json data;
    ~Transfer(){free_channel(channel);output.close();if(!temporary.empty()){std::error_code ec;std::filesystem::remove(temporary,ec);}}
};
ModTransfers::ModTransfers()=default;
ModTransfers::~ModTransfers()=default;
void ModTransfers::configure(const std::string& directory){
    if(directory.empty()){root_.clear();catalog_=Json::array();configured_=false;return;}
    auto root=std::filesystem::weakly_canonical(std::filesystem::u8path(directory)/"mods");
    Json catalog=Json::array();std::vector<std::filesystem::path> paths;
    if(std::filesystem::is_directory(root))for(const auto& entry:std::filesystem::directory_iterator(root)){
        const auto path=entry.path();auto extension=path.extension().string();
        std::transform(extension.begin(),extension.end(),extension.begin(),[](unsigned char c){return static_cast<char>(std::tolower(c));});
        if(extension!=".jar")continue;
        auto resolved=std::filesystem::canonical(path);
        if(resolved.parent_path()!=root||!std::filesystem::is_regular_file(resolved))throw Failure(-32602,"Mod path escapes its directory");
        paths.push_back(path);
        if(paths.size()>1000)throw Failure(-32602,"Too many mods");
    }
    std::sort(paths.begin(),paths.end());
    for(const auto& path:paths){
        auto encoded=path.filename().u8string();std::string name(encoded.begin(),encoded.end());
        auto size=std::filesystem::file_size(path);
        if(!safe_name(name)||size>maximum_file)throw Failure(-32602,"Invalid mod file");
        catalog.push_back({{"name",name},{"size",size},{"sha256",digest_file(path)}});
    }
    if(catalog.dump().size()>maximum_manifest)throw Failure(-32602,"Mod manifest exceeds limit");
    root_=std::move(root);catalog_=std::move(catalog);configured_=true;
}
Json ModTransfers::metadata()const{
    if(!configured_)return Json::object();
    Json preview=Json::array();
    for(const auto& item:catalog_){
        if(preview.size()>=24)break;
        if(item.at("name").get_ref<const std::string&>().size()<=64&&
           item.at("size").get<uint64_t>()<=2147483647ull)preview.push_back(item);
    }
    Json result{{"mods_count",catalog_.size()},{"mods",std::move(preview)}};
    {
        // The launcher identifies a mod set by sorted byte hashes, independent
        // of filenames, JSON key order, and the preview's pagination limit.
        std::vector<std::string> hashes;
        for(const auto& item:catalog_)hashes.push_back(item.at("sha256").get<std::string>());
        std::sort(hashes.begin(),hashes.end());
        std::string serialized;
        for(size_t i=0;i<hashes.size();++i){if(i)serialized+='\n';serialized+=hashes[i];}
        unsigned char digest[EVP_MAX_MD_SIZE]{};unsigned length=0;
        if(EVP_Digest(serialized.data(),serialized.size(),digest,&length,EVP_sha256(),nullptr)!=1||length!=32)
            throw std::runtime_error("Mod manifest digest failed");
        static constexpr char hex[]="0123456789abcdef";std::string value;value.reserve(64);
        for(unsigned i=0;i<length;++i){value+=hex[digest[i]>>4];value+=hex[digest[i]&15];}
        result["mods_hash"]=std::move(value);
    }
    return result;
}
void ModTransfers::accept(ssh_channel channel){
    if(serving_.size()>=4){free_channel(channel);return;}
    auto transfer=std::make_unique<Transfer>();transfer->channel=channel;transfer->server=true;transfer->state="request";
    serving_.push_back(std::move(transfer));
}
Json ModTransfers::command(const std::string& method,const Json& params,const std::string& room,bool host){
    if(method=="mods.manifest"&&host){auto result=page(catalog_,params);result["state"]="complete";return result;}
    if(method=="mods.status"||method=="mods.cancel"){
        const auto id=params.at("transfer").get<std::string>();auto found=transfers_.find(id);
        if(found==transfers_.end())throw Failure(-32602,"Unknown mod transfer");
        auto& t=*found->second;
        if(method=="mods.cancel"){transfers_.erase(found);return {{"cancelled",true}};}
        Json result{{"transfer",id},{"state",t.state},{"received",t.received},{"size",t.size},{"error",t.error}};
        if(t.manifest&&t.state=="complete")result.update(page(t.data,params));
        return result;
    }
    if(method!="mods.fetch"&&method!="mods.manifest")throw Failure(-32601,"Unknown mod operation");
    const auto active=std::count_if(transfers_.begin(),transfers_.end(),[](const auto& item){return item.second->state!="complete"&&item.second->state!="error";});
    if(active>=4||transfers_.size()>=16)throw Failure(-32010,"Mod transfer limit reached");
    auto transfer=std::make_unique<Transfer>();auto& t=*transfer;t.room=room;t.manifest=method=="mods.manifest";
    if(t.manifest)t.pending="{\"op\":\"manifest\"}\n";
    else{
        const auto item=params.at("item");t.digest=item.at("sha256").get<std::string>();
        if(!std::regex_match(t.digest,std::regex("[0-9a-f]{64}"))||(!item.at("size").is_number_unsigned()&&!item.at("size").is_number_integer()))throw Failure(-32602,"Invalid mod metadata");
        t.size=item.at("size").get<uint64_t>();if(t.size>maximum_file)throw Failure(-32602,"Mod exceeds size limit");
        auto destination=params.at("target").get<std::string>();if(destination.find('\0')!=std::string::npos)throw Failure(-32602,"Invalid mod target");
        t.target=std::filesystem::absolute(std::filesystem::u8path(destination));
        if(t.target.extension()!=".jar"||!std::filesystem::is_directory(t.target.parent_path())||std::filesystem::is_symlink(t.target))throw Failure(-32602,"Invalid mod target");
        t.temporary=t.target;t.temporary+="."+t.id+".part";
        t.output.open(t.temporary,std::ios::binary|std::ios::trunc);if(!t.output)throw Failure(-32602,"Mod destination cannot be written");
        t.hash.reset(EVP_MD_CTX_new());if(!t.hash||EVP_DigestInit_ex(t.hash.get(),EVP_sha256(),nullptr)!=1)throw std::runtime_error("SHA256 unavailable");
        t.pending=Json{{"op","file"},{"sha256",t.digest}}.dump()+"\n";
    }
    const auto id=t.id;
    log("mod_transfer_started",{{"transfer",id},{"manifest",t.manifest},{"bytes",t.size}});
    transfers_.emplace(id,std::move(transfer));return {{"transfer",id},{"state","opening"}};
}
void ModTransfers::pump(Transfer& t,Ssh& ssh){
    if(t.state=="complete"||t.state=="error")return;
    if(Clock::now()>t.deadline)throw Failure(-32000,"Mod transfer timed out");
    if(!t.channel)t.channel=ssh_channel_new(ssh.get());
    if(!t.channel)throw std::bad_alloc();
    if(!t.server&&t.stage==0){auto result=ssh_channel_open_forward(t.channel,t.room.c_str(),25564,"127.0.0.1",0);if(result==SSH_AGAIN)return;if(result!=SSH_OK)throw Failure(-32000,"Mod forwarding unavailable");t.stage=1;t.state="transferring";}
    if(!t.pending.empty()){
        int count=ssh_channel_write(t.channel,t.pending.data(),static_cast<uint32_t>(std::min<size_t>(t.pending.size(),65536)));
        if(count==SSH_ERROR)throw Failure(-32000,"Mod write failed");
        if(count>0){t.pending.erase(0,static_cast<size_t>(count));t.deadline=Clock::now()+std::chrono::seconds(30);}
        if(!t.pending.empty())return;
        if(t.server&&t.header&&(!t.source||t.received>=t.size)){t.state="complete";free_channel(t.channel);return;}
    }
    if(t.server&&t.header){
        std::array<char,65536> block{};t.source.read(block.data(),static_cast<std::streamsize>(std::min<uint64_t>(block.size(),t.size-t.received)));
        auto count=t.source.gcount();if(count<=0)throw Failure(-32000,"Mod file changed during transfer");
        t.pending.assign(block.data(),static_cast<size_t>(count));t.received+=static_cast<uint64_t>(count);return;
    }
    std::array<char,65536> block{};int count=ssh_channel_read_nonblocking(t.channel,block.data(),block.size(),0);
    if(count==SSH_ERROR||(count==0&&ssh_channel_is_eof(t.channel)))throw Failure(-32000,"Mod transfer disconnected");
    if(count<=0)return;
    t.deadline=Clock::now()+std::chrono::seconds(30);t.input.append(block.data(),static_cast<size_t>(count));
    if(!t.header){
        auto end=t.input.find('\n');if(end==std::string::npos){if(t.input.size()>512)throw Failure(-32602,"Mod header exceeds limit");return;}
        if(end>512)throw Failure(-32602,"Mod header exceeds limit");
        auto header=Json::parse(t.input.substr(0,end));t.input.erase(0,end+1);
        if(t.server){
            if(!t.input.empty())throw Failure(-32602,"Unexpected mod request data");
            auto operation=header.value("op","");
            if(operation=="manifest"){t.pending=catalog_.dump();t.size=t.pending.size();t.pending=Json{{"size",t.size}}.dump()+"\n"+t.pending;t.received=t.size;}
            else if(operation=="file"){
                auto digest=header.value("sha256","");auto item=std::find_if(catalog_.begin(),catalog_.end(),[&](const auto& value){return value.at("sha256")==digest;});
                if(item==catalog_.end())throw Failure(-32602,"Unknown mod file");
                auto path=std::filesystem::canonical(root_/std::filesystem::u8path(item->at("name").template get<std::string>()));
                t.size=item->at("size").get<uint64_t>();if(path.parent_path()!=root_||std::filesystem::file_size(path)!=t.size)throw Failure(-32602,"Mod changed; reopen the room");
                t.source.open(path,std::ios::binary);if(!t.source)throw Failure(-32000,"Mod cannot be opened");t.pending=Json{{"size",t.size}}.dump()+"\n";
            }else throw Failure(-32602,"Invalid mod operation");
            t.header=true;return;
        }
        if(!header.at("size").is_number_integer())throw Failure(-32602,"Invalid mod size");
        auto size=header.at("size").get<int64_t>();
        if(size<0||static_cast<uint64_t>(size)>(t.manifest?maximum_manifest:t.size)||(!t.manifest&&static_cast<uint64_t>(size)!=t.size))throw Failure(-32602,"Mod size mismatch");
        t.size=static_cast<uint64_t>(size);t.header=true;
    }
    if(t.input.size()>t.size-t.received)throw Failure(-32602,"Mod response exceeds declared size");
    if(t.manifest){if(t.data.is_null())t.data="";t.data.get_ref<std::string&>()+=t.input;}
    else{t.output.write(t.input.data(),static_cast<std::streamsize>(t.input.size()));if(!t.output||EVP_DigestUpdate(t.hash.get(),t.input.data(),t.input.size())!=1)throw Failure(-32000,"Mod write failed");}
    t.received+=t.input.size();t.input.clear();
    if(t.received==t.size){
        if(t.manifest){t.data=Json::parse(t.data.get<std::string>());if(!t.data.is_array()||t.data.size()>1000)throw Failure(-32602,"Invalid mod manifest");}
        else{
            unsigned char hash[32];unsigned length=0;if(EVP_DigestFinal_ex(t.hash.get(),hash,&length)!=1)throw std::runtime_error("SHA256 failed");
            static constexpr char hex[]="0123456789abcdef";std::string digest;for(unsigned i=0;i<length;++i){digest+=hex[hash[i]>>4];digest+=hex[hash[i]&15];}
            if(digest!=t.digest)throw Failure(-32602,"Mod SHA256 mismatch");
            t.output.close();
            if(std::filesystem::exists(t.target)){if(digest_file(t.target)!=t.digest)throw Failure(-32602,"Mod target already exists");std::filesystem::remove(t.temporary);}
            else std::filesystem::rename(t.temporary,t.target);
            t.temporary.clear();
        }
        t.state="complete";free_channel(t.channel);
        log("mod_transfer_completed",{{"transfer",t.id},{"manifest",t.manifest},{"bytes",t.received}});
    }
}
void ModTransfers::tick(Ssh& ssh){
    for(auto& [id,t]:transfers_)try{pump(*t,ssh);}catch(const std::exception& error){t->state="error";t->error=error.what();free_channel(t->channel);log("mod_transfer_failed",{{"transfer",id},{"bytes",t->received}});}
    for(auto& t:serving_)try{pump(*t,ssh);}catch(...){t->state="error";free_channel(t->channel);}
    std::erase_if(serving_,[](const auto& t){return t->state=="complete"||t->state=="error";});
}
void ModTransfers::disconnect(){
    serving_.clear();for(auto& [id,t]:transfers_)if(t->state!="complete"){t->state="error";t->error="Room connection changed";free_channel(t->channel);}
}
}
