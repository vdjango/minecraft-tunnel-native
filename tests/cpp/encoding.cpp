// SPDX-License-Identifier: AGPL-3.0-or-later
#include "../../core/encoding.hpp"
#include "../../core/digest.hpp"
#include <algorithm>
#include <array>
#include <functional>
#include <future>
#include <sstream>
#include <stdexcept>
#include <vector>
#ifdef _WIN32
#include <windows.h>
#else
#include <sys/mman.h>
#include <unistd.h>
#endif
namespace {
using namespace native::encoding;
void require(bool value) {
  if (!value)
    throw std::runtime_error("Encoding invariant failed");
}
template <class F> void rejected(F action) {
  try {
    action();
  } catch (const std::exception &) {
    return;
  }
  throw std::runtime_error("Malformed encoding accepted");
}
constexpr std::array backends{Backend::scalar, Backend::sse2, Backend::avx2,
                              Backend::avx512bw, Backend::neon};
void differential() {
  for (auto backend : backends) {
    if (!available(backend)) {
      rejected([&] { valid_characters("AA", Alphabet::standard, backend); });
      continue;
    }
    for (auto alphabet : {Alphabet::standard, Alphabet::mixed_url}) {
      for (size_t offset = 0; offset < 64; ++offset) {
        for (size_t length :
             {size_t{0}, size_t{1}, size_t{15}, size_t{16}, size_t{17},
              size_t{31}, size_t{32}, size_t{33}, size_t{63}, size_t{64},
              size_t{65}, size_t{127}, size_t{129}, size_t{65536}}) {
          std::string text(length + offset, 'A');
          auto view = std::string_view(text).substr(offset);
          require(valid_characters(view, alphabet, backend));
        }
      }
      // Every possible byte at every vector lane, including signed bytes.
      std::string text(129, 'A');
      for (size_t lane = 0; lane < text.size(); ++lane) {
        for (unsigned byte = 0; byte < 256; ++byte) {
          text[lane] = static_cast<char>(byte);
          const bool expected =
              (byte >= 'A' && byte <= 'Z') || (byte >= 'a' && byte <= 'z') ||
              (byte >= '0' && byte <= '9') || byte == '+' || byte == '/' ||
              (alphabet == Alphabet::mixed_url && (byte == '-' || byte == '_'));
          require(valid_characters(text, alphabet, backend) == expected);
        }
        text[lane] = 'A';
      }
    }
  }
}
void guard_pages() {
#ifdef _WIN32
  SYSTEM_INFO info;
  GetSystemInfo(&info);
  const size_t page = info.dwPageSize;
  auto *memory = static_cast<char *>(VirtualAlloc(
      nullptr, page * 2, MEM_RESERVE | MEM_COMMIT, PAGE_READWRITE));
  if (!memory)
    throw std::runtime_error("VirtualAlloc failed");
  DWORD old;
  if (!VirtualProtect(memory + page, page, PAGE_NOACCESS, &old)) {
    VirtualFree(memory, 0, MEM_RELEASE);
    throw std::runtime_error("VirtualProtect failed");
  }
#else
  const size_t page = static_cast<size_t>(sysconf(_SC_PAGESIZE));
  auto *memory =
      static_cast<char *>(mmap(nullptr, page * 2, PROT_READ | PROT_WRITE,
                               MAP_PRIVATE | MAP_ANONYMOUS, -1, 0));
  if (memory == MAP_FAILED)
    throw std::runtime_error("mmap failed");
  if (mprotect(memory + page, page, PROT_NONE)) {
    munmap(memory, page * 2);
    throw std::runtime_error("mprotect failed");
  }
#endif
  std::fill_n(memory, page, 'A');
  bool correct = true;
  for (auto backend : backends)
    if (available(backend))
      for (size_t length = 0; length <= 257; ++length)
        correct &=
            valid_characters(std::string_view(memory + page - length, length),
                             Alphabet::standard, backend);
  for (size_t length = 0; length <= 257; ++length) {
    const auto view = std::string_view(memory + page - length, length);
    correct &= base64_decode(base64_encode(view)) == view;
    if (length % 4 == 0)
      correct &= base64_decode(view) == std::string(length / 4 * 3, '\0');
  }
#ifdef _WIN32
  VirtualFree(memory, 0, MEM_RELEASE);
#else
  munmap(memory, page * 2);
#endif
  require(correct);
}
void concurrent_codec_roundtrips() {
  std::vector<std::future<bool>> tasks;
  for (unsigned worker = 0; worker < 8; ++worker)
    tasks.emplace_back(std::async(std::launch::async, [worker] {
      for (size_t length = 0; length <= 1024; ++length) {
        std::string input(length, '\0');
        for (size_t i = 0; i < length; ++i)
          input[i] = static_cast<char>(i * 131 + worker * 17 + length);
        std::string expected(4 * ((length + 2) / 3) + 1, '\0');
        const auto count = EVP_EncodeBlock(
            reinterpret_cast<unsigned char *>(expected.data()),
            reinterpret_cast<const unsigned char *>(input.data()),
            static_cast<int>(input.size()));
        expected.resize(static_cast<size_t>(count));
        if (base64_encode(input) != expected ||
            base64_decode(expected) != input)
          return false;
      }
      return true;
    }));
  for (auto &task : tasks)
    require(task.get());
}
void base64_vectors() {
  const std::array<std::string_view, 7> encoded{
      "", "Zg==", "Zm8=", "Zm9v", "Zm9vYg==", "Zm9vYmE=", "Zm9vYmFy"};
  const std::string plain = "foobar";
  for (size_t size = 0; size <= plain.size(); ++size) {
    require(base64_encode(std::string_view(plain).substr(0, size)) ==
            encoded[size]);
    require(base64_decode(encoded[size]) == plain.substr(0, size));
  }
  std::string all_bytes;
  for (unsigned byte = 0; byte < 256; ++byte)
    all_bytes += static_cast<char>(byte);
  require(base64_decode(base64_encode(all_bytes)) == all_bytes);
  DecodeOptions url{Alphabet::mixed_url, true, false, true};
  require(base64_decode(base64_encode(all_bytes, true), url) == all_bytes);
  for (auto text : {"A", "AA=", "A===", "====", "AB==", "AAB=", "AA=A", "AA\nA",
                    "AA A", "AA-A", "AA_A"})
    rejected([&] { base64_decode(text); });
  rejected([&] { base64_decode("Zg==", url); });
  require(base64_decode("Zh", {Alphabet::mixed_url, true, false, false}) ==
          "f");
}
void digests() {
  native::Digest digest(EVP_sha256());
  digest.update("a");
  digest.update("bc");
  const std::string sha256 =
      "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad";
  require(digest.final_hex() == sha256);
  std::istringstream input("abc");
  const auto pair = native::stream_digests(input, true);
  require(pair.first == sha256 &&
          pair.second == "a9993e364706816aba3e25717850c26c9cd0d89d");
  std::istringstream single("abc");
  const auto result = native::stream_digests(single, false);
  require(result.first == sha256 && result.second.empty());
  std::istringstream broken("abc");
  broken.setstate(std::ios::badbit | std::ios::eofbit);
  rejected([&] { native::stream_digests(broken, true); });
}
} // namespace
void add_encoding_tests(
    std::vector<std::pair<std::string, std::function<void()>>> &cases) {
  cases.emplace_back("encoding_concurrent_openssl_differential",
                     concurrent_codec_roundtrips);
  cases.emplace_back("encoding_backend_differential", differential);
  cases.emplace_back("encoding_guard_pages", guard_pages);
  cases.emplace_back("encoding_rfc4648_vectors", base64_vectors);
  cases.emplace_back("digest_known_vectors_and_read_failure", digests);
}
