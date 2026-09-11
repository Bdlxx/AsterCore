# 栖星 AsterCore · 插件加载器与事件总线

from __future__ import annotations

import importlib
import importlib.util
import sys
import inspect
import logging
import pkgutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

from .models import Event
from .plugin import HANDLE_NOT_HANDLED, Plugin, PluginContext, PluginMeta

log = logging.getLogger("astercore.plugins")


def _compat_legacy():
    """延迟导入 Linux 版兼容层（仅在遇到老插件时用到）"""
    from astercore.compat.legacy import (is_legacy_module, legacy_meta,
                                        make_legacy_adapter)
    return is_legacy_module, legacy_meta, make_legacy_adapter


# 原生插件（延迟 import，避免循环）
def _make_native_proxy(lib_path, action_cb, loop=None):
    from ..nativehost import NativeHostProxy
    return NativeHostProxy(lib_path, action_cb, loop=loop)


@dataclass(slots=True)
class LoadedPlugin:
    meta: PluginMeta
    kind: str = "py"              # py(模块/类) | native(原生 DLL/SO)
    module: Any = None            # 模块级插件（函数式）
    instance: Plugin | None = None  # 类插件
    lib_path: Any = None          # 原生库路径（kind=native）
    native_host: Any = None       # NativeHostProxy（kind=native，activate 后）
    config: dict[str, Any] = field(default_factory=dict)
    enabled: bool = True
    ctx: PluginContext | None = None
    source: str = ""               # 实际来源文件名（面板显示用）
    legacy: Any = None             # Linux 版老插件适配器（kind=legacy）
    runtime: Any = None            # 所属账号运行时（老插件兼容层需要）

    async def activate(self, ctx: PluginContext, loop=None) -> None:
        """注入能力并启动：py 走 on_load/setup；native 启动 host 子进程（隔离）"""
        self.ctx = ctx
        if self.kind == "legacy":
            # 老插件：注入所属账号（兼容层据此定位账号数据/config/发送通道）
            if self.legacy is not None:
                self.legacy.runtime = self.runtime
            return
        if self.kind == "native":
            async def _ac(req: dict):
                # 异步执行账号统一动作（proxy 在事件循环中调度，不阻塞读线程）
                action = req.get("action", "")
                params = {k: v for k, v in req.items() if k != "action"}
                return await ctx.action(action, params)
            self.native_host = _make_native_proxy(self.lib_path, _ac, loop=loop)
            self.native_host.init(self.config)
            return
        if self.instance is not None:
            await self.instance.on_load(self.config, ctx)
        else:
            setup = getattr(self.module, "setup", None)
            if setup is not None:
                ret = setup(ctx)
                if hasattr(ret, "__await__"):
                    await ret

    async def handle(self, event: Event) -> bool | str:
        if self.kind == "native":
            if self.native_host is None:
                return HANDLE_NOT_HANDLED
            try:
                return bool(await self.native_host.handle_event_async(event.to_dict()))
            except Exception:
                # host 崩溃且重试耗尽：标记 crashed（上层 disable_plugin 可据此停用）
                nh = self.native_host
                if nh is not None and getattr(nh, "_crashed", False):
                    log.error("原生插件 %s host 已崩溃且重启失败（将自动停用）", self.meta.name)
                    self.enabled = False
                    await self.shutdown()
                    return False
                log.exception("原生插件 %s 事件处理异常", self.meta.name)
                return False
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

    async def shutdown(self) -> None:
        """停用/卸载：native 关 host"""
        if self.kind == "native" and self.native_host is not None:
            try:
                self.native_host.stop()
            except Exception:
                pass
            self.native_host = None


