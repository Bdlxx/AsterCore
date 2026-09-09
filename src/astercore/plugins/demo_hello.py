# 栖星 AsterCore · 示例插件（模块级函数式写法）
# 安装目录：data/<账号>/plugins/demo_hello.py
# 收到私聊/群聊文本回复"你好/hello/hi"时回应（v0.1 演示：打印并终止链）。

import logging

from astercore.core.models import Event
from astercore.core.plugin import HANDLE_HANDLED, HANDLE_NOT_HANDLED, PluginMeta

log = logging.getLogger("astercore.demo_hello")

__meta__ = PluginMeta(
    name="demo_hello",
    name_cn="示例·打招呼",
    version="0.1.0",
    description="收到你好/hello/hi 时回应",
    author="AsterCore",
)

_KEYWORDS = ("你好", "hello", "hi", "在吗")


async def handle(event: Event):
    if event.type != "message":
        return HANDLE_NOT_HANDLED
    text = event.text().strip().lower()
    if not text or text not in _KEYWORDS:
        return HANDLE_NOT_HANDLED
    # 多账号字段演示：事件带 account_id
    log.info("demo_hello 命中关键词：%r（account=%s, %s）",
             text, event.account_id,
             f"group {event.group_id}" if event.is_group() else "private")
    return HANDLE_HANDLED
