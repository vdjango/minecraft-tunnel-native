// SPDX-License-Identifier: AGPL-3.0-or-later
#include "session.hpp"
#include "game_wire.hpp"
namespace bridge {
void Session::publish_game_chat_status(std::string status){
    game_chat_status_=std::move(status);
    event({{"type","bridge"},{"status",game_chat_status_}});
    if(chat_stage_==2&&chat_output_.size()<24000)
        chat_output_+=Json{{"op","bridge"},{"status",game_chat_status_}}.dump(-1,' ',true)+"\n";
}
void Session::start_game_chat(){
    if(role!="host")return;
    if(!game_chat_enabled_){publish_game_chat_status("房主未开启游戏聊天同步，大厅聊天可正常使用");return;}
    if(!game_port_){
        if(game_chat_status_!="等待房主开放世界，大厅聊天可正常使用")
            publish_game_chat_status("等待房主开放世界，大厅聊天可正常使用");
        return;
    }
    if(game_chat_)return;
    publish_game_chat_status("正在连接游戏聊天，大厅聊天可正常使用");
    try{auto protocol=settings_.value("game",Json::object()).value("protocol",0);game_chat_=std::make_unique<GameChat>(game_port_,protocol,game_chat_name_,[this](Json value){post([this,value]{
        if(value.value("type","")=="bridge"){
            publish_game_chat_status(value.at("status").get<std::string>());
        }else if(chat_stage_==2&&chat_output_.size()<24000)chat_output_+=value.dump(-1,' ',true)+"\n";
    });});}catch(...){publish_game_chat_status("此游戏协议暂不支持游戏聊天，大厅聊天仍可使用");}
}
void Session::advance_chat(){
    for(auto it=chat_pending_.begin();it!=chat_pending_.end();)if(Clock::now()>it->second.deadline){it->second.result->set_exception(std::make_exception_ptr(Failure(-32000,"Chat acknowledgement timed out")));it=chat_pending_.erase(it);}else ++it;
    if(chat_stage_==2||Clock::now()<next_chat_)return;
    if(!chat_){chat_=ssh_channel_new(ssh_->get());if(!chat_)return;chat_stage_=0;}
    int result=chat_stage_==0?ssh_channel_open_session(chat_):ssh_channel_request_subsystem(chat_,"buggcraft-chat-v1");
    if(result==SSH_AGAIN)return;
    if(result!=SSH_OK){free_channel(chat_);chat_stage_=0;next_chat_=Clock::now()+std::chrono::seconds(3);return;}
    if(++chat_stage_==2){
        chat_output_=Json{{"op","hello"},{"name",chat_name_}}.dump(-1,' ',true)+"\n";
        // Game login may finish before the chat subsystem opens. Always send
        // the latest status after hello, including after channel reconnects.
        if(role=="host"&&!game_chat_status_.empty())
            chat_output_+=Json{{"op","bridge"},{"status",game_chat_status_}}.dump(-1,' ',true)+"\n";
        chat_input_.clear();
    }
    wake_.notify();
}
Json Session::chat(Json value){
    if(value.empty())return events();
    if(!game::plain_text(value.at("text").get<std::string>()))throw Failure(-32602,"Invalid chat text");
    value["request"]=room_id(random_id()).substr(0,24);
    auto result=std::make_shared<std::promise<Json>>();auto future=result->get_future();
    if(!post([this,result,value]{try{
        if(chat_stage_!=2)throw Failure(-32000,"Chat unavailable");
        if(chat_pending_.size()>=10)throw Failure(-32010,"Too many pending chat messages");
        auto data=value.dump(-1,' ',true)+"\n";if(data.size()>4096||chat_output_.size()+data.size()>32768)throw Failure(-32602,"Chat output limit");
        chat_pending_.emplace(value.at("request").get<std::string>(),ChatRequest{result,Clock::now()+std::chrono::seconds(10)});chat_output_+=data;
    }catch(...){result->set_exception(std::current_exception());}}))throw Failure(-32004,"Session stopped");
    if(future.wait_for(std::chrono::seconds(12))!=std::future_status::ready)throw Failure(-32000,"Chat operation timed out");return future.get();
}
}
