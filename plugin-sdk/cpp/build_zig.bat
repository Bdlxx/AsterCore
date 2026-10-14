@echo off
rem 没有 Visual Studio 时用 zig 自带的 C++ 编译器（先 python -m pip install ziglang）
rem 产出：nap_hello_cpp.dll（x64）
setlocal
cd /d "%~dp0"
mkdir build 2>nul
python -m ziglang c++ -target x86_64-windows-gnu -shared -O2 -std=c++11 -fexec-charset=UTF-8 ^
   -Wno-nullability-completeness -I ".." -I "." ^
   -o nap_hello_cpp.dll example\hello_plugin.cpp
if errorlevel 1 (
  echo 编译失败：先确认 python -m ziglang version 能打印版本
  exit /b 1
)
echo 已生成 nap_hello_cpp.dll
python -m ziglang c++ -target x86_64-windows-gnu -O2 -std=c++11 -fexec-charset=UTF-8 ^
   -Wno-nullability-completeness -I ".." -I "." ^
   -o build\nap_sdk_tests.exe tests\test_sdk.cpp
if errorlevel 1 exit /b 1
build\nap_sdk_tests.exe
endlocal
