# 栖星 AsterCore · 示例插件①：打招呼（函数式写法）
#
# 这是"开发态"插件的标准写法：一个 .py 文件 = 一个插件，放本目录即被自动加载。
# 触发：群里/私聊发送 你好 / hello / hi / 在吗 → 回复"你好呀～"
#
# 改完保存后，在面板点该插件的「重载」即可生效，无需重启。

import logging

from astercore.core.models import Event, seg_text
from astercore.core.plugin import (HANDLE_HANDLED, HANDLE_NOT_HANDLED,
                                   PluginContext, PluginMeta)

log = logging.getLogger("astercore.demo_hello")

# 插件元信息（面板上显示的名字/说明）
__meta__ = PluginMeta(
    name="demo_hello",
    name_cn="示例·打招呼",
    version="0.2.0",
    description="收到 你好/hello/hi/在吗 时回复",
    author="AsterCore",
)

_KEYWORDS = ("你好", "hello", "hi", "在吗")

_ctx: PluginContext | None = None


def setup(ctx: PluginContext) -> None:
    """内核加载时注入能力对象（模块级插件的约定入口）"""
    global _ctx
    _ctx = ctx
    log.info("demo_hello 已就绪（account=%s）", ctx.account_id)


async def handle(event: Event):
    """收到事件时调用：返回 HANDLED 表示已处理（不再传给后面的插件）"""
    if event.type != "message":
        return HANDLE_NOT_HANDLED

    text = event.text().strip().lower()
    if not text or text not in _KEYWORDS:
        return HANDLE_NOT_HANDLED

    log.info("命中关键词 %r（账号 %s）", text, event.account_id)

    if _ctx is not None:
        reply = seg_text("你好呀～")
        if event.is_group() and event.group_id is not None:
            await _ctx.send_group(event.group_id, reply)
        elif event.user_id is not None:
            await _ctx.send_private(event.user_id, reply)
    return HANDLE_HANDLED
