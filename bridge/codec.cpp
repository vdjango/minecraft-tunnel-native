// SPDX-License-Identifier: AGPL-3.0-or-later
#define ZSTD_STATIC_LINKING_ONLY
#include "codec.hpp"
#include <algorithm>
#include <stdexcept>

namespace bridge {
namespace {
void varint(std::string& output, size_t value) {
    do {
        const auto byte = value & 127;
        value >>= 7;
        output += static_cast<char>(byte | (value ? 128 : 0));
    } while (value);
}

bool length(std::string_view data, size_t& offset, size_t& value) {
    value = 0;
    for (unsigned i = 0; i < 3; ++i) {
        if (offset >= data.size()) return false;
        const auto byte = static_cast<unsigned char>(data[offset++]);
        value |= static_cast<size_t>(byte & 127) << (i * 7);
        if (!(byte & 128)) return true;
    }
    throw std::runtime_error("Invalid compression length");
}
}

Codec::~Codec() {
    ZSTD_freeCCtx(compressor_);
    ZSTD_freeDCtx(decompressor_);
}

std::string Codec::encode(std::string_view data) {
    std::string output;
    encode(data, output);
    return output;
}

void Codec::encode(std::string_view data, std::string& output) {
    if (data.empty() || data.size() > 65536) throw std::runtime_error("Invalid compression block");
    output.clear();
    if (send_raw_ || ((raw_bytes >= 2048 || blocks_ >= 64) && wire_bytes >= raw_bytes)) {
        output.reserve(data.size() + (send_raw_ ? 0 : 1));
        if (!send_raw_) output += '\0';
        send_raw_ = true;
        output += data;
    } else {
        std::string_view payload = data;
        bool compressed = false;
        if (skip_) {
            --skip_;
        } else if (data.size() >= 256) {
            if (!compressor_) {
                compressor_ = ZSTD_createCCtx();
                if (!compressor_) throw std::bad_alloc();
            }
            candidate_.resize(ZSTD_compressBound(data.size()));
            const auto size = ZSTD_compressCCtx(compressor_, candidate_.data(), candidate_.size(),
                                               data.data(), data.size(), 1);
            if (ZSTD_isError(size)) throw std::runtime_error("Compression failed");
            if (size + 32 < data.size()) {
                payload = std::string_view(candidate_.data(), size);
                compressed = true;
                misses_ = 0;
                ++compressed_blocks;
            } else if (++misses_ >= 4) {
                skip_ = 31;
            }
        }
        output.reserve(payload.size() + 6);
        varint(output, (payload.size() << 1) | static_cast<size_t>(compressed));
        if (compressed) varint(output, data.size());
        output += payload;
        ++blocks_;
    }
    raw_bytes += data.size();
    wire_bytes += output.size();
}

void Codec::consume(size_t count) {
    consumed_ += count;
    if (consumed_ == pending_.size()) {
        pending_.clear();
        consumed_ = 0;
    }
}

void Codec::feed(std::string_view data) {
    if (data.size() > receive_capacity()) throw std::runtime_error("Compression receive budget exceeded");
    // Compact once per input batch, rather than moving every remaining frame
    // after each decoded block. Physical storage stays within receive_budget.
    if (consumed_ && (consumed_ >= pending_.size() / 2 || pending_.size() + data.size() > receive_budget)) {
        pending_.erase(0, consumed_);
        consumed_ = 0;
    }
    pending_ += data;
}

bool Codec::next(std::string& output) {
    output.clear();
    if (!magic_) {
        if (buffered() < 4) return false;
        if (std::string_view(pending_.data() + consumed_, 4) != "BCZ1")
            throw std::runtime_error("Invalid compression handshake");
        consume(4);
        magic_ = true;
    }
    auto input = std::string_view(pending_).substr(consumed_);
    if (receive_raw_) {
        if (input.empty()) return false;
        const auto size = std::min<size_t>(input.size(), 65536);
        output.assign(input.data(), size);
        consume(size);
        return true;
    }
    if (input.empty()) return false;
    size_t offset = 0, header = 0;
    if (!length(input, offset, header)) return false;
    if (!header) {
        consume(offset);
        receive_raw_ = true;
        return next(output);
    }
    const size_t size = header >> 1;
    const bool compressed = (header & 1) != 0;
    size_t decoded = size;
    if (!size || size > 65536) throw std::runtime_error("Invalid compression block length");
    if (compressed && !length(input, offset, decoded)) return false;
    if (!decoded || decoded > 65536) throw std::runtime_error("Invalid decompression size");
    if (input.size() < offset + size) return false;
    if (compressed) {
        ZSTD_frameHeader frame{};
        const auto result = ZSTD_getFrameHeader(&frame, input.data() + offset, size);
        if (result != 0 || frame.windowSize > 65536 || frame.frameContentSize != decoded ||
            ZSTD_findFrameCompressedSize(input.data() + offset, size) != size)
            throw std::runtime_error("Invalid compressed frame");
        if (!decompressor_) {
            decompressor_ = ZSTD_createDCtx();
            if (!decompressor_) throw std::bad_alloc();
            ZSTD_DCtx_setParameter(decompressor_, ZSTD_d_windowLogMax, 16);
        }
        output.resize(decoded);
        const auto count = ZSTD_decompressDCtx(decompressor_, output.data(), output.size(),
                                              input.data() + offset, size);
        if (ZSTD_isError(count) || count != decoded) throw std::runtime_error("Invalid compressed content");
    } else {
        output.assign(input.data() + offset, size);
    }
    consume(offset + size);
    return true;
}

bool Codec::complete() const { return magic_ && !buffered(); }
}
