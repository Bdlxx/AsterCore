# 栖星 AsterCore · Python 类插件示例（配置热更演示）
# 复制到 data/<账号>/plugins/demo_counter.py 使用。
# 演示：类插件 Plugin 继承、config 读取、on_reload 配置热更、事件统计计数。

import logging

from astercore.core.models import Event, Segment, seg_text
from astercore.core.plugin import Plugin, PluginMeta

log = logging.getLogger("astercore.demo_counter")

__meta__ = PluginMeta(
    name="demo_counter",
    name_cn="示例·计数器",
    version="0.1.0",
    description="统计命中次数，可配置关键词与回复文案（演示配置热更）",
    author="AsterCore",
)

DEFAULT_CONFIG = {
    "keywords": ["计数器", "count"],
    "reply": "计数君已响应 {n} 次～",
}


class demo_counter(Plugin):  # noqa: N801 —— 类名与插件名一致便于发布编译
    def __init__(self) -> None:
        super().__init__()
        self._n = 0

    async def on_load(self, config: dict, ctx=None) -> None:
        await super().on_load(config, ctx)
        cfg = dict(DEFAULT_CONFIG)
        cfg.update(config or {})
        self.config = cfg
        log.info("demo_counter 加载：keywords=%s", cfg.get("keywords"))

    async def on_reload(self, config: dict) -> None:
        """配置热更：面板保存 config.json 后内核自动调用"""
        cfg = dict(DEFAULT_CONFIG)
        cfg.update(config or {})
        self.config = cfg
        log.info("demo_counter 配置热更：keywords=%s", cfg.get("keywords"))

    async def handle(self, event: Event):
        if event.type != "message":
            return False
        text = event.text().strip().lower()
        kws = self.config.get("keywords", [])
        if not any(k in text for k in kws):
            return False
        self._n += 1
        reply = str(self.config.get("reply", "计数 {n}")).format(n=self._n)
        if self.ctx is not None:
            if event.is_group() and event.group_id is not None:
                await self.ctx.send_group(event.group_id, seg_text(reply))
            elif event.user_id is not None:
                await self.ctx.send_private(event.user_id, seg_text(reply))
        return True


plugin = demo_counter()  # 类插件形态：模块导出实例
