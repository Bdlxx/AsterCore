# 栖星 AsterCore · 兼容层的账号上下文
# 多账号下，老插件只认 `utils.*`（全局模块），因此需要一个"当前是哪个账号"的
# 判定机制。解析顺序（从可靠到兜底）：
#   1) 事件里的 self_id / account_id —— 事件驱动调用（send_message(event,...)）最可靠
#   2) contextvar —— 分发/加载期间由运行时设置（同步 handler 在线程池里也带着它）
#   3) 唯一在线账号 —— 单账号场景的兜底
# 这样既能支持多账号，又不会让老插件感知到任何变化。

from __future__ import annotations

import contextvars
import logging
from typing import Any

log = logging.getLogger("astercore.compat")

_current: contextvars.ContextVar[Any] = contextvars.ContextVar(
    "astercore_current_runtime", default=None)

# account_id(str) -> runtime；供事件解析用
_runtimes: dict[str, Any] = {}
# 最近一次激活的账号（兜底用）
_last: list[Any] = [None]


def register_runtime(rt: Any) -> None:
    _runtimes[str(getattr(rt, "account_id", ""))] = rt


def unregister_runtime(rt: Any) -> None:
    _runtimes.pop(str(getattr(rt, "account_id", "")), None)
    if _last[0] is rt:
        _last[0] = None


def set_current(rt: Any):
    """设置当前账号上下文，返回 token（用于 reset）"""
    _last[0] = rt
    return _current.set(rt)


def reset_current(token) -> None:
    try:
        _current.reset(token)
    except (ValueError, LookupError):
        pass


def current_runtime() -> Any:
    rt = _current.get()
    if rt is not None:
        return rt
    if _last[0] is not None:
        return _last[0]
    if len(_runtimes) == 1:
        return next(iter(_runtimes.values()))
    return None


def runtime_for_event(event: Any) -> Any:
    """按事件里的账号信息定位运行时（老插件总会把收到的 event 传回来）"""
    if isinstance(event, dict):
        for key in ("account_id", "self_id"):
            v = event.get(key)
            if v is None:
                continue
            rt = _runtimes.get(str(v))
            if rt is not None:
                return rt
    return current_runtime()


def resolve(event: Any = None) -> Any:
    """统一解析入口：有事件先按事件，否则用上下文/唯一账号"""
    rt = runtime_for_event(event) if event is not None else None
    return rt or current_runtime()
