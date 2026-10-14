@echo off
rem 用 MSVC 编示例插件（请在「x64 Native Tools Command Prompt for VS」里运行）
rem 产出：nap_hello_cpp.dll（x64；/utf-8 必开，/MT 静态链接运行库）
setlocal
cd /d "%~dp0"
cl /nologo /LD /O2 /W3 /MT /EHsc /std:c++14 /utf-8 /I ".." /I "." ^
   /Fe:nap_hello_cpp.dll /Fo:build\ /Fd:build\nap_hello_cpp.pdb example\hello_plugin.cpp
if errorlevel 1 (
  echo 编译失败：确认用的是 **x64** 工具链（cl 默认目标应为 x64）
  exit /b 1
)
echo 已生成 nap_hello_cpp.dll
rem 顺手编 SDK 自测并跑一遍
cl /nologo /O2 /W3 /MT /EHsc /std:c++14 /utf-8 /I ".." /I "." ^
   /Fe:build\nap_sdk_tests.exe /Fo:build\ tests\test_sdk.cpp
if errorlevel 1 exit /b 1
build\nap_sdk_tests.exe
endlocal
