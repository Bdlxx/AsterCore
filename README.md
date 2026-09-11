# 栖星 AsterCore

> QQ 机器人多后端框架 · **v0.2.0 可用版**（Windows 打包即用 / 内核跨平台）

栖星（AsterCore）是一个面向普通用户的 QQ 机器人框架：**多账号、多后端（NapCat / 内置协议可选）、插件化（.py/.pyd/.dll 三态）、带 Web 管理面板与自动更新**。

- 中文「栖星」· 英文 AsterCore（Aster=紫菀/小行星，Core=内核；与 bot 家族「依星/羽笙」星月意象一致）
- 完整规划：《Windows版开发计划书》（本地 docs，不进远端）· 进度：[docs/PROGRESS.md](docs/PROGRESS.md)

## 快速开始

### A. Windows 打包版（推荐）

1. 下载 `AsterCore-win64-vX.Y.Z.zip`，**整个解压**（`astercore.exe` 与 `_internal/` 必须同级）
2. 双击 `astercore.exe`（黑色控制台窗口是正常的，日志都在里面）
3. 首次运行直接回车用推荐运行方式；浏览器自动打开面板 `http://127.0.0.1:8080`

首次运行会自动在 exe 同级创建并播种示例插件：

```
data/        账号数据、插件配置、运行状态
accounts/    账号连接配置（accounts/<QQ>/backend.json）
plugins/     插件目录（已含 demo_hello.py / demo_counter.py）
```

接上 QQ：装好 NapCat → 开 WebSocket 服务端(3001) → 面板「新建账号」填 `ws://127.0.0.1:3001` → 启动。
详细图文步骤见包内 `使用说明.txt`。

### B. 源码运行（开发）

```bash
pip install -e ".[web]"
python -m astercore                 # 零参数 = 启动器（向导 → 面板 → 自动开浏览器）
python -m astercore --port 9000 --no-browser --yes     # 启动器参数也可直接给
python -m astercore --cli --account 10001 --dry-run    # 强制命令行模式：离线自检内核+插件链路
python -m astercore --cli --account 10001 --ws ws://127.0.0.1:3001 --token xxx
python -m astercore --cli --serve-accounts --port 8080 # 多账号面板
```

> 入口规则：**无参数或只给启动器参数**（`--port/--host/--no-browser/--yes/--shell`）→ 启动器；
> 出现 CLI 专属参数（`--account/--dry-run/--serve-accounts/...`）或显式 `--cli` → 命令行模式。

### 测试与验收

```bash
python -m unittest discover -s tests        # 79 个单测
python -m PyInstaller pack/astercore.spec --noconfirm --clean
python tools/e2e_frozen.py                  # 打包产物端到端验收（建号→连后端→插件→回复）
```

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
| `src/astercore/plugin_templates/` | 首启播种用的示例插件源码（打包后解压到用户 plugins/） |
| `src/astercore/paths.py` | 统一路径（打包时基准=exe 所在目录，避免数据乱跑） |
| `src/astercore/bootstrap.py` | 首启引导：建目录 + 播种示例插件（不覆盖用户文件） |
| `src/astercore/plugins/` | 内核自带示例插件（dry-run 用） |
| `plugin-sdk/` | 原生 ABI：nap_plugin.h + examples/c-sample |
| `pack/` | PyInstaller spec、build_pyd.py |
| `tools/` | fake_onebot.py（假 OneBot 服务端）· e2e_frozen.py（打包产物验收） |
| `pack/使用说明.txt` | 面向 Windows 用户的图文说明（随 zip 分发） |
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
accounts/<id>/backend.json              # 连接配置（WS/HTTP/token、后端名）
plugins/*.py|.pyd|.dll                  # 共享插件目录（所有账号加载，面板可分别启停）
data/<id>/plugins/<name>/config.json    # 插件配置（面板可直接编辑，保存即热更）
data/runtime.json                       # 运行方式/向导状态
```

> 基准目录默认是 exe 所在目录（源码运行则为当前目录），可用环境变量 `ASTERCORE_HOME`
> 或 `--data-dir/--accounts-dir/--plugins-dir` 覆盖。

## 状态

**v0.2.0 可用版**：解压双击即可运行；建号 → 连后端 → 插件收发消息 → 面板看日志全链路已由
打包产物端到端验收（23 项检查）覆盖。

- 内核：事件/动作模型 · 多后端（onebot/null）· 多账号 · 环形日志 · 插件三态（.py/.pyd/原生 C-ABI）
- 面板：账号管理 · 插件启停/配置热更 · 实时日志 · 调试台发消息 · 鉴权分级 · 运行方式切换
- 测试：79 单测 + `tools/e2e_frozen.py` 打包验收
- 待办：Lagrange 内置协议直登 · pywebview 内嵌窗口默认化 · 自动更新 UI

详见 [docs/PROGRESS.md](docs/PROGRESS.md)。
