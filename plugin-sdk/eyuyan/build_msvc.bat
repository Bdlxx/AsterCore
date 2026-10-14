@echo off
rem 编译易语言 C 桥（需要 Visual Studio 的开发者命令行；x64）
rem 用法：在「x64 Native Tools Command Prompt for VS」里运行本脚本
setlocal
cd /d "%~dp0"
cl /nologo /LD /O2 /W3 /Fe:nap_bridge.dll nap_eyuyan_bridge.c
if errorlevel 1 (
  echo 编译失败：确认用的是 x64 工具链（cl 的默认目标应为 x64）
  exit /b 1
)
echo 已生成 nap_bridge.dll
rem 看一眼导出名（应为 nap_bridge_init / nap_bridge_log / nap_bridge_action / ...）
dumpbin /exports nap_bridge.dll | findstr /i "nap_bridge_"
endlocal