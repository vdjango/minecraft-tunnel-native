cmake_minimum_required(VERSION 3.24)
project(buggcraft_bridge_curl LANGUAGES C)

# This is a standalone dependency build, never included by the node build.
# Use the same OpenSSL installation/toolchain as libssh and libdatachannel.
if(NOT CMAKE_BUILD_TYPE AND NOT CMAKE_CONFIGURATION_TYPES)
  set(CMAKE_BUILD_TYPE MinSizeRel CACHE STRING "Build configuration" FORCE)
endif()
if(MSVC)
  add_compile_options(/Gy /Gw)
  add_link_options(/OPT:REF /OPT:ICF)
else()
  add_compile_options(-ffunction-sections -fdata-sections)
  if(APPLE)
    add_link_options(-Wl,-dead_strip)
  else()
    add_link_options(-Wl,--gc-sections)
  endif()
endif()

set(BUILD_SHARED_LIBS ON CACHE BOOL "Build the shared HTTP dependency")
set(BUILD_STATIC_LIBS OFF CACHE BOOL "Build the static HTTP dependency")
foreach(feature BUILD_CURL_EXE BUILD_TESTING BUILD_LIBCURL_DOCS BUILD_MISC_DOCS
    ENABLE_CURL_MANUAL CURL_USE_SCHANNEL CURL_USE_PKGCONFIG CURL_ZLIB CURL_BROTLI
    CURL_ZSTD USE_NGHTTP2 USE_NGTCP2 USE_LIBIDN2 CURL_USE_LIBPSL CURL_USE_LIBSSH2
    CURL_USE_LIBSSH)
  set(${feature} OFF CACHE BOOL "Bridge HTTPS-only dependency profile" FORCE)
endforeach()
set(CURL_USE_OPENSSL ON CACHE BOOL "Reuse the bridge OpenSSL backend" FORCE)
set(CURL_DROP_UNUSED ON CACHE BOOL "Omit disabled API implementations" FORCE)
set(ENABLE_IPV6 ON CACHE BOOL "Retain dual-stack connectivity" FORCE)
if(WIN32)
  set(USE_WIN32_IDN ON CACHE BOOL "Use Windows IDN support" FORCE)
endif()
foreach(feature ALTSVC DOH COOKIES HSTS NETRC MIME FORM_API WEBSOCKETS VERBOSE_STRINGS
    DICT FILE FTP GOPHER IMAP LDAP LDAPS MQTT POP3 IPFS RTSP SMTP TELNET TFTP)
  set(CURL_DISABLE_${feature} ON CACHE BOOL "Unused by bridge HTTPS JSON exchange" FORCE)
endforeach()
# Keep HTTP(S) proxies, threaded DNS, certificate verification and custom CA support.
# Do not apply this profile to the launcher's general-purpose resource downloader.
include(FetchContent)
FetchContent_Declare(bridge_curl_source
  URL https://curl.se/download/curl-8.23.0.tar.xz
  URL_HASH SHA256=8dadd7d2536a314f982ad05a651b713f426bf7d02cc2c76cffb9796f2218e6c5
  DOWNLOAD_EXTRACT_TIMESTAMP TRUE)
FetchContent_MakeAvailable(bridge_curl_source)
