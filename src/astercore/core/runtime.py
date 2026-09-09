# 栖星 AsterCore · 账号运行时
# 一个账号 = 一个后端实例 + 一套插件加载器/总线 + 独立数据目录。

from __future__ import annotations

import asyncio
import sys
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .backend import Backend
from .bus import EventBus, PluginLoader
from .models import ActionResult, Event, seg_text
from .logring import install as install_logring, ring as _ring
from .plugin import Plugin, PluginContext

log = logging.getLogger("astercore.runtime")


class AccountContext(Plugin):
    """注入给插件的上下文基类（占位：后续给插件提供 send/调用能力）"""
    pass


@dataclass(slots=True)
class AccountRuntime:
    """账号运行时：装配后端 + 插件，驱动事件流转。"""

    account_id: int | str
    data_dir: Path
    backend: Backend
    plugin_dir: Path | None = None
    plugin_loader: PluginLoader = field(init=False)
    bus: EventBus = field(init=False)
    plugin_configs: dict[str, dict] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.data_dir = Path(self.data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.plugin_loader = PluginLoader(self.plugin_dir or self.data_dir / "plugins")
        self.bus = EventBus()
        self.backend.on_event = self._on_event
        install_logring()  # 内核日志接入环形缓冲（幂等）

    # ---- 日志（面板实时读取） ----
    def recent_logs(self, limit: int = 100) -> list[dict]:
        return _ring.recent(limit)

    # ---- 生命周期 ----
    async def start(self) -> None:
        plugins = self.plugin_loader.load_all()
        # 加载插件配置（data/plugins/<name>/config.json）并激活（注入 ctx）
        ctx = PluginContext(account_id=self.account_id, action=self.action)
        _loop = asyncio.get_running_loop()
        for lp in plugins:
            lp.config = self._load_plugin_config(lp.meta.name)
            try:
                await lp.activate(ctx, loop=_loop)
            except Exception:
                log.exception("插件 %s 激活失败（保持加载但不注入能力）", lp.meta.name)
        self.bus.set_plugins(plugins)
        await self.backend.start()
        log.info("账号 %s 启动完成（后端 %s，插件 %d）",
                 self.account_id, self.backend.name, len(plugins))

    async def stop(self) -> None:
        await self.backend.stop()

    # ---- 事件入口（后端回调） ----
    async def _on_event(self, event: Event) -> None:
        # 自消息过滤（各后端也可自行过滤；这里兜底）
        if event.self_id is not None and str(event.user_id) == str(event.self_id):
            return
        await self.bus.dispatch(event)

    # ---- 对外动作（给 web/CLI/未来插件 SDK 用） ----
    async def action(self, action: str, params: dict[str, Any]) -> ActionResult:
        params = dict(params)
        params.setdefault("account_id", self.account_id)
        # 发送类动作入日志（面板可见）
        if action in ("send_group", "send_private", "send_message", "upload_file"):
            try:
                texts = [s.get("data", {}).get("text", "") for s in
                         params.get("message", []) if isinstance(s, dict)
                         and s.get("type") == "text"]
                target = params.get("group_id") or params.get("user_id") or ""
                log.info("发送动作 %s → %s %r", action, target,
                         "".join(texts)[:40])
            except Exception:
                pass
        return await self.backend.action(action, params)

    # ---- 插件管理（web/CLI 用） ----
    def list_plugins(self) -> list[dict[str, Any]]:
        """列出插件与状态"""
        out = []
        for name, lp in self.plugin_loader.loaded.items():
            out.append({
                **lp.meta.to_dict(),
                "enabled": lp.enabled,
                "kind": lp.kind,
                "file": lp.source or f"{name}.py",
            })
        return out

    async def enable_plugin(self, name: str) -> bool:
        """启用插件（若未加载则加载并激活注入 ctx）"""
        lp = self.plugin_loader.loaded.get(name)
        if lp is None:
            lp = self.plugin_loader.load(name)
            if lp is None:
                return False
            lp.config = self._load_plugin_config(name)
        if not lp.enabled:
            lp.enabled = True
            ctx = PluginContext(account_id=self.account_id, action=self.action)
            try:
                await lp.activate(ctx, loop=asyncio.get_running_loop())
            except Exception:
                log.exception("插件 %s 激活失败", name)
                return False
        self._refresh_bus()
        return True

    async def disable_plugin(self, name: str) -> bool:
        lp = self.plugin_loader.loaded.get(name)
        if lp is None:
            return False
        lp.enabled = False
        await lp.shutdown()
        self._refresh_bus()
        log.info("插件 %s 已停用", name)
        return True

    async def reload_plugins(self, name: str | None = None) -> int:
        """插件热重载：
        - name=None：全量重扫（卸载消失/加载新增）
        - name=指定：仅重载该插件（.py/.pyd 源码改动后生效，无需重启）；
          原生插件先关 host 再重建
        """
        targets = [name] if name else list(self.plugin_loader.loaded) \
            + list(self.plugin_loader.discover_names())
        ctx = PluginContext(account_id=self.account_id, action=self.action)
        _loop = asyncio.get_running_loop()

        if name is not None:
            lp = self.plugin_loader.loaded.pop(name, None)
            if lp is not None:
                await lp.shutdown()
                # 移除已缓存模块，强制重新编译执行
                sys.modules.pop(f"astercore.plugins.{name}", None)
            new_lp = self.plugin_loader.load(name)
            if new_lp is not None:
                new_lp.config = self._load_plugin_config(name)
                try:
                    await new_lp.activate(ctx, loop=_loop)
                except Exception:
                    log.exception("插件 %s 重载后激活失败", name)
            self._refresh_bus()
            return len(self.plugin_loader.loaded)

        found = set(self.plugin_loader.discover_names())
        for n in list(self.plugin_loader.loaded):
            if n not in found:
                self.plugin_loader.unload(n)
        for n in found:
            if n not in self.plugin_loader.loaded:
                lp = self.plugin_loader.load(n)
                if lp is not None:
                    lp.config = self._load_plugin_config(n)
                    try:
                        await lp.activate(ctx, loop=_loop)
                    except Exception:
                        log.exception("插件 %s 激活失败", n)
        self._refresh_bus()
        return len(self.plugin_loader.loaded)

    def _refresh_bus(self) -> None:
        self.bus.set_plugins(self.plugin_loader.loaded.values())

    def send_group(self, group_id: int | str, text: str):
        """便捷：发群文本（供演示/调试/简单插件复用）"""
        async def _do():
            return await self.action(
                "send_group",
                {"group_id": group_id,
                 "message": [seg_text(text).to_dict()]},
            )
        return _do()

    # ---- 插件配置（面板读写 config.json） ----
    def _plugin_config_path(self, name: str) -> Path:
        return self.data_dir / "plugins" / name / "config.json"

    def _load_plugin_config(self, plugin_name: str) -> dict[str, Any]:
        cf = self._plugin_config_path(plugin_name)
        if cf.exists():
            import json
            try:
                return json.loads(cf.read_text(encoding="utf-8"))
            except Exception:
                log.warning("插件配置解析失败: %s", cf)
        return {}

    def get_plugin_config(self, name: str) -> dict[str, Any]:
        """读插件配置（含运行时内存值，保存后立即反映）"""
        lp = self.plugin_loader.loaded.get(name)
        if lp is not None:
            return dict(lp.config or {})
        return self._load_plugin_config(name)

    async def save_plugin_config(self, name: str, data: dict[str, Any]) -> bool:
        """写插件配置并尝试热更（类插件 on_reload；函数式更新内存值）"""
        import json
        if not isinstance(data, dict):
            return False
        cf = self._plugin_config_path(name)
        cf.parent.mkdir(parents=True, exist_ok=True)
        cf.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                      encoding="utf-8")
        lp = self.plugin_loader.loaded.get(name)
        if lp is not None:
            lp.config = dict(data)
            if lp.instance is not None:
                try:
                    await lp.instance.on_reload(dict(data))
                except Exception:
                    log.exception("插件 %s 配置热更失败", name)
        return True
