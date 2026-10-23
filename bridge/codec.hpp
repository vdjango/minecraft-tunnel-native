// SPDX-License-Identifier: AGPL-3.0-or-later
#pragma once
#include <zstd.h>
#include <cstdint>
#include <string>
#include <string_view>
namespace bridge {
// BCZ1: independent bounded frames; compatible with the published Python SDK.
class Codec {
    ZSTD_CCtx* compressor_ = ZSTD_createCCtx();
    ZSTD_DCtx* decompressor_ = ZSTD_createDCtx();
    unsigned misses_ = 0, skip_ = 0, blocks_ = 0;
    bool send_raw_ = false, receive_raw_ = false, magic_ = false;
    std::string pending_;
public:
    static constexpr size_t receive_budget = 131100;
    uint64_t raw_bytes = 0, wire_bytes = 0, compressed_blocks = 0;
    Codec();
    ~Codec();
    Codec(const Codec&) = delete;
    std::string encode(std::string_view data);
    void feed(std::string_view data);
    bool next(std::string& output);
    bool complete() const;
    size_t buffered() const { return pending_.size(); }
    size_t receive_capacity() const { return receive_budget - pending_.size(); }
};
}
