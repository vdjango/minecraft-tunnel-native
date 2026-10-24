// SPDX-License-Identifier: AGPL-3.0-or-later
#include "session.hpp"
namespace bridge {
Stream::~Stream(){
    if(!session.empty())log("stream_closed",{{"session",session},{"player",player},{"stream",trace},{"sent_bytes",sent},{"received_bytes",received},{"duration_ms",std::chrono::duration<double,std::milli>(Clock::now()-created).count()},{"max_stall_ms",max_stall_ms},{"clean_eof",sent_eof&&socket_eof}});
    if(codec)log("compression_summary",{{"session",session},{"player",player},{"stream",trace},{"raw_bytes",codec->raw_bytes},{"wire_bytes",codec->wire_bytes},{"compressed_blocks",codec->compressed_blocks}});
    if(data){data->resetCallbacks();data->close();}free_channel(channel);close_socket(socket);
}
void copy_into(Ring& ring,std::string_view data){if(data.size()>ring.free())throw std::runtime_error("Stream receive budget exceeded");while(!data.empty()){size_t count=std::min(data.size(),ring.writable());std::memcpy(ring.write_ptr(),data.data(),count);ring.produced(count);data.remove_prefix(count);}}
void half_close(NetSocket socket){
#ifdef _WIN32
    shutdown(socket,SD_SEND);
#else
    shutdown(socket,SHUT_WR);
#endif
}
NetSocket connect_game(int port,bool& connecting){
    auto fd=socket(AF_INET,SOCK_STREAM,0);if(fd==invalid_socket)throw std::runtime_error("Game socket unavailable");nonblocking(fd);tune_tcp(fd);
    sockaddr_in address{};address.sin_family=AF_INET;address.sin_addr.s_addr=htonl(INADDR_LOOPBACK);address.sin_port=htons(static_cast<uint16_t>(port));
    connecting=connect(fd,reinterpret_cast<sockaddr*>(&address),sizeof(address))!=0;
#ifdef _WIN32
    bool pending=would_block()||WSAGetLastError()==WSAEINPROGRESS;
#else
    bool pending=would_block()||errno==EINPROGRESS;
#endif
    if(connecting&&!pending){close_socket(fd);throw std::runtime_error("Game connection failed");}return fd;
}
void Session::pump(Stream& stream){
    if(stream.opening){
        const int result=ssh_channel_open_forward(stream.channel,room.c_str(),stream.relay_port,"127.0.0.1",0);
        if(result==SSH_AGAIN){if(Clock::now()-stream.created>std::chrono::seconds(5))stream.dead=true;return;}
        if(result!=SSH_OK){
            if(stream.relay_port==25566){free_channel(stream.channel);stream.channel=ssh_channel_new(ssh_->get());if(!stream.channel)throw std::bad_alloc();stream.relay_port=25565;log("compression_fallback",{{"session",identity},{"reason","channel-open-rejected"}});return;}
            stream.dead=true;log("stream_open_failed",{{"session",identity}});return;
        }
        stream.opening=false;if(stream.relay_port==25566){stream.codec=std::make_unique<Codec>();copy_into(stream.to_wire,"BCZ1");}
        log("game_path",{{"session",identity},{"player",stream.player},{"stream",stream.trace},{"path","relay"},{"compression",bool(stream.codec)}});
    }
    if(stream.connecting){PollFd fd{stream.socket,POLLOUT,0};net_poll(&fd,1,0);if(fd.revents){int error=0;SockLen size=sizeof(error);getsockopt(stream.socket,SOL_SOCKET,SO_ERROR,reinterpret_cast<char*>(&error),&size);if(error){stream.dead=true;return;}stream.connecting=false;}else{if(Clock::now()-stream.created>std::chrono::seconds(3))stream.dead=true;return;}}
    char buffer[65536];
    // Only a bounded quantum per stream, preserving fairness between players.
    if(stream.data){
        if(stream.data->isClosed()){stream.dead=true;return;}
        if(!stream.data->isOpen())return;
        if(!stream.local_eof&&stream.inflight<262144&&stream.data->bufferedAmount()<262144){
            const int count=recv(stream.socket,buffer,static_cast<int>(std::min<size_t>(16384,262144-stream.inflight)),0);
            if(count>0){std::array<std::byte,16385> frame;frame[0]=std::byte{'D'};std::memcpy(frame.data()+1,buffer,static_cast<size_t>(count));stream.data->send(frame.data(),static_cast<size_t>(count)+1);stream.inflight+=static_cast<size_t>(count);stream.sent+=static_cast<size_t>(count);}
            else if(count==0)stream.local_eof=true;else if(!would_block()){stream.dead=true;return;}
        }
    }else{
        // SSH CLOSE can arrive while decoded bytes are still queued for the
        // game socket. Drain them through EOF before releasing the stream.
        if(!stream.local_eof&&stream.to_wire.free()>=65600){int count=recv(stream.socket,buffer,sizeof(buffer),0);if(count>0){std::string_view bytes(buffer,static_cast<size_t>(count));if(stream.codec){stream.codec->encode(bytes,stream.encoded_block);copy_into(stream.to_wire,stream.encoded_block);}else copy_into(stream.to_wire,bytes);stream.sent+=static_cast<size_t>(count);}else if(count==0)stream.local_eof=true;else if(!would_block()){stream.dead=true;return;}}
        if(stream.to_wire.size()){int count=ssh_channel_write(stream.channel,stream.to_wire.read_ptr(),static_cast<uint32_t>(stream.to_wire.readable()));if(count>0)stream.to_wire.consumed(static_cast<size_t>(count));else if(count==SSH_ERROR){stream.dead=true;return;}}
        if(stream.codec){while(stream.to_socket.free()>=65536&&stream.codec->next(stream.decoded_block))copy_into(stream.to_socket,stream.decoded_block);}
        // A full payload frame also carries its length header. With exactly
        // 65536 buffered bytes it may still need a few more bytes to decode.
        // Bound reads by the decoder's actual capacity, not payload length.
        const size_t capacity=stream.codec?std::min(sizeof(buffer),stream.codec->receive_capacity()):sizeof(buffer);
        if(stream.to_socket.free()>=131100&&capacity&&!stream.remote_eof){
            int count=ssh_channel_read_nonblocking(stream.channel,buffer,static_cast<uint32_t>(capacity),0);
            if(count>0){if(stream.codec){stream.codec->feed(std::string_view(buffer,static_cast<size_t>(count)));while(stream.to_socket.free()>=65536&&stream.codec->next(stream.decoded_block))copy_into(stream.to_socket,stream.decoded_block);}else copy_into(stream.to_socket,std::string_view(buffer,static_cast<size_t>(count)));}
            else if(count==SSH_ERROR){stream.dead=true;return;}
            else if(ssh_channel_is_eof(stream.channel)){if(stream.codec&&!stream.codec->complete())throw std::runtime_error("Truncated compressed stream");stream.remote_eof=true;}
        }
    }
    if(stream.to_socket.size()){int count=net_send(stream.socket,stream.to_socket.read_ptr(),static_cast<int>(std::min<size_t>(65536,stream.to_socket.readable())));if(count>0){stream.to_socket.consumed(static_cast<size_t>(count));stream.received+=static_cast<size_t>(count);if(stream.data){std::array<std::byte,5> ack{std::byte{'A'}};auto amount=htonl(static_cast<uint32_t>(count));std::memcpy(ack.data()+1,&amount,4);stream.data->send(ack.data(),ack.size());}}else if(count<0&&!would_block()){stream.dead=true;return;}}
    if(stream.local_eof&&!stream.sent_eof&&!stream.to_wire.size()){
        if(stream.data)stream.data->send(std::string("EOF"));
        else {const int result=ssh_channel_send_eof(stream.channel);if(result==SSH_AGAIN)return;if(result!=SSH_OK){stream.dead=true;return;}}
        stream.sent_eof=true;
    }
    if(stream.remote_eof&&!stream.socket_eof&&!stream.to_socket.size()){half_close(stream.socket);stream.socket_eof=true;}
    if(stream.sent_eof&&stream.socket_eof&&!stream.inflight&&!stream.to_wire.size()&&!stream.to_socket.size())stream.dead=true;
}
}
