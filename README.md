# 栖星 AsterCore

> QQ 机器人多后端框架 · Windows 桌面版开发中（内核跨平台可运行）

栖星（AsterCore）是一个面向普通用户的 QQ 机器人框架：**多账号、多后端（NapCat / 内置协议可选）、插件化（.py/.pyd/.dll 三态）、带 Web 管理面板与自动更新**。

- 中文「栖星」· 英文 AsterCore（Aster=紫菀/小行星，Core=内核；与 bot 家族「依星/羽笙」星月意象一致）
- 完整规划：《Windows版开发计划书》（本地 docs，不进远端）· 进度：[docs/PROGRESS.md](docs/PROGRESS.md)

## 快速开始（Linux/macOS 本机演示）

```bash
pip install -e ".[web]"
python -m astercore.launch --no-browser   # 启动器：首启向导 → 面板（自动开浏览器去掉 --no-browser）
python -m astercore --account 10001 --dry-run          # 离线自检（内核+插件链路）
python -m astercore --account 10001 --ws ws://127.0.0.1:3001 --token xxx   # 连 OneBot v11
python -m astercore --serve-accounts --accounts-dir accounts --port 8080   # 多账号 Web 面板
# 打开 http://127.0.0.1:8080
```

测试：`python -m unittest discover -s tests -v`

## 架构

```
AsterCore.exe（未来桌面壳）
 ├─ 内核（Python，跨平台）          事件/动作模型 · 插件加载器 · 账号运行时
 ├─ Backend 适配层（可插拔）        onebot(OneBot v11 WS) / null / lagrange(规划)
 ├─ 插件体系
 │   ├─ .py    开发态
 │   ├─ .pyd   Cython 发布（防小白修改，pack/build_pyd.py）
 │   └─ .dll   原生 C-ABI（plugin-sdk/nap_plugin.h + C 示例）
 │       └─ 运行于 plugin-host 子进程（隔离 + 崩溃自动重启）
 └─ Web 面板（单端口 JSON API + 前端）  账号/插件/配置/实时日志/调试台/鉴权/运行方式
```

## 目录导览

| 路径 | 说明 |
|---|---|
| `src/astercore/core/` | 内核：models / plugin(SDK) / bus(加载+分发) / backend / runtime / manager / logring |
| `src/astercore/backends/` | onebot(WS) / null（可插拔注册） |
| `src/astercore/web/` | Flask 单端口面板（ManagerWebPanel + API + 前端） |
| `src/astercore/native.py` | 原生 DLL/SO ctypes 加载器 |
| `src/astercore/nativehost.py` | plugin-host 子进程代理（隔离/重启） |
| `src/astercore/host.py` | plugin-host 子进程（`python -m astercore.host`） |
| `src/astercore/plugins/` | 内置示例插件（demo_hello） |
| `plugin-sdk/` | 原生 ABI：nap_plugin.h + examples/c-sample |
| `pack/` | PyInstaller spec、build_pyd.py |
| `tools/` | fake_onebot.py（端到端联调用假服务端） |
| `docs/` | 进度表（计划书/ABI 本地私有） |

## 插件开发（Python SDK）

**函数式插件**（最小）：

```python
# data/<账号>/plugins/hello.py
from astercore.core.models import Event, seg_text
from astercore.core.plugin import PluginMeta

__meta__ = PluginMeta(name="hello", name_cn="示例", version="0.1.0")

async def handle(event: Event):
    if event.type == "message" and event.text() == "ping":
        # 有 ctx 时经 setup 注入发送能力（见 plugins/demo_hello.py）
        return True
    return False
```

**类插件**（带配置热更）：继承 `Plugin`，`on_load(config, ctx)` / `handle(event)` / `on_reload(config)`。

**原生 C 插件**：见 `plugin-sdk/examples/c-sample/`（编译 .so/.dll 放入插件目录即被统一加载器识别）。

## 账号与数据

```
accounts/<id>/backend.json      # 连接配置（WS/HTTP/token、后端名）
data/<id>/plugins/<name>/config.json   # 插件配置（面板可编辑）
```

## 状态

开发中（内核可用）：事件/动作 · 多后端 · 多账号 · 插件三态(部分) · Web 面板 · 26 单测。
详见 [docs/PROGRESS.md](docs/PROGRESS.md)。
