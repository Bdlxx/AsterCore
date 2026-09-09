# 栖星 AsterCore · 环形日志缓冲
# 供 Web 面板/桌面壳实时读取结构化日志（ts/level/source/msg）。
# 通过 logging.Handler 挂到 "astercore" logger 树。

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from typing import Any, Callable


def _source_of(record: logging.LogRecord) -> str:
    """把 logger name 收敛成短来源：astercore.runtime → runtime"""
    parts = record.name.split(".")
    for i in range(len(parts) - 1, 0, -1):
        if parts[i] != "plugins":
            return parts[i]
    return record.name


class LogRing:
    """线程安全环形日志缓冲。"""

    def __init__(self, capacity: int = 2000) -> None:
        self.capacity = capacity
        self._buf: deque[dict[str, Any]] = deque(maxlen=capacity)
        self._lock = threading.Lock()
        self._listeners: list[Callable[[dict[str, Any]], None]] = []

    # ---- 写 ----
    def add(self, level: str, source: str, msg: str, ts: float | None = None) -> dict:
        entry = {
            "ts": ts if ts is not None else time.time(),
            "level": level,
            "source": source,
            "msg": msg,
        }
        with self._lock:
            self._buf.append(entry)
            listeners = list(self._listeners)
        for fn in listeners:
            try:
                fn(entry)
            except Exception:
                pass
        return entry

    # ---- 读 ----
    def recent(self, limit: int = 100) -> list[dict]:
        with self._lock:
            items = list(self._buf)[-limit:]
        return items

    def tail_since(self, ts: float) -> list[dict]:
        """返回 ts 之后的新条目（轮询/SSE 增量用）"""
        with self._lock:
            items = [e for e in self._buf if e["ts"] > ts]
        return items

    def subscribe(self, fn: Callable[[dict[str, Any]], None]) -> None:
        with self._lock:
            self._listeners.append(fn)

    def clear(self) -> None:
        with self._lock:
            self._buf.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._buf)


_LEVEL_MAP = {
    logging.DEBUG: "debug",
    logging.INFO: "info",
    logging.WARNING: "warn",
    logging.ERROR: "error",
    logging.CRITICAL: "fatal",
}


class RingHandler(logging.Handler):
    """把 logging 记录灌入 LogRing。"""

    def __init__(self, ring: LogRing) -> None:
        super().__init__()
        self.ring = ring

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.ring.add(
                level=_LEVEL_MAP.get(record.levelno, "info"),
                source=_source_of(record),
                msg=record.getMessage(),
                ts=record.created,
            )
        except Exception:
            pass


# 全局单例（进程内共享）
ring = LogRing()
_handler = RingHandler(ring)
_handler.setFormatter(logging.Formatter("%(message)s"))


def install(level: int = logging.INFO) -> LogRing:
    """挂到 astercore logger 树（幂等）"""
    root = logging.getLogger("astercore")
    if _handler not in root.handlers:
        root.addHandler(_handler)
        root.setLevel(min(root.level or logging.NOTSET, level) if root.level else level)
    return ring
