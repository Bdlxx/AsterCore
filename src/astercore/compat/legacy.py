# 栖星 AsterCore · 老插件（Linux 版）适配
# 识别依据：老插件的契约是 `handle(event: dict) -> bool`（同步、无 __meta__），
# 与 AsterCore 新 SDK（__meta__ / async）区分开，两者可共存于同一目录。
#
# 适配要点：
#   1. 事件：AsterCore Event → OneBot v11 dict（优先用后端保留的原始报文）
#   2. 同步阻塞：老插件是同步代码（可能发网络请求），放进线程池执行，不阻塞事件循环
#   3. 账号上下文：线程池不继承 contextvars，因此复制上下文后再执行
#   4. 返回值：True=已处理 / False=放行（与内核语义一致）

from __future__ import annotations

import asyncio
import contextvars
import inspect
import logging
from typing import Any, Callable

from astercore.compat import context
from astercore.compat.onebot import event_to_onebot
from astercore.core.models import Event
from astercore.core.plugin import HANDLE_HANDLED, HANDLE_NOT_HANDLED, PluginMeta

log = logging.getLogger("astercore.compat.legacy")


def _has_attr(module: Any, name: str) -> bool:
    return getattr(module, name, None) is not None


def is_legacy_module(module: Any) -> bool:
    """是否为 Linux 版老插件（同步 handle + 无新 SDK 元信息）"""
    if module is None:
        return False
    if _has_attr(module, "__meta__") or _has_attr(module, "META"):
        return False                      # 新 SDK：有元信息
    if getattr(module, "plugin", None) is not None:
        return False                      # 新 SDK：类插件实例
    fn = getattr(module, "handle", None)
    if not callable(fn):
        return False
    if inspect.iscoroutinefunction(fn):
        return False                      # 新 SDK：async handle
    # 老插件 handle 只收一个 event 参数；新式无 meta 的 sync handle 也兼容此处
    try:
        sig = inspect.signature(fn)
        params = [p for p in sig.parameters.values()
                  if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)]
    except (TypeError, ValueError):
        return False
    return 1 <= len(params) <= 2


def legacy_meta(module: Any, fallback_name: str) -> PluginMeta:
    """老插件的元信息：优先用插件自带的 __plugin_*__（Linux 版 SDK 规范），
    其次查内置注册表，最后退回文件名/文档字符串。"""
    name_en = getattr(module, "__plugin_name_en__", None) or fallback_name
    name_cn = getattr(module, "__plugin_name_cn__", None)
    desc = getattr(module, "__plugin_desc__", None)
    version = getattr(module, "__plugin_version__", None)
    author = getattr(module, "__plugin_author__", None)
    if name_cn or desc:
        return PluginMeta(
            name=str(name_en), name_cn=str(name_cn or fallback_name),
            version=str(version or "legacy"), description=str(desc or ""),
            author=str(author or "Linux 版插件"),
        )
    return _legacy_meta_fallback(module, fallback_name)


def _legacy_meta_fallback(module: Any, fallback_name: str) -> PluginMeta:
    """无自带元信息时：注册表 → 文档字符串 → 文件名"""
    from astercore.compat.utils_pkg import PLUGIN_META_DEFAULT
    key = fallback_name
    known = PLUGIN_META_DEFAULT.get(key) or {}
    if not known:
        # 文件名与开关键名不一致时（如 pseudo_persona.py ↔ 键 pseudo）按 name_en 反查
        for k, v in PLUGIN_META_DEFAULT.items():
            if v.get("name_en") == key:
                known = v
                break
    doc = (getattr(module, "__doc__", "") or "").strip().splitlines()
    desc = known.get("description") or (doc[0].strip() if doc else "Linux 版插件")
    return PluginMeta(
        name=key,
        name_cn=known.get("name_cn") or key,
        version=str(getattr(module, "__version__", "") or "legacy"),
        description=desc,
        author="Linux 版插件",
    )


class LegacyPluginAdapter:
    """把老插件包装成内核插件（对外只暴露 async handle）"""

    def __init__(self, module: Any, name: str) -> None:
        self.module = module
        self.name = name
        self.runtime: Any = None      # 由 LoadedPlugin.activate 注入
        self._fn: Callable = getattr(module, "handle")
        self.calls = 0

    # 让面板/加载器读取到的元信息仍然完整
    def __getattr__(self, item: str):     # 透传老插件的其他属性
        return getattr(self.module, item)

    async def handle(self, event: Event) -> bool | str:
        payload = event_to_onebot(event)
        loop = asyncio.get_running_loop()
        token = context.set_current(self.runtime) if self.runtime is not None else None
        try:
            ctx = contextvars.copy_context()   # 线程池不继承上下文，需显式复制
            fn = self._fn

            def _call():
                # 老插件日志里标注来源插件（与 Linux 版一致）
                try:
                    ulog = __import__("utils.log", fromlist=["set_current_plugin"])
                    ulog.set_current_plugin(self.name)
                except Exception:
                    pass
                try:
                    return fn(payload)
                finally:
                    try:
                        ulog = __import__("utils.log", fromlist=["set_current_plugin"])
                        ulog.set_current_plugin(None)
                    except Exception:
                        pass

            ret = await loop.run_in_executor(None, lambda: ctx.run(_call))
            self.calls += 1
            return HANDLE_HANDLED if ret else HANDLE_NOT_HANDLED
        except Exception:
            log.exception("老插件 %s 处理事件异常", self.name)
            return HANDLE_NOT_HANDLED
        finally:
            if token is not None:
                context.reset_current(token)


def make_legacy_adapter(module: Any, name: str) -> LegacyPluginAdapter:
    return LegacyPluginAdapter(module, name)
