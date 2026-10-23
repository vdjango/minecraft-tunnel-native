// SPDX-License-Identifier: AGPL-3.0-or-later
#include "session.hpp"
#include "routing.hpp"
#include "route_selector.hpp"
#include "local_game.hpp"
#include "social.hpp"
#include "rpc.hpp"
#include <iostream>
namespace bridge {
struct Node{Profile profile;std::vector<Profile> alternatives;std::unique_ptr<RouteSelector> routes;};
class Rpc {
    std::unordered_map<std::string,Node> nodes_;
    std::unordered_map<std::string,std::shared_ptr<Session>> sessions_;
    std::unordered_map<std::string,std::string> session_nodes_;
    Node& node(const Json& p){auto key=p.value("node",Json(nullptr));if(!key.is_string()||!nodes_.contains(key.get<std::string>()))throw Failure(-32602,"Unknown node handle; call node.add first");return nodes_.at(key.get<std::string>());}
    std::shared_ptr<Session> session(const Json& p){auto key=p.value("session",Json(nullptr));if(!key.is_string()||!sessions_.contains(key.get<std::string>()))throw Failure(-32004,"Unknown session");return sessions_.at(key.get<std::string>());}
public:
    ~Rpc(){for(auto& [id,s]:sessions_)s->stop();}
    Json dispatch(const std::string& method,const Json& p){
        if(method=="bridge.initialize"){if(p.value("protocol",Json(1))!=1)throw Failure(-32602,"Unsupported bridge protocol version");
            return {{"protocol",1},{"name","buggcraft-bridge"},{"jsonrpc","2.0"},{"build",{{"protocol",1},{"implementation","cpp"},{"version",BUGGCRAFT_VERSION},{"signed",false}}},{"transports",{"auto","relay"}},{"mixed_rooms",true},
                {"methods",{"node.add","node.remove","node.request","social.operate","node.routes","node.selected","node.failed","bridge.events","game.status","game.discover","game.announce","room.list","room.resolve","room.host","room.join","room.close","room.request","room.retarget","room.configure","mods.manifest","mods.fetch","mods.status","mods.cancel","mods.configure","chat.poll","chat.send","session.events","session.list"}},{"limits",{{"request_bytes",8192},{"nodes",16},{"sessions",8}}}};}
        if(method=="node.add"){if(nodes_.size()>=16)throw Failure(-32010,"Node limit reached");if(!p.contains("code")||!p["code"].is_string())throw Failure(-32602,"code must be a string");Node value;value.profile=Profile::decode(p["code"].get<std::string>());
            auto imported=p.value("alternatives",Json::array());if(!imported.is_array()||imported.size()>5)throw Failure(-32602,"Invalid fallback profiles");
            for(const auto& item:imported)value.alternatives.push_back(Profile::from_json(item));
            bool verified=false;
            try{Ssh connection(value.profile,"directory:rooms",{},2);auto ping=connection.request({{"op","ping"}},2);value.profile.name=ping.value("name","Node");verified=true;
                try{auto topology=connection.request({{"op","topology"}},2).at("nodes");if(topology.is_array()&&topology.size()<=32)for(auto& item:topology)try{value.alternatives.push_back(Profile::from_json(item));}catch(...){} }catch(...){}
            }catch(...){if(value.alternatives.empty())throw;}
            value.routes=std::make_unique<RouteSelector>(value.profile,value.alternatives);
            try{value.routes->refresh(value.profile,value.alternatives);}catch(...){if(!verified)throw;log("route_probe_deferred");}
            auto handle=random_id(12);Json result{{"node",handle},{"name",value.profile.name},{"identity",value.profile.token.starts_with("acct1")?"account":"anonymous"}};nodes_.emplace(handle,std::move(value));return result;}
        if(method=="node.routes"){auto& n=node(p);try{n.routes->refresh(n.profile,n.alternatives,p.value("refresh",false));}catch(...){}
            return {{"name",n.profile.name},{"routes",n.routes->status()},{"backups",n.alternatives.size()}};}
        if(method=="node.selected"){auto& n=node(p);try{n.routes->refresh(n.profile,n.alternatives);}catch(...){}
            auto profile_json=[](const Profile& profile){return Json{{"server_host",profile.host},{"server_port",profile.port},{"server_public_key",profile.key},{"token",profile.token}};};
            auto backups=Json::array();for(const auto& backup:n.alternatives)backups.push_back(profile_json(backup));
            return {{"profile",profile_json(n.profile)},{"name",n.profile.name},{"backups",backups},{"accepting",n.routes->admissible(n.profile)}};}
        if(method=="bridge.events"){auto result=Json::array();for(auto& [handle,n]:nodes_)for(auto& event:n.routes->events()){event["node"]=handle;result.push_back(event);}return {{"events",result}};}
        if(method=="game.status")return game_status(p.at("port").get<int>(),p.value("timeout_ms",2000));
        if(method=="game.discover"){std::set<int> excluded;auto list=p.value("exclude",Json::array());if(!list.is_array()||list.size()>64)throw Failure(-32602,"Invalid excluded ports");for(auto& item:list){if(!item.is_number_integer()||item.get<int>()<1||item.get<int>()>65535)throw Failure(-32602,"Invalid excluded port");excluded.insert(item.get<int>());}return discover_worlds(excluded,p.value("duration_ms",3000));}
        if(method=="game.announce"){const auto port=p.at("port").get<int>();if(port<1||port>65535)throw Failure(-32602,"Invalid game port");announce_world(port,p.value("title",std::string("Buggcraft Room")));return {{"announced",true}};}
        if(method=="node.failed"){auto& n=node(p);n.routes->failed(Profile::from_json(p.at("profile")));return {{"recorded",true}};}
        if(method=="node.remove"){node(p);auto key=p.at("node").get<std::string>();for(auto& [id,n]:session_nodes_)if(n==key)throw Failure(-32009,"Node has active rooms");nodes_.erase(key);return {{"removed",true}};}
        if(method=="room.list"||method=="room.resolve"||method=="node.request"||method=="social.operate"){auto& n=node(p);Json request;
            const auto started=Clock::now();
            if(method=="room.list"){auto offset=p.value("offset",Json(0));auto limit=p.value("limit",20);auto query=p.value("query",std::string{});if(!offset.is_number_integer()||offset.get<int64_t>()<0||offset.get<int64_t>()>4096||limit<1||limit>50||query.size()>400)throw Failure(-32602,"Invalid room pagination");request={{"op","list"},{"offset",offset},{"limit",limit},{"query",query}};}
            else if(method=="room.resolve")request={{"op","resolve"},{"id",p.at("id")}};
            else if(method=="social.operate")request={{"op","social"},{"event",social_event(n.profile,p)}};
            else{request=p.at("request");if(!request.is_object())throw Failure(-32602,"Invalid request");}
            try{n.routes->refresh(n.profile,n.alternatives);}catch(...){}
            const auto before=n.profile;
            auto result=directory_request(n.profile,n.alternatives,request);
            if(method=="room.resolve"&&!result.contains("secret")&&result.contains("profile")&&!result["profile"].is_null()){
                const auto owner=Profile::from_json(result["profile"]);Ssh direct(owner,"directory:rooms");result=direct.request(request);
            }
            if(method=="room.list"){
                result["connect_ms"]=std::chrono::duration<double,std::milli>(Clock::now()-started).count();
                result["node_name"]=n.profile.name;result["node_latency_ms"]=result["connect_ms"];
            }
            if(before.key!=n.profile.key||before.host!=n.profile.host||before.port!=n.profile.port){n.routes->failed(before);n.routes->recovered(before,n.profile);try{n.routes->refresh(n.profile,n.alternatives);}catch(...){}}
            return result;}
        if(method=="room.host"||method=="room.join"){auto& n=node(p);if(sessions_.size()>=8)throw Failure(-32010,"Session limit reached");auto policy=p.value("transport","auto");if(policy!="auto"&&policy!="relay")throw Failure(-32602,"Invalid transport policy");auto compression=p.value("compression","auto");if(compression!="auto"&&compression!="off")throw Failure(-32602,"Invalid compression policy");
            bool host=method=="room.host";int port=25565;if(host&&p.contains("game_port")){if(!p["game_port"].is_number_integer()||p["game_port"].is_boolean())throw Failure(-32602,"Invalid game_port");port=p["game_port"].get<int>();if(port<0||port>65535)throw Failure(-32602,"Invalid game_port");}
            std::string secret=host?random_id(32):invitation(p.at("invite").get<std::string>());Json settings=p.value("settings",Json::object());if(!settings.is_object())throw Failure(-32602,"Invalid room settings");
            try{n.routes->refresh(n.profile,n.alternatives);}catch(...){if(host)throw;}
            if(host&&!n.routes->admissible(n.profile))throw Failure(-32022,"No node with available room capacity");
            const auto before=n.profile;
            auto profile=host?n.profile:locate_room(n.profile,n.alternatives,room_id(secret));
            if(before.key!=n.profile.key||before.host!=n.profile.host||before.port!=n.profile.port){n.routes->failed(before);n.routes->recovered(before,n.profile);}
            auto endpoint=std::make_shared<Session>(profile,host?"host":"client",secret,policy,port,settings,compression,n.alternatives,p.value("listen_port",0),p.value("announce",true),p.value("game_directory",std::string{}),p.value("chat_name",std::string("Player")),p.value("game_chat_enabled",false),p.value("game_chat_name",std::string{}));auto result=endpoint->start();sessions_[endpoint->identity]=endpoint;session_nodes_[endpoint->identity]=p["node"].get<std::string>();return result;}
        if(method=="room.close"){auto endpoint=session(p);endpoint->stop();sessions_.erase(endpoint->identity);session_nodes_.erase(endpoint->identity);return {{"closed",true}};}
        if(method=="room.request")return session(p)->request(p.at("request"));
        if(method=="session.events")return session(p)->events();
        if(method=="room.retarget")return session(p)->retarget(p.at("port").get<int>(),p.at("game"));
        if(method=="room.configure")return session(p)->configure(p.at("settings"));
        if(method=="mods.manifest"||method=="mods.fetch"||method=="mods.status"||method=="mods.cancel"||method=="mods.configure")return session(p)->mods(method,p);
        if(method=="chat.poll")return session(p)->chat(Json::object());
        if(method=="chat.send"){auto text=p.at("text").get<std::string>();if(text.empty()||text.size()>2000)throw Failure(-32602,"Invalid chat text");return session(p)->chat({{"op","send"},{"text",text},{"request",random_id(12)}});}
        if(method=="session.list"){auto values=Json::array();for(auto& [id,s]:sessions_)values.push_back(s->status());return {{"sessions",values}};}
        throw Failure(-32601,"Method not found");
    }
    std::string exchange(std::string_view line){
            Json id=nullptr,response;bool notification=false;
            try{if(line.size()>8192)throw Failure(-32600,"Request too large");Json value;try{value=Json::parse(line);}catch(...){throw Failure(-32700,"Parse error");}
                if(!value.is_object()||value.value("jsonrpc",Json(nullptr))!="2.0")throw Failure(-32600,"Invalid request");
                notification=!value.contains("id");id=value.value("id",Json(nullptr));if(!id.is_null()&&!id.is_number_integer()&&!id.is_string()||id.is_string()&&id.get_ref<const std::string&>().size()>128){id=nullptr;throw Failure(-32600,"Invalid request id");}
                if(!value.contains("method")||!value["method"].is_string())throw Failure(-32600,"Invalid method");auto params=value.value("params",Json::object());if(!params.is_object())throw Failure(-32602,"params must be an object");response={{"result",dispatch(value["method"].get<std::string>(),params)}};
            }catch(const Failure& error){response={{"error",{{"code",error.code},{"message",error.what()}}}};}
            catch(const Json::exception&){response={{"error",{{"code",-32602},{"message","Invalid parameters"}}}};}
            catch(const std::exception&){response={{"error",{{"code",-32000},{"message","Node operation failed"}}}};}
            if(notification)return {};
            response["jsonrpc"]="2.0";response["id"]=id;return response.dump(-1,' ',true);
    }
    void run(){
        std::string line;
        while(true){
            line.clear();char byte;
            while(std::cin.get(byte)){line+=byte;if(line.size()>8192||byte=='\n')break;}
            if(line.empty())break;
            auto response=exchange(line);
            if(!response.empty())std::cout<<response<<std::endl;
            if(line.size()>8192)break;
        }
    }
};
Rpc* create_rpc(){return new Rpc;}
void destroy_rpc(Rpc* rpc){delete rpc;}
std::string exchange_rpc(Rpc* rpc,std::string_view request){return rpc->exchange(request);}
void run_rpc(){Rpc rpc;rpc.run();}
}
