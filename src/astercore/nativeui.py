# 栖星 AsterCore · 本机提示（原生弹窗）
#
# 为什么：降级到浏览器模式时**不能静默打开浏览器** —— 用户双击的是 exe，
# 突然弹出浏览器窗口会让人困惑。必须先用系统原生弹窗说清楚：
#   · 为什么降级（具体原因）
#   · 面板访问地址（可复制）
#   · 怎么装 WebView2 获得更好体验
#
# 用 ctypes 调 user32.MessageBoxW：不引入任何依赖、不会因为缺 DLL 而失败。
# 非 Windows（开发/CI）只打印到 stderr —— **绝不能阻塞**，否则自动化会挂住。

from __future__ import annotations

import logging
import os
import sys

log = logging.getLogger("astercore.nativeui")

# Win32 MessageBox 常量
MB_OK = 0x0
MB_YESNO = 0x4
MB_ICONERROR = 0x10
MB_ICONWARNING = 0x30
MB_ICONINFORMATION = 0x40
MB_TOPMOST = 0x40000
MB_SETFOREGROUND = 0x10000
IDYES = 6

_ICONS = {"info": MB_ICONINFORMATION, "warning": MB_ICONWARNING,
          "error": MB_ICONERROR}


def dialogs_disabled() -> bool:
    """无头/自动化环境下不弹窗（否则 e2e 会卡在一个没人点的对话框上）"""
    return bool(os.environ.get("ASTER_NO_DIALOG") or os.environ.get("ASTER_NO_OPEN"))


def message_box(title: str, text: str, kind: str = "info") -> bool:
    """弹一个原生提示框。返回 True 表示"用户看过并确认了"。

    非 Windows / 禁用了弹窗：打到 stderr 并直接返回 True（调用方逻辑照常继续）。
    """
    if dialogs_disabled():
        log.info("[弹窗已禁用] %s: %s", title, text)
        return True
    if os.name != "nt":
        print(f"\n[{title}]\n{text}\n", file=sys.stderr, flush=True)
        return True
    try:
        import ctypes
        flags = MB_OK | MB_TOPMOST | MB_SETFOREGROUND | _ICONS.get(kind, MB_ICONINFORMATION)
        ctypes.windll.user32.MessageBoxW(None, str(text), str(title), flags)
        return True
    except Exception as e:                        # noqa: BLE001 — 弹窗失败不能拖垮启动
        log.warning("原生弹窗失败（改为控制台输出）: %s", e)
        print(f"\n[{title}]\n{text}\n", file=sys.stderr, flush=True)
        return True


def ask_yes_no(title: str, text: str, kind: str = "warning") -> bool:
    """是/否 弹窗。无窗口环境返回 False（**默认不选"是"**，避免自动化里误触发）"""
    if dialogs_disabled():
        log.info("[弹窗已禁用] %s: %s → 默认否", title, text)
        return False
    if os.name != "nt":
        print(f"\n[{title}]\n{text}\n(非 Windows：默认「否」)\n", file=sys.stderr, flush=True)
        return False
    try:
        import ctypes
        flags = MB_YESNO | MB_TOPMOST | MB_SETFOREGROUND | _ICONS.get(kind, MB_ICONWARNING)
        return ctypes.windll.user32.MessageBoxW(None, str(text), str(title), flags) == IDYES
    except Exception as e:                        # noqa: BLE001
        log.warning("原生弹窗失败: %s", e)
        return False
