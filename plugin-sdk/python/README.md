# 栖星 AsterCore · Python 插件开发指南

> 适用于 **Windows 版（AsterCore）与 Linux 版（NapCat-WordLibBot）**：两边跑的是**同一份插件代码**。
> SDK 本体：本目录的 [`nap_sdk.py`](nap_sdk.py)（单文件、零依赖）。

---

## 1. 三分钟上手

插件就是一个 `.py` 文件，放进 **`data/<账号>/plugins/`**（Windows 面板里也能看到同一目录），
**文件名（不含后缀）= 插件英文名**，启动时自动加载，无需注册。

```python
# plugins/my_plugin.py
__plugin_name_cn__ = "我的插件"      # 中文名（面板显示）
__plugin_name_en__ = "my_plugin"     # 英文标识（与文件名一致）
__plugin_version__ = "1.0.0"
__plugin_desc__    = "功能描述"
__plugin_author__  = "作者名"

from utils.api import send_message

def handle(event: dict) -> bool:
    if str(event.get("raw_message", "")).strip() == "ping":
        send_message(event, "pong")
        return True                 # True=已处理（事件链到此为止）
    return False                    # False=放行给后面的插件
```

用 SDK 可以少写样板（效果完全一样）：

```python
try:                                     # 宿主机：两端都把插件目录挂成 plugins 命名空间
    from plugins import nap_sdk as sdk
except ImportError:                      # 离线自测时同目录直接跑
    import nap_sdk as sdk

@sdk.meta(name_cn="我的插件", name_en="my_plugin", version="1.0.0", desc="ping→pong")
@sdk.rule(sdk.cmd("ping"))
def ping(event: dict) -> bool:
    sdk.reply(event, "pong")
    return True
```

`@sdk.rule(...)` 会在模块里**自动生成 `handle`**；你自己写了 `handle` 就不会被覆盖。

---

## 2. 事件结构（`handle` 收到的 dict）

| 字段 | 说明 | 示例 |
|---|---|---|
| `post_type` | 事件类型 | `"message"` |
| `message_type` | 消息类型 | `"group"` / `"private"` |
| `raw_message` | 原始文本（老插件的写法，含 CQ 码） | `"签到"` |
| `user_id` | 发送者 QQ | `123456789` |
| `group_id` | 群号（私聊无此字段） | `957918829` |
| `self_id` | 机器人 QQ | `740979632` |
| `message` | 消息段数组 | `[{"type":"text","data":{"text":"签到"}}]` |

取纯文本用 `sdk.event_text(event)`（内部就是 `raw_message`，缺了用 `message` 段兜底）。

---

## 3. 常用 API

```python
from utils.api import send_message, http_get

send_message(event, "你好！")                 # 回复：群→群、私聊→私聊（自动判断）
send_message(event, [                          # 图文混排（消息段数组）
    {"type": "text",  "data": {"text": "看："}},
    {"type": "image", "data": {"file": "http://…"}},
])
members = http_get("get_group_member_list", {"group_id": 123456})   # NapCat HTTP API
```

SDK 里的等价写法（**离线自测也能跑**）：`sdk.reply(event, "你好！")`、`sdk.http_get(action, params)`。
> 顶部直接 `from utils.api import send_message` 的老写法当然也支持，但**离线自测时会 import 失败**
> （那时还没有 utils），需要的话把它放进函数内部。

---

## 4. 分群开关（每个群独立）

**所有插件在每个群默认关闭**（与 Linux 版一致）——私聊发送开启/关闭命令等于全局控制。

```python
from utils.plugin_toggle import is_enabled, set_enabled

if event.get("message_type") == "group" and not is_enabled(event["group_id"], "my_plugin"):
    return False
set_enabled(event["group_id"], "my_plugin", True)
```

SDK 写法（插件名自动取 `__plugin_name_en__`）：

```python
if not sdk.enabled(event):        # 群没开 → False；私聊永远 True
    return False
sdk.toggle(event, True)           # 本群开；toggle(event, False) 关
```

---

