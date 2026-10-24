# libcurl may already import ZLIB::ZLIB through vcpkg. The bridge only needs
# zlibstatic; building the shared target also tries to redefine that alias.
set(ZLIB_BUILD_SHARED OFF CACHE BOOL "" FORCE)
set(ZLIB_BUILD_STATIC ON CACHE BOOL "" FORCE)
set(ZLIB_BUILD_EXAMPLES OFF CACHE BOOL "" FORCE)
set(ZLIB_BUILD_TESTING OFF CACHE BOOL "" FORCE)
set(ZLIB_INSTALL OFF CACHE BOOL "" FORCE)
add_subdirectory("${CMAKE_CURRENT_LIST_DIR}/../.deps/zlib-1.3.2" zlib-build EXCLUDE_FROM_ALL)
