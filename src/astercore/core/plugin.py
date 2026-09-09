# 栖星 AsterCore · 插件 SDK
# Python 插件协议（与《ABI 草案》对齐）。桌面版插件可两态分发：
#   .py（开发） / .pyd（Cython 编译发布，防小白修改）

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from .models import Event, Segment, seg_text


class PluginContext:
    """注入给插件的动作入口（对应《ABI 草案》NapApi.action）。

    插件通过它发消息/执行动作；内部包装账号后端的统一 Action。
    v0.1 提供最常用便捷方法，完整动作集见 core.models 动作常量。
    """

    def __init__(self, account_id: int | str | None,
                 action: Callable[[str, dict[str, Any]], Awaitable[Any]]) -> None:
        self.account_id = account_id
        self._action = action

    async def send_group(self, group_id: int | str, message: list[Segment] | str):
        """发群消息：message 可为 Segment 列表或纯文本"""
        return await self._action("send_group", {
            "group_id": group_id,
            "message": _norm_message(message),
        })

    async def send_private(self, user_id: int | str, message: list[Segment] | str):
        return await self._action("send_private", {
            "user_id": user_id,
            "message": _norm_message(message),
        })

    async def send_message(self, message_type: str, target: int | str,
                           message: list[Segment] | str):
        """按类型发：message_type=group/private"""
        act = "send_group" if message_type == "group" else "send_private"
        key = "group_id" if message_type == "group" else "user_id"
        return await self._action(act, {key: target, "message": _norm_message(message)})

    async def recall(self, message_id: int | str):
        return await self._action("recall_message", {"message_id": message_id})

    async def set_group_ban(self, group_id, user_id, duration: int = 600):
        return await self._action("set_group_ban", {
            "group_id": group_id, "user_id": user_id, "duration": duration})

    async def action(self, action: str, params: dict[str, Any]):
        """任意统一动作（透传）"""
        params = dict(params)
        params.setdefault("account_id", self.account_id)
        return await self._action(action, params)


def _norm_message(message: list[Segment] | Segment | str) -> list[dict]:
    if isinstance(message, str):
        return [seg_text(message).to_dict()]
    if isinstance(message, Segment):
        return [message.to_dict()]
    return [s.to_dict() for s in message]


# 插件 handle 返回值语义（与 ABI 草案一致）
HANDLE_NOT_HANDLED = False  # 未处理，放行后续插件
HANDLE_HANDLED = True       # 已处理，终止事件链


@dataclass(slots=True)
class PluginMeta:
    """插件元信息（由插件模块级导出提供）"""

    name: str                     # 唯一名（ASCII 小写+下划线）
    name_cn: str = ""             # 中文显示名
    version: str = "0.1.0"
    description: str = ""
    author: str = ""
    master_only: bool = False     # 仅主人可用（用于命令级约束，供内核提示）

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "name_cn": self.name_cn,
            "version": self.version,
            "description": self.description,
            "author": self.author,
        }


class Plugin:
    """插件基类（可选继承；也可以直接模块级导出以下接口）"""

    meta: PluginMeta

    def __init__(self) -> None:
        self.config: dict[str, Any] = {}
        self.ctx: PluginContext | None = None
        self.log: Callable[[int, str], None] | None = None

    # 生命周期（ctx 由内核注入：发消息/执行动作的能力）
    async def on_load(self, config: dict[str, Any], ctx: PluginContext | None = None) -> None:
        """加载（可异步初始化）；异常会导致插件被标记失败"""
        self.config = config or {}
        self.ctx = ctx

    async def on_unload(self) -> None: ...

    async def on_reload(self, config: dict[str, Any]) -> None:
        """配置热更（可选）"""
        self.config = config or {}

    # 事件处理：返回 True=已处理(停链) / False=放行；或返回 "fallback" 表示解析失败待兜底
    async def handle(self, event: Event) -> bool | str:
        return HANDLE_NOT_HANDLED


# 兼容"模块级函数式"插件：
# 模块导出 __meta__ + handle(event) -> bool 即可，无需继承 Plugin。
# 模块级 handle 为同步或 async 均可（内核统一 await 包装）。

__all__ = [
    "PluginMeta",
    "Plugin",
    "PluginContext",
    "HANDLE_HANDLED",
    "HANDLE_NOT_HANDLED",
    "Event",
    "Segment",
]