## 5. 配置（一次声明，两端都出表单）

配置文件放在**账号数据目录**下，格式是固定的三段：

```json
{
  "commands": { "sign1": "签到", "rank": "签到排行" },
  "settings": { "limit": 3, "auto": true },
  "messages": { "welcome": "今天也要元气满满～" }
}
```

- Linux 的 WebUI 直接认这三段（`commands` 文本 / `settings` 开关与数字 / `messages` 文本域）；
- Windows 面板用 `__config_schema__` 生成表单 —— **SDK 会自动从上面三段生成它**，所以声明一次两边都有表单。

```python
sdk.declare_config({
    "commands": {"sign1": ("签到", "每日签到")},
    "settings": {"limit": ("每日上限", 3, "number"), "auto": ("自动签到", True, "bool")},
    "messages": {"welcome": ("签到问候", "你好呀～")},
}, scope=globals())

limit = sdk.cfg("settings.limit", 3)      # 读（支持点路径；缺项用声明里的默认值）
sdk.set_cfg("settings.limit", 5)          # 写单个值
```

> ⚠️ **不要硬编码 `data/xxx_config.json`**：两端的数据目录约定不同
> （Linux `<实例>/data/<名>_config.json`，Windows `<实例>/data/plugins/<名>/config.json`）。
> 用 `sdk.config_path()` / `sdk.cfg()`，它会自己找对位置。

---

## 6. 离线自测（不用起机器人）

```python
if __name__ == "__main__":
    for text in ("帮助", "签到"):
        sdk.dev(globals(), raw_message=text)      # 打印命中与回复
    print("检查：", sdk.check(globals()) or "通过 ✅")
```

命令行：

```bash
python nap_sdk.py --run my_plugin.py --text 签到     # 跑一次
python nap_sdk.py --run my_plugin.py --check-only    # 只做检查
```

`sdk.check()` 会报出**在 Linux 上跑不了**的写法，发布前请清空它：

| 报出的问题 | 为什么 |
|---|---|
| `import astercore…` | Linux 版没有这个包，插件会加载失败 |
| `class Xxx(Plugin)` / `self.ctx…` | 类插件与 ctx 是 **Windows 单端**能力，Linux 版没有 |
| 硬编码 `data/…json` | 两端数据目录不同，换端就读不到配置 |

---

## 7. 生成一个新插件（脚手架）

```bash
python new_plugin.py 我的插件 --name-en my_plugin --dir "D:/…/data/10001/plugins"
```

它会写出 `my_plugin.py`（元数据 + 配置三段 + 一条示例规则 + 自测入口）并**把 `nap_sdk.py` 拷到同目录**；
目录不给就打印该放哪儿。生成后：

```bash
cd "<插件目录>" && python my_plugin.py     # 离线自测
```

---

## 8. 发布

| 形态 | 说明 |
|---|---|
| `.py` | 直接放插件目录即可（开发/开源推荐） |
| `.pyd` | Cython 编译发布（防小白改动）；文件名仍是 `<英文名>.pyd` |
| 依赖 | 插件目录里可以带自己的辅助模块（如本 SDK 的 `nap_sdk.py`），`from plugins import xxx` 引用 |

📌 **插件仓库**：公共插件已拆到 [NapCat-WordLibBot-Plugins](https://github.com/Bdlxx/NapCat-WordLibBot-Plugins)，`install.sh` 部署实例时自动拉取；本仓库只内置核心词库插件。

---

## 9. 常见问题

**Q：插件加载了但群里不响应？** 分群开关默认关闭 —— 先发一次开启命令（`is_enabled` / `sdk.enabled`）。

**Q：改了配置什么时候生效？** JSON 文件是每次读取的（`sdk.cfg()` 每次读盘）；面板改完保存即刻生效。
**Q：插件崩了会不会拖垮机器人？** Python 插件在内核线程里跑，异常会被捕获并记日志；原生（DLL）插件才走子进程隔离。

**Q：怎么调试？** 面板「运行日志」能看到插件日志；离线用 `sdk.dev(...)` 迭代最快。