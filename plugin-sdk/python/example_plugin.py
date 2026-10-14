# -*- coding: utf-8 -*-
"""示例插件：签到 + 分群开关 + 配置 + 帮助（拷到 data/<账号>/plugins/ 就能跑）

双端说明：
  · 契约是 Linux 那套（模块级 handle + utils），所以同一份文件在 Linux 版上也跑得起来；
  · nap_sdk.py 要与本文件**同目录**（不同目录也没关系，改 import 即可）。
"""
try:                                     # 宿主机：两端都把插件目录挂成 plugins 命名空间
    from plugins import nap_sdk as sdk
except ImportError:                      # 离线自测：同目录直接跑
    import nap_sdk as sdk

# 发消息用 sdk.reply(event, ...)：宿主里就是 utils.api.send_message，
# 但**离线自测也能跑**（顶部 `from utils.api import send_message` 在离线时会 import 失败，
# 因为那时还没有 utils —— 需要老写法的话，把它放进函数内部、或 try/except 包起来）。

# ---------------- 元数据（WebUI / 面板显示用） ----------------
sdk.set_meta(globals(),
             name_cn="示例·签到",
             name_en="example_sign_in",
             version="1.0.0",
             desc="演示 SDK：指令、分群开关、配置三段、帮助与离线自测",
             author="AsterCore")

# ---------------- 配置（一次声明：Linux 的 WebUI 认识这三段，Windows 面板也出表单） ----------------
sdk.declare_config({
    "commands": {
        "sign1": ("签到", "每日签到"),
        "rank": ("签到排行", "查看签到次数"),
        "enable": ("开启签到", "本群开启"),
        "disable": ("关闭签到", "本群关闭"),
    },
    "settings": {
        "limit": ("每日签到上限", 3, "number"),
        "auto": ("自动签到", True, "bool"),
    },
    "messages": {
        "welcome": ("签到问候", "今天也要元气满满～"),
    },
}, scope=globals())

_COUNT: dict = {}                        # 演示用：真实插件请存到自己的数据文件里


@sdk.rule(sdk.cmd("签到"), sdk.in_group(sdk.cmd("打卡")))
def sign_in(event: dict) -> bool:
    """每日签到：演示「按群开关 + 读配置」"""
    if not sdk.enabled(event):           # 本群没开（私聊永远开）
        return False
    gid = str(event.get("group_id") or "私聊")
    uid = str(event.get("user_id") or "")
    n = _COUNT.get((gid, uid), 0) + 1
    limit = int(sdk.cfg("settings.limit", 3) or 3)
    if n > limit:
        sdk.reply(event, f"今天已经签过 {limit} 次啦，明天再来～")
        return True
    _COUNT[(gid, uid)] = n
    sdk.reply(event, f"{sdk.cfg('messages.welcome', '你好呀～')}（第 {n} 次）")
    return True


@sdk.rule(sdk.cmd("签到排行", "签到排行榜"))
def rank(event: dict) -> bool:
    if not _COUNT:
        sdk.reply(event, "还没有人签到哦")
        return True
    top = sorted(_COUNT.items(), key=lambda kv: -kv[1])[:5]
    lines = ["签到排行："] + [f"  {i}. {uid} — {c} 次" for i, ((_g, uid), c) in enumerate(top, 1)]
    sdk.reply(event, "\n".join(lines))
    return True


@sdk.rule(sdk.cmd("开启签到", "关闭签到"))
def switch(event: dict) -> bool:
    on = "开启" in sdk.event_text(event)
    if not sdk.toggle(event, on):
        sdk.reply(event, "这个开关只能在群里用")
        return True
    sdk.reply(event, "本群已开启签到" if on else "本群已关闭签到")
    return True


sdk.help_rule("帮助", "签到帮助")         # 自带回复，裸调即可（别再包 @sdk.rule）


if __name__ == "__main__":               # 离线自测：python example_plugin.py
    for t in ("帮助", "签到", "开启签到", "签到", "签到", "签到", "签到", "签到排行"):
        sdk.dev(globals(), raw_message=t)
    print("契约/可移植性检查：", sdk.check(globals()) or "通过 ✅")