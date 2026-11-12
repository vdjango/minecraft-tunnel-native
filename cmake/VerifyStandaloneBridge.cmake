if(NOT EXISTS "${BRIDGE_FILE}")
  message(FATAL_ERROR "Missing standalone bridge: ${BRIDGE_FILE}")
endif()
# Resolve every normal/delay PE dependency; never assume -static succeeded.
file(GET_RUNTIME_DEPENDENCIES EXECUTABLES "${BRIDGE_FILE}"
  RESOLVED_DEPENDENCIES_VAR resolved UNRESOLVED_DEPENDENCIES_VAR unresolved
  PRE_EXCLUDE_REGEXES "api-ms-" "ext-ms-"
  POST_EXCLUDE_REGEXES "[/\\][Ww][Ii][Nn][Dd][Oo][Ww][Ss][/\\]")
if(resolved OR unresolved)
  message(FATAL_ERROR "Standalone bridge still needs non-system DLLs: ${resolved};${unresolved}")
endif()
message(STATUS "Standalone bridge depends only on Windows system libraries")
