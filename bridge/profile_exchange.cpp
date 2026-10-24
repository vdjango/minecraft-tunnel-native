// SPDX-License-Identifier: AGPL-3.0-or-later
#include "profile_exchange.hpp"
#include <curl/curl.h>
#include <openssl/evp.h>
#include <algorithm>
#include <cctype>
#include <memory>
#include <mutex>

namespace bridge {
namespace {
constexpr size_t max_response = 32768;

std::string decode_base64url(std::string value) {
    if (value.empty() || value.size() > 2048 || value.size() % 4 == 1)
        throw Failure(-32602, "Invalid access code");
    for (char& c : value) {
        if (c == '-') c = '+';
        else if (c == '_') c = '/';
        else if (!((c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') ||
                   (c >= '0' && c <= '9') || c == '+' || c == '/'))
            throw Failure(-32602, "Invalid access code");
    }
    while (value.size() % 4) value += '=';
    std::string result(value.size() / 4 * 3, '\0');
    const int count = EVP_DecodeBlock(reinterpret_cast<unsigned char*>(result.data()),
        reinterpret_cast<const unsigned char*>(value.data()), static_cast<int>(value.size()));
    if (count < 0) throw Failure(-32602, "Invalid access code");
    int length = count;
    if (value.back() == '=') --length;
    if (value.size() > 1 && value[value.size() - 2] == '=') --length;
    result.resize(static_cast<size_t>(length));
    return result;
}

size_t append_response(char* data, size_t size, size_t count, void* target) {
    if (size && count > max_response / size) return 0;
    const size_t length = size * count;
    auto& response = *static_cast<std::string*>(target);
    if (response.size() > max_response || length > max_response - response.size()) return 0;
    try {
        response.append(data, length);
    } catch (...) {
        return 0;
    }
    return length;
}

std::string issuer_url(const std::string& encoded) {
    auto url = decode_base64url(encoded);
    if (url.size() > 1024 || std::any_of(url.begin(), url.end(), [](unsigned char c) { return c <= 32 || c == 127; }))
        throw Failure(-32602, "Invalid access code issuer");
    CURLU* parts = curl_url();
    if (!parts) throw std::bad_alloc();
    const auto cleanup = std::unique_ptr<CURLU, decltype(&curl_url_cleanup)>(parts, curl_url_cleanup);
    char* scheme = nullptr;
    char* host = nullptr;
    char* path = nullptr;
    char* user = nullptr;
    char* query = nullptr;
    char* fragment = nullptr;
    const bool parsed = curl_url_set(parts, CURLUPART_URL, url.c_str(), 0) == CURLUE_OK &&
        curl_url_get(parts, CURLUPART_SCHEME, &scheme, 0) == CURLUE_OK &&
        curl_url_get(parts, CURLUPART_HOST, &host, 0) == CURLUE_OK;
    const bool has_path = curl_url_get(parts, CURLUPART_PATH, &path, 0) == CURLUE_OK;
    const bool has_user = curl_url_get(parts, CURLUPART_USER, &user, 0) == CURLUE_OK;
    const bool has_query = curl_url_get(parts, CURLUPART_QUERY, &query, 0) == CURLUE_OK;
    const bool has_fragment = curl_url_get(parts, CURLUPART_FRAGMENT, &fragment, 0) == CURLUE_OK;
    const bool valid = parsed && std::string(scheme) == "https" && host && *host &&
        (!has_path || std::string(path) == "/") && !has_user && !has_query && !has_fragment && url.size() <= 1024;
    curl_free(scheme); curl_free(host); curl_free(path); curl_free(user); curl_free(query); curl_free(fragment);
    if (!valid) throw Failure(-32602, "Invalid access code issuer");
    while (!url.empty() && url.back() == '/') url.pop_back();
    return url;
}

template<typename Value>
void set_option(CURL* handle, CURLoption option, Value value) {
    if (curl_easy_setopt(handle, option, value) != CURLE_OK)
        throw Failure(-32032, "Access code exchange unavailable");
}
}

Json redeem_profile_code(const std::string& code) {
    if (code.size() > 4096 || !code.starts_with("buggnode2-"))
        throw Failure(-32602, "Invalid access code");
    auto payload = code.substr(10);
    std::string delegation_key;
    if (const auto separator = payload.find('~'); separator != std::string::npos) {
        delegation_key = payload.substr(separator + 1);
        payload.resize(separator);
        if (delegation_key.size() < 32 || delegation_key.size() > 128 ||
            delegation_key.find_first_not_of("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_") != std::string::npos)
            throw Failure(-32602, "Invalid access code");
    }
    const auto separator = payload.find('.');
    if (separator == std::string::npos || payload.find('.', separator + 1) != std::string::npos)
        throw Failure(-32602, "Invalid access code");
    const auto issuer = issuer_url(payload.substr(0, separator));
    const auto reference = payload.substr(separator + 1);
    if (reference.size() < 32 || reference.size() > 64 ||
        reference.find_first_not_of("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_") != std::string::npos)
        throw Failure(-32602, "Invalid access code");

    static std::once_flag curl_init;
    static CURLcode init_result = CURLE_FAILED_INIT;
    std::call_once(curl_init, [] { init_result = curl_global_init(CURL_GLOBAL_DEFAULT); });
    if (init_result != CURLE_OK) throw Failure(-32032, "Access code exchange unavailable");
    using CurlPtr = std::unique_ptr<CURL, decltype(&curl_easy_cleanup)>;
    CurlPtr handle(curl_easy_init(), curl_easy_cleanup);
    if (!handle) throw std::bad_alloc();
    std::string response;
    const auto body = Json{{"reference", reference}}.dump();
    curl_slist* headers = nullptr;
    headers = curl_slist_append(headers, "Content-Type: application/json");
    if (!headers) throw std::bad_alloc();
    const auto free_headers = std::unique_ptr<curl_slist, decltype(&curl_slist_free_all)>(headers, curl_slist_free_all);
    const auto endpoint = issuer + "/api/profile/redeem";
    set_option(handle.get(), CURLOPT_URL, endpoint.c_str());
    set_option(handle.get(), CURLOPT_PROTOCOLS_STR, "https");
    set_option(handle.get(), CURLOPT_FOLLOWLOCATION, 0L);
    set_option(handle.get(), CURLOPT_CONNECTTIMEOUT_MS, 5000L);
    set_option(handle.get(), CURLOPT_TIMEOUT_MS, 10000L);
    set_option(handle.get(), CURLOPT_SSL_VERIFYPEER, 1L);
    set_option(handle.get(), CURLOPT_SSL_VERIFYHOST, 2L);
#if defined(_WIN32) && defined(CURLSSLOPT_NATIVE_CA)
    set_option(handle.get(), CURLOPT_SSL_OPTIONS, static_cast<long>(CURLSSLOPT_NATIVE_CA));
#endif
    set_option(handle.get(), CURLOPT_HTTPHEADER, headers);
    set_option(handle.get(), CURLOPT_POST, 1L);
    set_option(handle.get(), CURLOPT_POSTFIELDS, body.data());
    set_option(handle.get(), CURLOPT_POSTFIELDSIZE, static_cast<long>(body.size()));
    set_option(handle.get(), CURLOPT_WRITEFUNCTION, append_response);
    set_option(handle.get(), CURLOPT_WRITEDATA, &response);
    const auto result = curl_easy_perform(handle.get());
    long status = 0;
    curl_easy_getinfo(handle.get(), CURLINFO_RESPONSE_CODE, &status);
    if (result != CURLE_OK || status != 200) throw Failure(-32032, "Access code exchange failed");
    try {
        auto profile = Json::parse(response).at("profile");
        if (!delegation_key.empty()) profile["token"] = profile.at("token").get<std::string>() + "~" + delegation_key;
        return profile;
    } catch (const Failure&) { throw; }
    catch (...) { throw Failure(-32032, "Access code exchange failed"); }
}
}
