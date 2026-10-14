#!/bin/sh
# 用 MinGW-w64 编译易语言 C 桥（x64）：gcc -shared -O2 -o nap_bridge.dll nap_eyuyan_bridge.c
set -e
cd "$(dirname "$0")"
gcc -shared -O2 -o nap_bridge.dll nap_eyuyan_bridge.c
echo "已生成 nap_bridge.dll"
# 看导出名（MinGW 下 __declspec(dllexport) 会生成 nap_bridge_init 这类无修饰名）
x86_64-w64-mingw32-objdump -p nap_bridge.dll 2>/dev/null | grep -i nap_bridge_ || \
  objdump -p nap_bridge.dll | grep -i nap_bridge_