class PluginLoader:
    """从插件目录加载插件（模块级函数式 + Plugin 类两种形态）。

    - 目录形态：plugins/xxx.py（发布可换成 xxx.pyd，import 优先 pyd）
    - 模块需导出：__meta__: PluginMeta 或 META 字典 + handle(event)
    - 可选导出：plugin: Plugin 实例 / load / unload
    """

    def __init__(self, plugins_dir: str | Path,
                 extra_dirs: list[str | Path] | None = None,
                 account_id: Any = None,
                 runtime: Any = None) -> None:
        """plugins_dir: 主目录（新 SDK 插件，共享）
        extra_dirs:  追加目录（如账号内的 Linux 版老插件目录）
        account_id:  用于隔离模块命名空间（同名插件在不同账号互不干扰）
        runtime:     所属账号运行时（老插件兼容层用）
        """
        self.plugins_dir = Path(plugins_dir)
        self.extra_dirs = [Path(d) for d in (extra_dirs or [])]
        self.account_id = account_id
        self.runtime = runtime
        self.loaded: dict[str, LoadedPlugin] = {}
        self.skipped: set[str] = set()

    # ---- 老插件的 `plugins.*` 内部导入支持 ----
    def _install_plugins_namespace(self, sample_file: Path | None) -> None:
        """老插件之间用 `from plugins.xxx import yyy` 互相引用（如 video_parser
        引用 parser_bridge）。Linux 版靠 CWD=实例目录 + PEP420 命名空间包实现，
        这里等价地注入一个指向该账号插件目录的 `plugins` 包。"""
        if sample_file is None:
            return
        d = Path(sample_file).parent
        mod = sys.modules.get("plugins")
        if mod is None or getattr(mod, "_astercore_ns", False):
            import types as _types
            mod = _types.ModuleType("plugins")
            mod._astercore_ns = True
            sys.modules["plugins"] = mod
        paths = list(getattr(mod, "__path__", []) or [])
        if str(d) not in paths:
            paths.insert(0, str(d))       # 最近加载的账号优先
        mod.__path__ = paths

    # ---- 目录解析 ----
    def all_dirs(self) -> list[Path]:
        return [self.plugins_dir, *self.extra_dirs]

    def find_source(self, name: str) -> Path | None:
        """按扩展名优先级在多个目录里找插件文件（.py 优先于 .pyd/.so）"""
        for sfx in (".py", ".pyd", ".so"):
            for d in self.all_dirs():
                f = d / f"{name}{sfx}"
                if f.exists():
                    return f
        for d in self.all_dirs():
            for sfx in (".dll",):
                f = d / f"{name}{sfx}"
                if f.exists():
                    return f
        return None

    def discover_names(self) -> list[str]:
        """扫描所有插件目录的插件名（.py/.pyd 模块 或 .so/.dll 原生库）"""
        names: list[str] = []
        for d in self.all_dirs():
            if not d.exists():
                continue
            for p in sorted(d.iterdir()):
                if p.suffix in (".py", ".pyd", ".so", ".dll") and not p.name.startswith("_"):
                    names.append(p.stem)
        return list(dict.fromkeys(names))  # 去重保序（同名 py+so 只留 py 优先）


    def load(self, name: str) -> LoadedPlugin | None:
        if name in self.loaded:
            return self.loaded[name]
        try:
            # 1) Python 模块（.py 开发优先；.pyd Windows 发布；.so Cython/Linux）
            #    模块名带账号维度：两个账号各自的同名老插件互不干扰（全局状态隔离）
            ns = f"astercore_plugins_{self.account_id or 'shared'}"
            py_sources = [f for sfx in (".py", ".pyd", ".so")
                          if (f := self.find_source(name)) is not None
                          and f.suffix == sfx]
            module = None
            from astercore.compat.utils_pkg import install as _compat_install
            _compat_install()          # 幂等：老插件在 import 期就要 import utils
            self._install_plugins_namespace(py_sources[0] if py_sources else None)
            for f in py_sources:
                try:
                    spec = importlib.util.spec_from_file_location(
                        f"{ns}.{name}", f)
                    if spec is None or spec.loader is None:
                        continue
                    mod = importlib.util.module_from_spec(spec)
                    sys.modules[f"{ns}.{name}"] = mod
                    spec.loader.exec_module(mod)
                    module = mod
                    break
                except Exception as e:
                    log.debug("插件 %s 以 Python 方式加载失败(%s): %s", name, f.name, e)
                    sys.modules.pop(f"{ns}.{name}", None)
                    continue

            if module is not None:
                # ① Linux 版老插件：同步 handle(event: dict) -> bool
                #    契约见《Windows版开发计划书》§5.2「保持现有 SDK 接口不变」
                is_legacy_module, legacy_meta, make_legacy_adapter = _compat_legacy()
                if is_legacy_module(module):
                    adapter = make_legacy_adapter(module, name)
                    lp = LoadedPlugin(meta=legacy_meta(module, name),
                                      kind="legacy", module=adapter,
                                      legacy=adapter, runtime=self.runtime)
                    lp.source = f"legacy:{Path(py_sources[0]).name}"
                    self.loaded[name] = lp
                    log.info("已加载 Linux 版插件 %s（兼容层）", lp.meta.name_cn or name)
                    return lp

                # 像 Linux 版 main.py 一样：只收「有 handle」的模块，
                # 辅助文件（如 parser_bridge/jm_worker）不算插件
                if not hasattr(module, "handle") and getattr(module, "plugin", None) is None:
                    self.skipped.add(name)
                    log.info("跳过 %s（无 handle，非插件模块）", name)
                    return None

                meta = self._read_meta(module, name)
                lp = LoadedPlugin(meta=meta, module=module, runtime=self.runtime)
                # 类插件
                inst = getattr(module, "plugin", None)
                if isinstance(inst, Plugin):
                    lp.instance = inst
                    lp.module = None
                src = py_sources[0] if py_sources else None
                lp.source = src.name if src else f"{name}.py"
                self.loaded[name] = lp
                log.info("已加载插件 %s v%s", meta.name_cn or name, meta.version)
                return lp

            # 2) 原生 C-ABI 库（ctypes 加载 + nap_plugin_* 导出）
            lib = self.find_source(name)
            if lib is not None and lib.suffix in (".so", ".dll"):
                meta = PluginMeta(name=name, name_cn=f"原生·{name}",
                                  version="?", description="原生 C-ABI 插件")
                lp = LoadedPlugin(meta=meta, kind="native", lib_path=lib,
                                  source=lib.name, runtime=self.runtime)
                self.loaded[name] = lp
                log.info("已登记原生插件 %s (%s)", name, lib.name)
                return lp
            log.warning("插件 %s 文件不存在或无法加载", name)
            return None
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
        try:
            import asyncio
            asyncio.get_event_loop().run_until_complete(lp.shutdown())
        except Exception:
            pass

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
