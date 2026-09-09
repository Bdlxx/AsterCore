# 栖星 AsterCore · 插件加载器与事件总线

from __future__ import annotations

import importlib
import importlib.util
import inspect
import logging
import pkgutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

from .models import Event
from .plugin import HANDLE_NOT_HANDLED, Plugin, PluginMeta

log = logging.getLogger("astercore.plugins")


@dataclass(slots=True)
class LoadedPlugin:
    meta: PluginMeta
    module: Any = None            # 模块级插件（函数式）
    instance: Plugin | None = None  # 类插件
    config: dict[str, Any] = field(default_factory=dict)
    enabled: bool = True

    async def handle(self, event: Event) -> bool | str:
        """调用插件处理事件（同步/异步统一 await）"""
        fn = None
        if self.module is not None and hasattr(self.module, "handle"):
            fn = self.module.handle
        elif self.instance is not None:
            fn = self.instance.handle
        if fn is None:
            return HANDLE_NOT_HANDLED
        ret = fn(event)
        if inspect.isawaitable(ret):
            ret = await ret
        return bool(ret) if not isinstance(ret, str) else ret


class PluginLoader:
    """从插件目录加载插件（模块级函数式 + Plugin 类两种形态）。

    - 目录形态：plugins/xxx.py（发布可换成 xxx.pyd，import 优先 pyd）
    - 模块需导出：__meta__: PluginMeta 或 META 字典 + handle(event)
    - 可选导出：plugin: Plugin 实例 / load / unload
    """

    def __init__(self, plugins_dir: str | Path) -> None:
        self.plugins_dir = Path(plugins_dir)
        self.loaded: dict[str, LoadedPlugin] = {}

    def discover_names(self) -> list[str]:
        """扫描目录内可导入模块名（.py / .pyd）"""
        if not self.plugins_dir.exists():
            return []
        names: list[str] = []
        for p in sorted(self.plugins_dir.iterdir()):
            if p.suffix in (".py", ".pyd") and not p.name.startswith("_") and p.name != "__init__.py":
                names.append(p.stem)
        return names

    def load(self, name: str) -> LoadedPlugin | None:
        if name in self.loaded:
            return self.loaded[name]
        try:
            # 优先 .pyd（发布二进制），回退 .py
            spec = None
            for suffix in (".pyd", ".py"):
                f = self.plugins_dir / f"{name}{suffix}"
                if f.exists():
                    spec = importlib.util.spec_from_file_location(
                        f"astercore.plugins.{name}", f
                    )
                    break
            if spec is None or spec.loader is None:
                log.warning("插件 %s 文件不存在", name)
                return None
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)

            meta = self._read_meta(module, name)
            lp = LoadedPlugin(meta=meta, module=module)
            # 若模块导出 plugin 实例（Plugin 子类）→ 用实例形态
            inst = getattr(module, "plugin", None)
            if isinstance(inst, Plugin):
                lp.instance = inst
                lp.module = None  # 类插件优先
            self.loaded[name] = lp
            log.info("已加载插件 %s v%s", meta.name_cn or name, meta.version)
            return lp
        except Exception as e:
            log.exception("插件 %s 加载失败: %s", name, e)
            return None

    def load_all(self) -> list[LoadedPlugin]:
        ok = []
        for name in self.discover_names():
            lp = self.load(name)
            if lp is not None:
                ok.append(lp)
        return ok

    def unload(self, name: str) -> None:
        lp = self.loaded.pop(name, None)
        if lp is None:
            return
        # TODO: 调用 unload 生命周期（v0.1 先支持启停粒度）

    @staticmethod
    def _read_meta(module: Any, fallback_name: str) -> PluginMeta:
        meta = getattr(module, "__meta__", None) or getattr(module, "META", None)
        if isinstance(meta, dict):
            return PluginMeta(
                name=meta.get("name", fallback_name),
                name_cn=meta.get("name_cn", ""),
                version=meta.get("version", "0.1.0"),
                description=meta.get("description", ""),
                author=meta.get("author", ""),
                master_only=bool(meta.get("master_only", False)),
            )
        if isinstance(meta, PluginMeta):
            if not meta.name:
                meta.name = fallback_name
            return meta
        return PluginMeta(name=fallback_name, name_cn=fallback_name)


class EventBus:
    """事件总线：按加载顺序投递事件到插件（首个返回 True 终止链）。"""

    def __init__(self) -> None:
        self.handlers: list[LoadedPlugin] = []

    def set_plugins(self, plugins: Iterable[LoadedPlugin]) -> None:
        self.handlers = [p for p in plugins if p.enabled]

    async def dispatch(self, event: Event) -> str:
        """返回: handled(有插件处理) / passed(全部放行) / failed(某插件返回错误串)"""
        for lp in self.handlers:
            try:
                ret = await lp.handle(event)
            except Exception:
                log.exception("插件 %s 处理事件异常", lp.meta.name)
                continue
            if ret is True:
                return "handled"
            if isinstance(ret, str) and ret == "fallback":
                return "failed"
        return "passed"
