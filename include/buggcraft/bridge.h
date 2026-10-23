/* SPDX-License-Identifier: AGPL-3.0-or-later */
#ifndef BUGGCRAFT_BRIDGE_H
#define BUGGCRAFT_BRIDGE_H
#include <stddef.h>
#include <stdint.h>
#if defined(_WIN32)
# if defined(BUGGCRAFT_BRIDGE_EXPORTS)
#  define BUGGCRAFT_API __declspec(dllexport)
# else
#  define BUGGCRAFT_API __declspec(dllimport)
# endif
# define BUGGCRAFT_CALL __cdecl
#else
# define BUGGCRAFT_API __attribute__((visibility("default")))
# define BUGGCRAFT_CALL
#endif
#ifdef __cplusplus
extern "C" {
#endif
typedef struct buggcraft_bridge buggcraft_bridge;
enum buggcraft_bridge_status {
    BUGGCRAFT_OK = 0,
    BUGGCRAFT_INVALID_ARGUMENT = 1,
    BUGGCRAFT_INITIALIZATION_FAILED = 2,
    BUGGCRAFT_INTERNAL_ERROR = 3
};
BUGGCRAFT_API uint32_t BUGGCRAFT_CALL buggcraft_bridge_abi_version(void);
BUGGCRAFT_API int32_t BUGGCRAFT_CALL buggcraft_bridge_create(buggcraft_bridge** bridge);
/* UTF-8 JSON-RPC, maximum 8192 request bytes. Responses belong to this library.
 * Calls on one handle are serialized. Different handles may run concurrently.
 * JSON-RPC failures are returned as JSON with status BUGGCRAFT_OK.
 * Notifications return NULL/0. Do not race destroy against calls or unload the
 * library before destroying every handle and freeing every response. */
BUGGCRAFT_API int32_t BUGGCRAFT_CALL buggcraft_bridge_call(
    buggcraft_bridge* bridge, const char* request, size_t request_size,
    char** response, size_t* response_size);
BUGGCRAFT_API void BUGGCRAFT_CALL buggcraft_bridge_free(void* response);
BUGGCRAFT_API void BUGGCRAFT_CALL buggcraft_bridge_destroy(buggcraft_bridge* bridge);
#ifdef __cplusplus
}
#endif
#endif
