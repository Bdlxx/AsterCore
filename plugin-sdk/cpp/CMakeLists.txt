# 栖星 AsterCore · C++ 插件 SDK（INTERFACE 库：只带头文件路径与 /utf-8 之类的编译选项）
#
# 用法（在你的插件工程里）：
#   add_subdirectory(plugin-sdk/cpp)              # 或 find_package / 直接 include
#   target_link_libraries(你的插件 PRIVATE nap_sdk)
#
# 单独构建本目录（会顺带编出示例 DLL 与 SDK 自测）：
#   cmake -S plugin-sdk/cpp -B build-cpp
#   cmake --build build-cpp --config Release
#   ctest --test-dir build-cpp --output-on-failure
cmake_minimum_required(VERSION 3.15)
project(astercore_nap_cpp_sdk CXX)

set(CMAKE_CXX_STANDARD 11)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

# 必须都是 x64（AsterCore 是 64 位进程）
if(MSVC)
  add_compile_options(/utf-8)                 # ⚠ 中文字面量走 UTF-8，别省
  if(CMAKE_SIZEOF_VOID_P EQUAL 4)
    message(FATAL_ERROR "要编 x64（AsterCore 是 64 位进程，32 位 DLL 加载必失败）")
  endif()
else()
  add_compile_options(-finput-charset=UTF-8 -fexec-charset=UTF-8)
endif()

# MinGW：必须把运行库静态链进去（-static）。只用 -static-libgcc -static-libstdc++ 时，
# posix 线程模型的 GCC 仍会拖 libwinpthread-1.dll → DLL 加载报"找不到模块或它的依赖" ✗
if(MINGW)
  add_link_options(-static)
endif()

add_library(nap_sdk INTERFACE)
target_include_directories(nap_sdk INTERFACE
  ${CMAKE_CURRENT_SOURCE_DIR}          # nap_plugin.hpp
  ${CMAKE_CURRENT_SOURCE_DIR}/..)      # nap_plugin.h（ABI 头）
set_property(TARGET nap_sdk PROPERTY INTERFACE_COMPILE_FEATURES cxx_std_11)

# --- 示例插件（产出 nap_hello_cpp.dll）---
add_library(nap_hello_cpp SHARED example/hello_plugin.cpp)
target_link_libraries(nap_hello_cpp PRIVATE nap_sdk)

# --- SDK 自测（可执行，ctest 直接跑）---
option(NAP_BUILD_TESTS "编 SDK 自带单元测试" ON)
if(NAP_BUILD_TESTS)
  enable_testing()
  add_executable(nap_sdk_tests tests/test_sdk.cpp)
  target_link_libraries(nap_sdk_tests PRIVATE nap_sdk)
  add_test(NAME nap_sdk_tests COMMAND nap_sdk_tests)
endif()
