# Optional link profile only. The engine, protocol and ISA dispatch stay shared.
if(BUGGCRAFT_STANDALONE_BRIDGE)
  if(NOT WIN32 OR NOT BUGGCRAFT_BRIDGE)
    message(FATAL_ERROR "The standalone bridge profile currently requires Windows and BUGGCRAFT_BRIDGE=ON")
  endif()
  set(OPENSSL_USE_STATIC_LIBS TRUE)
  set(CURL_USE_STATIC_LIBS TRUE)
  if(MSVC)
    set(CMAKE_MSVC_RUNTIME_LIBRARY "MultiThreaded$<$<CONFIG:Debug>:Debug>")
    add_compile_options(/Gy /Gw)
    add_link_options(/OPT:REF /OPT:ICF)
  elseif(MINGW)
    add_compile_options(-ffunction-sections -fdata-sections)
    add_link_options(-static -static-libgcc -static-libstdc++ -Wl,--gc-sections)
  else()
    message(FATAL_ERROR "Use an MSVC or MinGW Windows toolchain for the standalone bridge")
  endif()
endif()

function(buggcraft_embed_bridge_notices target)
  if(MSVC)
    target_compile_options(${target} PRIVATE /utf-8)
  endif()
  # Some OpenSSL static distributions enable certificate zlib support but do not
  # publish that dependency. Reuse the existing bridge zlib, after libcrypto.
  target_link_libraries(OpenSSL::Crypto INTERFACE zlibstatic)
  # Embed notices as ordinary read-only data; never unpack files or load a DLL.
  set(notices "${CMAKE_CURRENT_SOURCE_DIR}/THIRD_PARTY_NOTICES.md" "${CMAKE_CURRENT_SOURCE_DIR}/LICENSE")
  file(GLOB dependency_notices CONFIGURE_DEPENDS "${CMAKE_CURRENT_SOURCE_DIR}/licenses/*.txt")
  list(APPEND notices ${dependency_notices}
    "${CMAKE_CURRENT_SOURCE_DIR}/.deps/libssh-0.12.2/COPYING"
    "${CMAKE_CURRENT_SOURCE_DIR}/.deps/base64-0.5.2/LICENSE")
  find_file(standalone_openssl_notice NAMES LICENSE.txt LICENSE LICENSE.md copyright
    PATHS "${OPENSSL_INCLUDE_DIR}/../share/openssl" "${OPENSSL_INCLUDE_DIR}/.."
    NO_DEFAULT_PATH)
  if(NOT standalone_openssl_notice AND EXISTS "${CMAKE_CURRENT_SOURCE_DIR}/licenses/openssl-Apache-2.0.txt")
    set(standalone_openssl_notice "${CMAKE_CURRENT_SOURCE_DIR}/licenses/openssl-Apache-2.0.txt")
  endif()
  if(NOT standalone_openssl_notice)
    message(FATAL_ERROR "Set standalone_openssl_notice to the OpenSSL distribution license")
  endif()
  list(APPEND notices "${standalone_openssl_notice}")
  if(MINGW)
    get_filename_component(compiler_bin "${CMAKE_CXX_COMPILER}" DIRECTORY)
    set(BUGGCRAFT_RUNTIME_NOTICES "${compiler_bin}/../licenses/gcc/COPYING.RUNTIME;${compiler_bin}/../licenses/gcc/COPYING3;${compiler_bin}/../licenses/mingw-w64/COPYING;${compiler_bin}/../licenses/mingw-w64/COPYING.MinGW-w64-runtime.txt;${compiler_bin}/../licenses/winpthreads/COPYING" CACHE STRING "Static compiler runtime notices")
    list(APPEND notices ${BUGGCRAFT_RUNTIME_NOTICES})
  endif()
  list(REMOVE_DUPLICATES notices)
  set(content "")
  foreach(notice IN LISTS notices)
    set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS "${notice}")
    file(READ "${notice}" text)
    get_filename_component(name "${notice}" NAME)
    string(APPEND content "\n===== ${name} =====\n${text}\n")
  endforeach()
  set(header "${CMAKE_CURRENT_BINARY_DIR}/generated/bridge_notices.hpp")
  # A byte array avoids MSVC's string-literal size limit and preserves UTF-8
  # without depending on the compiler's source execution character set.
  string(HEX "${content}" notice_hex)
  string(REGEX REPLACE "(..)" "0x\\1,\n" notice_bytes "${notice_hex}")
  file(WRITE "${header}" "#pragma once\ninline constexpr unsigned char bridge_notices[] = {\n${notice_bytes}};\n")
  target_include_directories(${target} PRIVATE "${CMAKE_CURRENT_BINARY_DIR}/generated")
  target_compile_definitions(${target} PRIVATE BUGGCRAFT_EMBEDDED_NOTICES)
endfunction()

function(buggcraft_finish_standalone_bridge target)
  add_custom_command(TARGET ${target} POST_BUILD
    COMMAND "${CMAKE_COMMAND}" "-DBRIDGE_FILE=$<TARGET_FILE:${target}>"
      -P "${CMAKE_CURRENT_SOURCE_DIR}/cmake/VerifyStandaloneBridge.cmake"
    VERBATIM)
endfunction()
