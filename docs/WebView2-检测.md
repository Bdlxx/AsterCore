# WebView2 支持检测 · 实现说明

> 对着《WebView2 支持检测说明》实现。**判定逻辑全部在 Linux 上单测过**（34 项），
> 需要真机确认的部分单列在 §5 —— 别把"没测过"当"没问题"。

---

## 1. 检测分层（`src/astercore/webview2.py`）

| 层 | 判据 | 实现 | 关键点 |
|---|---|---|---|
| ① 系统 | Windows **build 号** | `judge_os(build)` | 用 `sys.getwindowsversion().build`，**不用 `platform.release()`** —— 它在 Python 3.12 之前对 Win11 返回 `'10'`（说明 §7.1） |
| ② Runtime 是否安装 | 注册表 `pv` 字段 | `read_runtime_version()` | 三个位置依次试：HKLM\WOW6432Node、HKLM、HKCU；`pv` 为空或 `0.0.0.0` = **未安装**（说明 §2） |
| ③ 版本是否达标 | 主版本号 | `judge_runtime(v, min_major)` | Win7 SP1 / Server 2012 R2 → 109，其他 → 92 |

判定表逐行钉在 `tests/test_webview2.py::OsLayerTest.CASES`：

```
22631/22000 → 通过（Win11）      19045 → 通过（Win10 22H2）
17134       → 通过（1803 恰好达标） 17133 → 不通过（1803 之前）
10240       → 不通过（Win10 1507）  9600 → 不通过（Win8.1）
9200        → 不通过（Win8）        7601 → 通过但受限（Win7 SP1）
7600        → 不通过                2600 → 不通过（XP）
```

> 一个规范细节：`min_major_for(9600)` 返回 **109** 而不是 92 —— 因为
> **Server 2012 R2 就是 build 9600**（Win8.1 的 Server SKU），说明 §2 第三层
> 把「Windows 7 SP1 / Server 2012 R2」并列成 109 那一档。9600 在①层已经不通过，
> 所以这个值只在展示时可见。

## 2. 四种结果（说明 §3）

`detect()` 返回 `Wv2Status(ok, code, reason, build, os_label, runtime, min_major)`：

| code | 含义 | 处理 |
|---|---|---|
| `ok` | 三层全过 | 内嵌窗口 |
| `os_too_old` | 系统版本不够 | 浏览器模式 + 弹窗说明（**不给安装链接**，给了是误导） |
| `runtime_missing` | 未装 Runtime | 浏览器模式 + 弹窗（**带下载入口**） |
| `version_too_old` | Runtime 太旧 | 浏览器模式 + 弹窗（说明需要 92/109+） |
| `not_windows` | 非 Windows | 有 pywebview 就直接用（检测不适用） |

`Wv2Status.needs_install` 专门标出"装一下就有更好体验"的两种 —— 对应说明 §3
那句**「Runtime 未安装时，不要静默降级」**。

①层不过时**直接短路**，不去读注册表（省时间；测试里用"reader 是否被调用"钉住）。

## 3. transport 配置项（说明 §四）

`runtime.json` 的 `extra.transport = {"mode": "auto|webview|http"}`：

- `auto`：走检测（默认）
- `webview` / `http`：**跳过检测**直接指定
- 值非法（手改配置写坏）→ 读取时退回 `auto`，**不把非法值当命令执行**

对外可用 `--shell` / `--no-shell` 单次覆盖，与配置项的区别是：命令行是"这一次"，
配置是持久的。

## 4. 降级体验（说明 §五）

| 要求 | 实现 |
|---|---|
| 5.1 **明确告知** | `nativeui.message_box()` → Windows 用 `ctypes` 调 `user32.MessageBoxW`（`MB_TOPMOST|MB_SETFOREGROUND`，免得被控制台盖住）。内容含：为什么降级 + 可复制的面板地址 + 如何安装 WebView2（附官方下载地址） |
| 5.2 **端口策略** | **本地回退**：`127.0.0.1` + **随机端口**（`_random_free_port`），绝不占用用户配置的那个端口、不对外；**主动开 Web**：用户配置的 host/port + 强制密码。两条路径在代码里是分开的分支 |
| 5.3 **托盘常驻** | HTTP 模式下即使没有窗口也起托盘（「打开面板」/「退出」）；托盘「退出」通过 `loop.call_soon_threadsafe` 唤醒主循环。没有托盘组件时只提示"用 Ctrl+C 退出"，**绝不伪装成能托盘** |

**一个我自己加的约束**：降级弹窗**只在"本地回退"时弹**，不在"用户主动开了 Web 服务"时弹。
理由有两条 —— ① 用户主动开 Web 时，浏览器模式本来就是他要的结果，再解释一遍是噪音；
② 弹窗是阻塞的，无头环境（CI/打包验收）里没人点会一直卡住。
`nativeui.dialogs_disabled()` 另外认 `ASTER_NO_DIALOG` / `ASTER_NO_OPEN` 两个环境变量兜底。

## 5. ⚠️ 需要真机验证的部分（本机是 Linux，测不了）

判定逻辑全部单测过，但**下面这些是 Windows 专有的"接线"**，只能在真机确认：

| 项 | 为什么测不了 | 怎么验（1 分钟） |
|---|---|---|
| 注册表读取 | 本机没有 `winreg` | Win 上 `python -c "from astercore.webview2 import read_runtime_version as r; print(r())"` —— 装了 Runtime 应打印版本号，未装打印 `None` |
| Win11 build 号 | 本机 `sys.getwindowsversion` 不存在 | Win11 上应得 `22000+`（若打印 19045 说明拿错了 API） |
| 原生弹窗外观 | 需要交互式桌面 | 临时把 `transport` 设成让检测失败的场景，或直接调 `nativeui.message_box("t","c")` |
| ARM64 设备 | 没有设备 | 说明 §7.2 已注明未覆盖，只做提示 |
| Server Core | 无交互桌面 | 说明 §7.3 列为不针对；行为上会因「未装 Runtime」自然降级到 HTTP —— 这正是规范要的结果 |

## 6. 还没做的（说明里提到但本轮不涉及）

- **ARM64 注册表路径**（说明 §7.2）：目前 `arch_note()` 只提示，不阻断。真机支持时补查询路径即可。
- **面板里显示系统信息**（说明 §7.1 提到的副作用）：`Wv2Status` 已经带 `os_label`/`build`，
  面板要显示时直接调 `webview2.summary()` 即可 —— 本轮只做到日志输出。
- **windowed 模式**（无控制台窗口）：仍需先做 `sys.stdout` 兜底并在真机验证，见施工对照表。

---

*相关：`docs/桌面版施工对照.md` · `tests/test_webview2.py`*
