# 栖星 AsterCore · 单实例锁
#
# 施工手册 §8.2：双击两次会开两个实例 → 两个进程抢同一个 QQ 账号，非常难查。
#
# 做法：对 <临时目录>/astercore.lock 加**排他锁**（Windows 用 msvcrt，POSIX 用 fcntl）。
#   · 拿不到锁 = 已有实例在跑 → 尽力把它的窗口唤到前台，然后自身退出
#   · 文件本身一直存在没关系，判断依据是"锁"而不是"文件在不在"（进程崩溃后锁自动释放）
#
# 坑（手册 §8.2）：锁必须**持有到进程结束** —— 拿锁对象用完就被 GC 掉的话锁就没了，
# 所以 acquire() 返回的对象要一直引用着（模块级变量或 main 的局部变量都行）。

from __future__ import annotations

import logging
import os
import tempfile

log = logging.getLogger("astercore.singleinstance")

LOCK_NAME = "astercore.lock"


class SingleInstance:
    """排他锁的持有者：只要这个对象活着，锁就在。"""

    def __init__(self, path: str, fh) -> None:
        self.path = path
        self._fh = fh

    def release(self) -> None:
        fh, self._fh = self._fh, None
        if fh is None:
            return
        try:
            if os.name == "nt":
                import msvcrt
                try:
                    fh.seek(0)
                    msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
                except OSError:
                    pass
            else:
                import fcntl
                try:
                    fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
                except OSError:
                    pass
        finally:
            try:
                fh.close()
            except OSError:
                pass


def lock_path(name: str = LOCK_NAME) -> str:
    return os.path.join(tempfile.gettempdir(), name)


def acquire(name: str = LOCK_NAME) -> SingleInstance | None:
    """尝试拿锁；已被占用返回 None。"""
    path = lock_path(name)
    try:
        fh = open(path, "a+")
    except OSError as e:
        log.warning("单实例锁文件无法打开（跳过单实例保护）: %s", e)
        return SingleInstance(path, None)     # 拿不到文件就别拦着用户启动
    try:
        if os.name == "nt":
            import msvcrt
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        try:
            fh.close()
        except OSError:
            pass
        return None
    try:
        fh.seek(0)
        fh.truncate()
        fh.write(str(os.getpid()))
        fh.flush()
    except OSError:
        pass
    return SingleInstance(path, fh)


def try_focus_window(title: str) -> bool:
    """尽力把已有实例的窗口唤到前台（Windows 专用，尽力而为）。

    做不到就返回 False —— 调用方打印一句"程序已经在运行"即可。
    不用 pywin32，纯 ctypes 调 user32：FindWindowW → ShowWindow → SetForegroundWindow。
    """
    if os.name != "nt":
        return False
    try:
        import ctypes
        u32 = ctypes.windll.user32
        hwnd = u32.FindWindowW(None, title)
        if not hwnd:
            return False
        u32.ShowWindow(hwnd, 9)          # SW_RESTORE
        u32.SetForegroundWindow(hwnd)
        return True
    except Exception as e:               # noqa: BLE001 — 唤窗失败绝不能影响启动逻辑
        log.debug("唤回窗口失败: %s", e)
        return False
