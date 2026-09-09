# 栖星 AsterCore · 账号运行时
# 一个账号 = 一个后端实例 + 一套插件加载器/总线 + 独立数据目录。

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .backend import Backend
from .bus import EventBus, PluginLoader
from .models import ActionResult, Event, seg_text
from .plugin import Plugin

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

    # ---- 生命周期 ----
    async def start(self) -> None:
        plugins = self.plugin_loader.load_all()
        # 加载插件配置（data/plugins/<name>/config.json）
        for lp in plugins:
            lp.config = self._load_plugin_config(lp.meta.name)
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
        return await self.backend.action(action, params)

    def send_group(self, group_id: int | str, text: str):
        """便捷：发群文本（供演示/调试/简单插件复用）"""
        async def _do():
            return await self.action(
                "send_group",
                {"group_id": group_id,
                 "message": [seg_text(text).to_dict()]},
            )
        return _do()

    # ---- 配置 ----
    def _load_plugin_config(self, plugin_name: str) -> dict[str, Any]:
        cf = self.data_dir / "plugins" / plugin_name / "config.json"
        if cf.exists():
            import json
            try:
                return json.loads(cf.read_text(encoding="utf-8"))
            except Exception:
                log.warning("插件配置解析失败: %s", cf)
        return {}
