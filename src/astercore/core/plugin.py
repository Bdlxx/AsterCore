# 栖星 AsterCore · 插件 SDK
# Python 插件协议（与《ABI 草案》对齐）。桌面版插件可两态分发：
#   .py（开发） / .pyd（Cython 编译发布，防小白修改）

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from .models import Event, Segment

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
        self.log: Callable[[int, str], None] | None = None

    # 生命周期
    async def on_load(self, config: dict[str, Any]) -> None:
        """加载（可异步初始化）；异常会导致插件被标记失败"""
        self.config = config or {}

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
    "HANDLE_HANDLED",
    "HANDLE_NOT_HANDLED",
    "Event",
    "Segment",
]
