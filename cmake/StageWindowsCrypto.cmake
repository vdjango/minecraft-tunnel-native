function(buggcraft_stage_windows_crypto target)
  if(native_crypto_runtime)
    set(crypto_files ${native_crypto_runtime})
    if(native_crypto_runtime_debug)
      set(crypto_files
        "$<$<CONFIG:Debug>:${native_crypto_runtime_debug}>"
        "$<$<NOT:$<CONFIG:Debug>>:${native_crypto_runtime}>")
    endif()
    add_custom_command(TARGET ${target} POST_BUILD
      COMMAND ${CMAKE_COMMAND} -E copy_if_different
        "${crypto_files}" "$<TARGET_FILE_DIR:${target}>"
      COMMAND_EXPAND_LISTS VERBATIM)
  endif()
endfunction()

function(buggcraft_install_windows_crypto)
  if(native_crypto_runtime)
    set(crypto_files ${native_crypto_runtime})
    if(native_crypto_runtime_debug)
      set(crypto_files
        "$<$<CONFIG:Debug>:${native_crypto_runtime_debug}>"
        "$<$<NOT:$<CONFIG:Debug>>:${native_crypto_runtime}>")
    endif()
    # Dependency scanning can resolve transitive OpenSSL imports to System32,
    # which is intentionally excluded. Install the selected toolchain's DLLs
    # explicitly so the package does not depend on the build machine.
    install(FILES "${crypto_files}" DESTINATION bin)
  endif()
endfunction()
