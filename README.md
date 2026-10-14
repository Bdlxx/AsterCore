# 栖星 AsterCore

> QQ 机器人多后端框架 · **v0.6.30**（Windows 打包即用 / 内核跨平台）

栖星（AsterCore）是一个面向普通用户的 QQ 机器人框架：**多账号、多后端（NapCat / 内置协议可选）、
插件化（.py/.pyd/.dll 三态）、带 Web 管理面板、桌面内嵌窗口与自动更新**。

- 中文「栖星」· 英文 AsterCore（Aster=紫菀/小行星，Core=内核）
- 完整规划：《Windows版开发计划书》（本地 docs，不进远端）· 进度：[docs/PROGRESS.md](docs/PROGRESS.md)
- 两端（Windows 桌面版 ↔ Linux 机器人）**共用同一套面板资产**，改动必须同步并跑 `test_dual_end_assets`

## 快速开始

### A. Windows 打包版（推荐）

1. 到 [Releases](https://github.com/Bdlxx/AsterCore/releases) 下载 `AsterCore-win64-vX.Y.Z.zip`，
   **整个解压**（`astercore.exe` 与 `_internal/` 必须同级）；同页附 `.sha256` 可校验
2. 双击 `astercore.exe`（**无边框桌面窗口**，不弹控制台；日志写入 `console.log`）
3. 首次运行按向导走：**连接引导页**会一步步告诉你去哪下载 NapCat、装到哪个目录、怎么填连接信息

窗口行为：右上角**系统默认样式的三键**（最小化 / 最大化 / 关闭）；点 **✕** 会问
**「关闭窗口（保留托盘）」还是「退出程序」**，可勾「记住我的选择」（设置页第 ④ 张卡可改）。
顶栏可拖动窗口，**双击顶栏空白处 = 最大化/还原**。

首次运行会自动在 exe 同级创建并播种示例插件：

```
data/        账号数据、插件配置、运行状态
accounts/    账号连接配置（accounts/<QQ>/backend.json）
plugins/     插件目录（已含 demo_hello.py / demo_counter.py）
```

接上 QQ：装好 NapCat → 开 WebSocket 服务端(3001) → 面板「添加账号」填 `ws://127.0.0.1:3001` → 启动。
包里还带了 `使用说明.txt`、`常见问题FAQ.md`（exe 同级）与 `docs/`（9 篇）。

### B. 源码运行（开发）

```bash
pip install -e ".[web,desktop]"
python -m astercore                 # 零参数 = 启动器（向导 → 面板 → 自动开浏览器/内嵌窗口）
python -m astercore --port 9000 --no-browser --yes     # 启动器参数也可直接给
python -m astercore --cli --account 10001 --dry-run    # 强制命令行模式：离线自检内核+插件链路
python -m astercore --cli --account 10001 --ws ws://127.0.0.1:3001 --token xxx
```

> 入口规则：**无参数或只给启动器参数**（`--port/--host/--no-browser/--yes/--shell`）→ 启动器；
> 出现 CLI 专属参数（`--account/--dry-run/--serve-accounts/...`）或显式 `--cli` → 命令行模式。

## 质量闸门（改代码前请先看这段）

| 闸门 | 跑法 | 规模 |
|---|---|---|
| 单元/集成测试 | `python -m unittest discover -s tests -t .` | **487 项** |
| 面板静态自检（无需浏览器） | `python tools/check_panel_static.py` | **36 条** |
| 双端一致性（共用资产字节一致 + 不耦合某端 API/插件名） | Linux 仓 `python -m unittest tests.test_dual_end_assets` | 6 个共用文件 |
| 真实浏览器交互验证（需 playwright + GUI） | `python tools/verify_panel_ui.py` | 交互级 |
| 打包链路自检 | `tests/test_packaging.py` + CI 出包 | — |

**CI 会跑闸门**：`.github/workflows/windows-build.yml` 在打包**之前**执行静态自检 + 全量单测，
不过就不出包（tag `v*` 或手动 `workflow_dispatch` 触发）。详见
[docs/质量闸门与验证.md](docs/质量闸门与验证.md)（含各闸门的**边界**与发版前手工清单）。

```bash
python -m unittest discover -s tests    # 全量
python tools/check_panel_static.py      # 界面接线类回归（秒级）
python tools/e2e_frozen.py              # 打包产物端到端验收（建号→连后端→插件→回复）
python -m PyInstaller pack/astercore.spec --noconfirm --clean
```

## 架构

```
astercore.exe（pywebview 无边框桌面窗口 + 内嵌 RPC 通道）
 ├─ 内核（Python，跨平台）          事件/动作模型 · 插件加载器 · 账号运行时
 ├─ Backend 适配层（可插拔）        onebot(OneBot v11 WS) / null / lagrange(内置)
 ├─ 插件体系
 │   ├─ .py    开发态
 │   ├─ .pyd   Cython 发布（防小白修改，pack/build_pyd.py）
 │   └─ .dll/.so 原生 C-ABI（plugin-sdk/nap_plugin.h + C 示例）
 │       └─ 运行于 plugin-host 子进程（隔离 + 崩溃自动重启）
 ├─ 面板（Flask 单端口 JSON API + 共用前端）
 │   └─ 两端共用同一份 panel.js/panel.css（改动必须同步，见 test_dual_end_assets）
 ├─ 桌面壳 shell.py                 无边框窗口 · 托盘 · 单实例 · 三键/关闭行为 · 内嵌 RPC
 └─ 更新器 updater.py               检查 Release → 下载 → sha256 校验 → 暂存 → 重启替换 → 回滚
```

> **桌面端与浏览器端的传输差异**：桌面窗口用 `file://` 加载，请求经 `installFetchShim` 转到
> 进程内 RPC（`window.pywebview.api.rpc`）。因此**新接口必须同时注册** HTTP 路由与**固定路径**的
> hub 方法，且入参是 `{method, body}` 信封（由 `_reg_rpc` 统一拆包）——否则桌面上取不到值。

## 目录导览

| 路径 | 说明 |
|---|---|
| `src/astercore/core/` | 内核：models / plugin(SDK) / bus(加载+分发) / backend / runtime / manager / logring |
| `src/astercore/backends/` | onebot(WS) / null / lagrange（可插拔注册） |
| `src/astercore/web/` | Flask 面板：server / appapi / guide / setup / ui_pages / panel_pages + `static/` |
| `src/astercore/shell.py` | 桌面壳：无边框窗口、托盘、单实例、最小化/最大化、关闭行为 |
| `src/astercore/launch.py` | 启动与 RPC 注册（`_reg_rpc`：拆信封 + 统一 `{ok,data}` 形状） |
| `src/astercore/updater.py` | 应用内更新（含 sha256 校验与失败回滚） |
| `src/astercore/compat/` | 老插件兼容层（`utils.*` / `~/napcat/cache/images` 路径映射 + 一次性迁移） |
| `src/astercore/native.py` · `nativehost.py` · `host.py` | 原生插件加载与 plugin-host 子进程 |
| `src/astercore/bootstrap.py` · `paths.py` | 首启引导与统一路径（打包时基准=exe 所在目录） |
| `tools/check_panel_static.py` | 面板静态自检（含"死容器必须不存在"等机制断言） |
| `tools/verify_panel_ui.py` | 真实浏览器交互验证（需 playwright） |
| `tools/` | fake_onebot.py（假 OneBot 服务端）· e2e_frozen.py（打包产物验收）· fetch_ffmpeg.py |
| `pack/` | PyInstaller spec、build_pyd.py、version_info、图标、`使用说明.txt` |
| `docs/` | 用户文档与 FAQ、质量闸门、双端对齐、插件配置 schema 等（计划书本地私有） |
| `AGENT_SYNC.md` | 两个开发副本（本地 ↔ 服务端）之间的**交流板**：事实与决策都写在这里 |

## 插件 SDK（写插件从这里开始）

SDK **在仓库里、不进冻结包**（用户决定：包体保持精简；写插件请看仓库 —— 不要在 exe 里找）。

| 位置 | 适用 | 说明 |
|---|---|---|
| `plugin-sdk/python/` | **两端通用**（Windows 桌面版 + Linux 版） | 单文件零依赖 `nap_sdk.py`：契约仍是 Linux 老一套（模块级 `handle(event)` + `utils.*`），只多三样糖 —— 规则匹配、配置三段（**一次声明，两端 WebUI 都出表单**）、离线自测与可移植性检查（`sdk.check()` 会抓 `import astercore` / 类 ctx / 硬编码 data 路径） |
| `plugin-sdk/python/new_plugin.py` | 脚手架 | 生成插件骨架并把 SDK 一并拷过去 |
| `plugin-sdk/python/README.md` | 指南 | 《插件开发指南》（含两端一致性要求）· `example_plugin.py` 示例 |
| `plugin-sdk/cpp/` | **仅 Windows（原生 DLL）** | 单头文件 `nap_plugin.hpp`（C++11、零第三方依赖）：一行 `NAP_PLUGIN(你的类)` 生成全部 6 个导出；自带 `nap::Json`（含 `\uXXXX` 与代理对、深度上限、数字往返不丢精度）、`nap::Event`（取值/关键词/@我）、`nap::Host`（动作/日志/配置）与 `send_group`/`send_private`/`seg_text`/`seg_image`/`seg_at`/`logf`。配 `CMakeLists.txt`（INTERFACE 库 `nap_sdk`）、`build_msvc.bat` / `build_zig.bat`、`example/hello_plugin.cpp`（30 行完整示例）、`tests/test_sdk.cpp`（SDK 自测） |

**设计底线**：SDK **不 import astercore 内部**（同一份插件代码要能在两端跑）；缺 `utils` 时只装
**离线替身**、绝不覆盖真 `utils`（否则宿主里会静默用替身发消息）。

```bash
# C++ 原生插件：有 VS 时
cl /LD /O2 /MT /utf-8 /I plugin-sdk /I plugin-sdk/cpp /Fe:你的插件.dll 你的插件.cpp
# 没有 VS：python -m pip install ziglang 后跑 plugin-sdk/cpp/build_zig.bat

# 生成一个新插件（自动拷 SDK 到插件目录）
python plugin-sdk/python/new_plugin.py my_plugin --dir plugins
```

> ⚠ **原生插件两条硬要求**（不满足会静默出问题）：
> ① **必须 x64** —— 宿主进程是 x64，32 位 DLL **装不进去**（这也是易语言那一支被整体删除的原因：
> 易语言本体只能编 32 位；需要它请走 32 位桥接子进程或改用火山PC）。
> ② **必须开 UTF-8** —— MSVC 加 `/utf-8`，GCC/Clang 加 `-fexec-charset=UTF-8`；
> 否则中文字面量按系统 ANSI(GBK) 编，`has_keyword("你好")` **永远匹配不上且毫无报错**。
> SDK 里有 `nap::utf8_literals_ok()`，`init` 时会打 error 日志点名。
>
> 另：**事件形状**两种都认 —— 原生插件拿到的是 `type/raw/segments`（ABI 草案 §5），
> 老插件（Python legacy）是 OneBot 形状（`post_type/raw_message/message`）。
>
> 校验 DLL：`python plugin-sdk/tools/check_dll_exports.py <你的.dll>`（6 个必需导出 / ABI / name / version）。

## 插件开发（Python SDK）

**函数式插件**（最小）：

```python
# plugins/hello.py
from astercore.core.models import Event
from astercore.core.plugin import PluginMeta

__meta__ = PluginMeta(name="hello", name_cn="示例", version="0.1.0")

async def handle(event: Event):
    if event.type == "message" and event.text() == "ping":
        return True     # 有 ctx 时经 setup 注入发送能力（见 plugins/demo_hello.py）
    return False
```

**类插件**（带配置热更）：继承 `Plugin`，`on_load(config, ctx)` / `handle(event)` / `on_reload(config)`。

**原生 C 插件**：见 `plugin-sdk/examples/c-sample/`（编译 .so/.dll 放入插件目录即被统一加载器识别；
**平台不符的库会被拒绝**，避免 Windows 上误加载 Linux `.so` 触发"损坏的映像"）。

## 账号与数据

```
accounts/<id>/backend.json              # 连接配置（WS/HTTP/token、后端名）
plugins/*.py|.pyd|.dll                  # 共享插件目录（所有账号加载，面板可分别启停）
data/<id>/plugins/<name>/config.json    # 插件配置（面板可直接编辑，保存即热更）
data/runtime.json                       # 运行方式/向导状态/窗口关闭行为等
```

> 基准目录默认是 exe 所在目录（源码运行则为当前目录），可用环境变量 `ASTERCORE_HOME`
> 或 `--data-dir/--accounts-dir/--plugins-dir` 覆盖。

## 发版流程

```bash
# 1) 改版本号（4 处：__init__.py / pyproject.toml / pack/version_info.txt / pack/使用说明.txt）
# 2) 跑闸门：静态自检 + 全量单测（都绿）
# 3) 打 tag → CI 构建 → 自动发布 Release（zip + .sha256）
git tag -a vX.Y.Z -m "..." && git push origin refs/tags/vX.Y.Z
```

CI 步骤顺序：`Install deps → Static panel check → Run tests → 取 ffmpeg/Lagrange → Build exe →
Add docs（按 `.bak` 守卫）→ Package zip → Write checksums → Publish release`。

## 状态

**v0.6.30**：解压双击即可运行；建号 → 连后端 → 插件收发消息 → 面板看日志全链路已由打包产物
端到端验收覆盖，并且**从 v0.6.26 起 CI 在打包前跑质量闸门**（静态自检 + 全量单测）。

- 内核：事件/动作模型 · 多后端（onebot/null/lagrange）· 多账号 · 环形日志 · 插件三态
- 面板：概览 · 账号（横向列表 + 添加/编辑/启停/删除）· 插件配置 · 运行日志 · 连接引导 · 设置 · 关于与更新
- 桌面：无边框窗口 + 系统三键 + **关闭行为二选一（可记住）** · 托盘 · 单实例 · 双击顶栏最大化 · 文字可选中
- 引导：三步装 NapCat（**第①步用浏览器打开官方下载页**）· 一键下载解压 + 顶栏进度 · 一键建号并启动
- 更新：应用内检查 Release → 下载 → sha256 校验 → 重启替换 → 失败回滚
- 兼容：Linux 版老插件可直接跑（`utils.*` 兼容层 + 图片缓存目录映射与**一次性迁移**）
- 已知边界与待办：见 [docs/质量闸门与验证.md](docs/质量闸门与验证.md) 与 [AGENT_SYNC.md](AGENT_SYNC.md)

详见 [docs/PROGRESS.md](docs/PROGRESS.md)。
