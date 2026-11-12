// SPDX-License-Identifier: AGPL-3.0-or-later
#include "../../bridge/cipher_policy.hpp"
#include <functional>
#include <limits>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>

void add_cipher_policy_tests(std::vector<std::pair<std::string,std::function<void()>>>& cases) {
    cases.emplace_back("cipher_policy_small_packet_guard_and_unavailable_provider", [] {
        const auto require=[](bool ok) { if (!ok) throw std::runtime_error("unsafe cipher preference"); };
        require(bridge::prefer_aes_gcm(100, 1000, 100, 200));
        require(!bridge::prefer_aes_gcm(100, 1000, 300, 200));
        require(!bridge::prefer_aes_gcm(900, 1000, 100, 200));
        require(!bridge::prefer_aes_gcm(0, 1000, 100, 200));
        require(!bridge::prefer_aes_gcm(100, 0, 100, 200));
        require(!bridge::prefer_aes_gcm(std::numeric_limits<double>::quiet_NaN(), 1000, 100, 200));
    });
    cases.emplace_back("cipher_policy_concurrent_handles_share_immutable_selection", [] {
        const bridge::CipherPolicy* first=nullptr; const bridge::CipherPolicy* second=nullptr;
        std::thread a([&]{first=&bridge::cipher_policy();}), b([&]{second=&bridge::cipher_policy();});
        a.join(); b.join();
        if (first!=second) throw std::runtime_error("cipher policy raced");
        auto session=ssh_new();
        if (!session) throw std::bad_alloc();
        const bool ok=bridge::apply_cipher_policy(session,*first);
        ssh_free(session);
        if (!ok) throw std::runtime_error("cipher preference rejected");
    });
}
