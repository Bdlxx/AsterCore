# 栖星 AsterCore · 示例插件②：计数器（类写法 + 配置热更）
#
# 演示进阶用法：
#   1. 继承 Plugin 基类（类名建议与插件名一致）
#   2. on_load / on_reload 读取配置（面板里可直接编辑 JSON 并热更）
#   3. 用 self.ctx 发消息
#
# 触发：发送含"计数器"/"count"的消息 → 回复已响应次数
# 配置：面板 → 该插件 → 配置，改 keywords / reply 后保存即热更。

import logging

from astercore.core.models import Event, seg_text
from astercore.core.plugin import Plugin, PluginMeta

log = logging.getLogger("astercore.demo_counter")

__meta__ = PluginMeta(
    name="demo_counter",
    name_cn="示例·计数器",
    version="0.2.0",
    description="统计触发次数，关键词与回复可在面板配置（演示配置热更）",
    author="AsterCore",
)

DEFAULT_CONFIG = {
    "keywords": ["计数器", "count"],
    "reply": "计数君已响应 {n} 次～",
}


class demo_counter(Plugin):  # noqa: N801 —— 类名与插件名一致，便于日后编译发布
    def __init__(self) -> None:
        super().__init__()
        self._n = 0

    async def on_load(self, config: dict, ctx=None) -> None:
        await super().on_load(config, ctx)
        self._apply(config)

    async def on_reload(self, config: dict) -> None:
        """面板保存 config.json 后内核自动调用（热更，不重启）"""
        self._apply(config)
        log.info("配置已热更：keywords=%s", self.config.get("keywords"))

    def _apply(self, config: dict | None) -> None:
        cfg = dict(DEFAULT_CONFIG)
        cfg.update(config or {})
        self.config = cfg

    async def handle(self, event: Event):
        if event.type != "message":
            return False
        text = event.text().strip().lower()
        if not any(k.lower() in text for k in self.config.get("keywords", [])):
            return False

        self._n += 1
        reply = str(self.config.get("reply", "计数 {n}")).format(n=self._n)
        if self.ctx is not None:
            msg = seg_text(reply)
            if event.is_group() and event.group_id is not None:
                await self.ctx.send_group(event.group_id, msg)
            elif event.user_id is not None:
                await self.ctx.send_private(event.user_id, msg)
        return True
