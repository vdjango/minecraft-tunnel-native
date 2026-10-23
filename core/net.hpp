#pragma once
#ifdef _WIN32
#include <winsock2.h>
#include <ws2tcpip.h>
#include <mstcpip.h>
using NetSocket = SOCKET;
using PollFd = WSAPOLLFD;
using SockLen = int;
constexpr auto invalid_socket = INVALID_SOCKET;
inline int net_poll(PollFd* fds, size_t count, int timeout) {
    return WSAPoll(fds, static_cast<ULONG>(count), timeout);
}
inline void close_socket(NetSocket fd) { if (fd != invalid_socket) closesocket(fd); }
inline void nonblocking(NetSocket fd) { u_long yes = 1; ioctlsocket(fd, FIONBIO, &yes); }
inline bool would_block() { return WSAGetLastError() == WSAEWOULDBLOCK; }
#else
#include <sys/socket.h>
#include <netdb.h>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <arpa/inet.h>
#include <unistd.h>
#include <fcntl.h>
#include <poll.h>
#include <cerrno>
using NetSocket = int;
using PollFd = pollfd;
using SockLen = socklen_t;
constexpr auto invalid_socket = -1;
inline int net_poll(PollFd* fds, size_t count, int timeout) { return poll(fds, count, timeout); }
inline void close_socket(NetSocket fd) { if (fd != invalid_socket) close(fd); }
inline void nonblocking(NetSocket fd) { fcntl(fd, F_SETFL, fcntl(fd, F_GETFL, 0) | O_NONBLOCK); }
inline bool would_block() { return errno == EAGAIN || errno == EWOULDBLOCK; }
#endif
#include <algorithm>
#include <array>
#include <cstdint>
#include <cstring>
#include <stdexcept>
#include <string>
#include <vector>

inline int net_send(NetSocket fd,const char* data,int size) {
#ifdef MSG_NOSIGNAL
    return static_cast<int>(::send(fd,data,static_cast<size_t>(size),MSG_NOSIGNAL));
#else
    return static_cast<int>(::send(fd,data,size,0));
#endif
}

inline void tune_tcp(NetSocket fd) {
    int yes = 1;
    setsockopt(fd, IPPROTO_TCP, TCP_NODELAY, reinterpret_cast<const char*>(&yes), sizeof(yes));
    setsockopt(fd, SOL_SOCKET, SO_KEEPALIVE, reinterpret_cast<const char*>(&yes), sizeof(yes));
#ifdef _WIN32
    tcp_keepalive keep{1, 15000, 15000};
    DWORD returned = 0;
    WSAIoctl(fd, SIO_KEEPALIVE_VALS, &keep, sizeof(keep), nullptr, 0, &returned, nullptr, nullptr);
#else
    int interval = 15, probes = 3;
#ifdef TCP_KEEPIDLE
    setsockopt(fd, IPPROTO_TCP, TCP_KEEPIDLE, &interval, sizeof(interval));
#elif defined(TCP_KEEPALIVE)
    setsockopt(fd, IPPROTO_TCP, TCP_KEEPALIVE, &interval, sizeof(interval));
#endif
    setsockopt(fd, IPPROTO_TCP, TCP_KEEPINTVL, &interval, sizeof(interval));
    setsockopt(fd, IPPROTO_TCP, TCP_KEEPCNT, &probes, sizeof(probes));
#ifdef SO_NOSIGPIPE
    setsockopt(fd, SOL_SOCKET, SO_NOSIGPIPE, &yes, sizeof(yes));
#endif
#endif
}

// A UDP wakeup pair works on Windows, Linux and macOS; no polling stdin and no
// per-connection thread. Control events wake a sleeping network worker directly.
class Wakeup {
    NetSocket receive_ = invalid_socket, send_ = invalid_socket;
public:
    Wakeup() {
        receive_ = socket(AF_INET, SOCK_DGRAM, 0);
        send_ = socket(AF_INET, SOCK_DGRAM, 0);
        sockaddr_in address{};
        address.sin_family = AF_INET;
        address.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
        if (receive_ == invalid_socket || send_ == invalid_socket ||
            bind(receive_, reinterpret_cast<sockaddr*>(&address), sizeof(address))) {
            close_socket(receive_);
            close_socket(send_);
            throw std::runtime_error("worker wakeup socket unavailable");
        }
        SockLen size = sizeof(address);
        getsockname(receive_, reinterpret_cast<sockaddr*>(&address), &size);
        connect(send_, reinterpret_cast<sockaddr*>(&address), size);
        nonblocking(receive_);
        nonblocking(send_);
    }
    Wakeup(const Wakeup&) = delete;
    ~Wakeup() { close_socket(receive_); close_socket(send_); }
    NetSocket fd() const { return receive_; }
    void notify() { const char byte = 1; send(send_, &byte, 1, 0); }
    void drain() { char data[512]; while (recv(receive_, data, sizeof(data), 0) > 0) {} }
};

class Ring {
    std::vector<char> bytes_;
    size_t head_ = 0, used_ = 0;
public:
    explicit Ring(size_t capacity) : bytes_(capacity) { if (!capacity) throw std::invalid_argument("zero ring capacity"); }
    size_t size() const { return used_; }
    size_t free() const { return bytes_.size() - used_; }
    char* write_ptr() { return bytes_.data() + (head_ + used_) % bytes_.size(); }
    size_t writable() const { return std::min(free(), bytes_.size() - (head_ + used_) % bytes_.size()); }
    const char* read_ptr() const { return bytes_.data() + head_; }
    size_t readable() const { return std::min(used_, bytes_.size() - head_); }
    void produced(size_t count) { if(count>free())throw std::out_of_range("ring overflow");used_ += count; }
    void consumed(size_t count) { if(count>used_)throw std::out_of_range("ring underflow");head_ = (head_ + count) % bytes_.size(); used_ -= count; }
};
