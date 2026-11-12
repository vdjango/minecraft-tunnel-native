// SPDX-License-Identifier: AGPL-3.0-or-later
#pragma once
#include <libssh/libssh.h>

namespace bridge {
// Process-wide, immutable after first use. EVP owns CPU/OS dispatch; probes use
// synthetic bytes only. The SSH session and all key material stay in libssh.
struct CipherPolicy {
    const char* name = "library";
    const char* preferred = "";
    double aes_bulk_ns = 0, chacha_bulk_ns = 0;
    double aes_small_ns = 0, chacha_small_ns = 0;
    double probe_ms = 0;
};
bool prefer_aes_gcm(double aes_bulk, double chacha_bulk, double aes_small, double chacha_small);
const CipherPolicy& cipher_policy();
// Called before connect on the session's owner thread; preserves the library's
// allowed fallback list. Returns false if the library rejects the preference.
bool apply_cipher_policy(ssh_session session, const CipherPolicy& policy);
}
