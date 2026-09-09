@echo off
rem 栖星 AsterCore · Windows 启动脚本（编译前用 Python 直跑；打包后改为启动 AsterCore.exe）
rem 双击本文件即可：首启向导 → 账号/面板 → 自动打开浏览器

cd /d %~dp0
python -m astercore.launch %*
if errorlevel 1 pause
