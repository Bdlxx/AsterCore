# 栖星 AsterCore · 示例插件（模块级函数式写法 + setup 注入能力）
# 收到"你好/hello/hi/在吗"时，通过 ctx 回复一条群消息。

import logging

from astercore.core.models import Event, seg_text
from astercore.core.plugin import HANDLE_HANDLED, HANDLE_NOT_HANDLED, PluginContext, PluginMeta

log = logging.getLogger("astercore.demo_hello")

__meta__ = PluginMeta(
    name="demo_hello",
    name_cn="示例·打招呼",
    version="0.2.0",
    description="收到你好/hello/hi 时回复",
    author="AsterCore",
)

_KEYWORDS = ("你好", "hello", "hi", "在吗")

_ctx: PluginContext | None = None


def setup(ctx: PluginContext) -> None:
    """内核加载时注入能力（模块级插件约定）"""
    global _ctx
    _ctx = ctx
    log.info("demo_hello 已获得发送能力（account=%s）", ctx.account_id)


async def handle(event: Event):
    if event.type != "message":
        return HANDLE_NOT_HANDLED
    text = event.text().strip().lower()
    if not text or text not in _KEYWORDS:
        return HANDLE_NOT_HANDLED

    log.info("demo_hello 命中：%r（account=%s, %s）",
             text, event.account_id,
             f"group {event.group_id}" if event.is_group() else "private")

    # 通过 ctx 回复（群消息回群、私聊回私聊）
    if _ctx is not None:
        if event.is_group() and event.group_id is not None:
            await _ctx.send_group(event.group_id, seg_text("你好呀～"))
        elif event.user_id is not None:
            await _ctx.send_private(event.user_id, seg_text("你好呀～"))
    return HANDLE_HANDLED
