# 栖星 AsterCore · 统一路径解析
# 解决"双击 exe / 从别处启动时数据目录乱跑"的问题：
#   - 打包运行（PyInstaller）：基准目录 = astercore.exe 所在目录
#   - 源码运行：基准目录 = 当前工作目录
# 所有默认目录（data/ accounts/ plugins/）都相对基准目录，用户解压即用。

from __future__ import annotations

import os
import sys
from pathlib import Path


def is_frozen() -> bool:
    """是否运行在 PyInstaller 打包环境中"""
    return bool(getattr(sys, "frozen", False))


def app_base_dir() -> Path:
    """应用基准目录（数据/账号/插件的根）"""
    env = os.environ.get("ASTERCORE_HOME")
    if env:
        return Path(env).expanduser().resolve()
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path.cwd().resolve()


def default_data_dir() -> Path:
    return app_base_dir() / "data"


def default_accounts_dir() -> Path:
    return app_base_dir() / "accounts"


def default_plugins_dir() -> Path:
    """用户共享插件目录（放 .py/.pyd/.dll 即被加载）"""
    return app_base_dir() / "plugins"


def plugin_templates_dir() -> Path:
    """内置示例插件模板目录（随包分发，用于首启播种）"""
    return Path(__file__).resolve().parent / "plugin_templates"


def resolve_data_dir(arg: str | None) -> Path:
    """命令行未指定时用默认基准目录"""
    return Path(arg).expanduser() if arg else default_data_dir()


def resolve_accounts_dir(arg: str | None) -> Path:
    return Path(arg).expanduser() if arg else default_accounts_dir()
