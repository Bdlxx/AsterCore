# 栖星 AsterCore · Null 后端（dry-run / 单元测试用）
# action 只打印不真发，用于验证「插件 → ctx.action → 后端」链路。

from __future__ import annotations

import logging

from ..core.backend import Backend, BackendConfig, register_backend
from ..core.models import ActionResult, Event

log = logging.getLogger("astercore.backend.null")


@register_backend
class NullBackend(Backend):
    name = "null"

    async def start(self) -> None:
        self._running = True
        log.info("Null 后端已启动（不会连接任何协议）")

    async def stop(self) -> None:
        self._running = False

    async def check(self) -> ActionResult:
        return ActionResult.success({"null": True})

    async def action(self, action: str, params: dict[str, Any]) -> ActionResult:
        log.info("[Null动作] %s %s", action, params)
        return ActionResult.success({"sent": True, "action": action})
