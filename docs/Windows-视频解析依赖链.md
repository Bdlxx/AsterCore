# Windows 移植 · 视频解析依赖链专题

> 2026-09-26 调研 + 落地记录。对应《双端对齐.md》§4 的 P0 #6/#7/#8
> —— 也就是"不做在 Windows 上一定坏"清单里最后三条，本次全部有了结论。
>
> 一句话结论：**依赖链在 Windows 上完全可行（36 个包全部有现成轮子，无需编译器）；
> 真正会坏的不是依赖，而是老插件里写死的 Linux 绝对路径。**

---

## 1. 依赖链实测：可行（原先的最大不确定性已消除）

方法：用 pip 以 **Windows 平台 + CPython 3.12** 为约束，把整条依赖链的轮子下载下来看
有没有需要现场编译的包（这是"没实测过 Windows wheel 成功率"那个待办的正解）。

```bash
python3 -m pip download --only-binary=:all: --platform win_amd64 --python-version 3.12 <包…>
```

**结果：36 个包，全部是 `*win_amd64.whl` 或 `py3-none-any.whl`，零源码编译。**

两个本来最可能翻车的二进制包反而最稳：

| 包 | Windows 轮子 | 说明 |
|---|---|---|
| `curl_cffi` | `curl_cffi-0.16.3-cp310-abi3-win_amd64.whl` | **abi3**，一套轮子通吃 cp310+ |
| `msgspec` | `msgspec-0.21.1-cp312-cp312-win_amd64.whl` | 有 cp312 正式轮子 |

### 唯一的坑：`qrcode-terminal` 只有 sdist

`bilibili-api-python` 依赖 `qrcode_terminal`，而它在 PyPI 上**只有 sdist**：

```
$ pip download --only-binary=:all: --platform win_amd64 … bilibili-api-python
ERROR: Could not find a version that satisfies the requirement qrcode_terminal (from versions: none)
```

危害等级：**低**。它是个 1.7 kB 的纯 Python `setup.py` 包，Windows 上 `pip install` 现场
构建即可（不需要编译器），只是**不能用 `--only-binary=:all:` 一把梭**。构建脚本要写成
"先装其余全部（only-binary），再单独允许 sdist 装 qrcode-terminal"。

### 体积

| 项 | 值 |
|---|---|
| 下载包总体积 | **21 MB**（36 个包） |
| 最大三项 | pillow 6.9M · lxml 3.9M · yt-dlp 3.1M |
| 现有冻结产物 | 53 MB（`dist/astercore`） |
| 预估加入后 | 约 140–170 MB（onedir zip） |

### 版本锚定（与 Linux 线上一致）

```
aiohttp==3.14.3        curl_cffi==0.16.2      msgspec==0.21.1
yt-dlp==2026.8.19      bilibili-api-python==17.4.2
pillow==12.3.0         json5==0.15.0          beautifulsoup4==4.15.0
httpx==0.28.1          aiofiles==25.1.0       APScheduler==3.11.3
```

> ⚠️ **包名别写错**：必须用 `bilibili-api-python`（提供 `bilibili_api` 模块，
> 线上是 17.4.2）。PyPI 上还有个老包叫 `bilibili_api`（9.1.0），名字相近但完全不是
> 同一个东西 —— 我第一次就下错了，`--only-binary` 会"成功"给你一个 9.1.0。

---

## 2. 真正的问题：老插件里写死的 Linux 绝对路径

这类缺陷的可怕之处是 **Windows 上不报错**：

```python
os.makedirs("/root/napcat/cache/images")   # 真的建出 C:\root\napcat\cache\images
open("C:/root/.../a.jpg", "w")             # 真的写得进去
```

以 `/` 开头的路径在 Windows 上被当作「当前盘根目录」，于是探测"目录可不可写"永远成功、
文件也确实写下去了 —— 只是写到了 NapCat 根本不会去读的地方。而**发送**时用的
`/app/cache/images/…` 会被兼容层按前缀映射到 `%USERPROFILE%\napcat\cache\images\…`。
两边不是同一个目录，现象就是「解析成功、下载成功，就是发不出去」。

### 已修的 4 处（两个实例同步，Linux 行为逐字节不变）

| 文件 | 问题 | 修法 |
|---|---|---|
| `parser_bridge.py` `get_cache_dir()` | 按 QQ 硬编码 `/root/napcat{,2}/cache/images` | ①显式 `NAPCAT_CACHE_HOST` ②仅**非 Windows 且目录真实存在**才用老路径 ③否则 `~/napcat/cache/images` |
| `marry.py` `_get_cache_dir()` | 同上（头像缓存） | 同上（三分支语义在 Linux 上逐字未变） |
| `jm_downloader.py` `_napcat_shared_candidates()` | 老路径排在**第一个候选**，且探测会被绕过 | Windows 直接返回 `[(本机目录, 本机目录)]` |
| `jm_downloader.py` 发 PDF | 走 **NapCat HTTP 直发**，不经过兼容层出口映射 | Windows 下"写入目录=发送路径"，并用 `_send_path()` 按平台选分隔符 |

> **为什么 jm 单独特殊**：其它插件的文件路径都经 `utils.api.send_message` 出去，
> 兼容层在那里统一做映射；jm 的 PDF 是 `requests.post` 直连 NapCat HTTP 拿
> `message_id`（撤回要用），**绕过了映射**，所以必须把"发送路径"本身弄对。

### 已验证（不是推断）

- **Linux 零回归**：把改动前的旧逻辑原样重写一遍，与改动后的真实函数逐项比对
  —— `parser_bridge` / `marry` / `jm` 候选列表（顺序+内容）/ 容器路径拼接 /
  `_venv_python()` / cookie 根，**6 项全部一致**。
- **Windows 语义**：在 Linux 上模拟 `_is_windows()`，断言三个插件都不再出现
  `/root/napcat`，且落点是 `~/napcat/cache/images`。
- **收敛性（最关键）**：Windows 模拟下调用真实的 `compat.paths.map_segments`，
  断言"解析核心写盘的那个文件" == "兼容层映射后要读的路径"，且文件真实存在。
  —— 只修一半（写盘或发送）都不算修好，这条断言把两半钉在一起。
- 以上全部固化为 `tests/test_windows_paths.py`（10 项）。

---

## 3. ffmpeg（P0 #6）

老插件用**裸命令名**调用（`video_parser_core/utils.py` 里 `cmd[0]` 就是字符串
`"ffmpeg"`），所以**不需要改插件**，只要让它在 PATH 里能被找到：

`bootstrap.find_ffmpeg()` 的查找顺序 → 找到就把所在目录**插到 PATH 最前面**：

1. 环境变量 `FFMPEG_PATH`
2. `<安装目录>/tools/ffmpeg/bin/ffmpeg.exe`（随包分发推荐）
3. `<安装目录>/tools/ffmpeg/ffmpeg.exe`
4. 系统 PATH

找不到**不阻塞启动**，只告警；启动自检结果在 `info["ffmpeg"]` 里，可给面板显示。

---

## 4. 其它已落地的兼容层加固

| 项 | 内容 |
|---|---|
| 基准目录固化 | `prepare_legacy_env` 设 `ASTERCORE_HOME`；解析核心的共享 cookie 池
（5 层 `dirname` 推算仓库根）改用它兜底，避免在 Windows 上推到安装目录**之外** |
| 依赖自检 | 启动时 `legacy_parser_deps()` 探一遍 11 个依赖，缺了明确告警（否则是"静默半死"） |
| stdout 编码 | `force_utf8_stdio()`：插件里有 `print("⚠ …")`，U+26A0/U+274C **不在 GBK 里**，中文 Windows 上 print 会抛 `UnicodeEncodeError`，而插件跑在主进程线程池里，异常被上游吞掉 → 表现为"某平台之后所有解析器不再注册"。设 `PYTHONUTF8` 只影响**子进程**，改不了当前进程，所以必须显式 reconfigure |
| venv 解释器副本 | `jm_downloader._venv_python()` 探测的是 `venv/bin/python`（**不带扩展名**），Windows venv 里只有 `Scripts/python.exe` → 兼容层两个名字都放；且**必须覆盖非 PE 文件**（从 Linux 拷来的 `bin/python` 是 ELF，`os.path.exists` 为真会被选中，`Popen` 报 WinError 193） |
| 冻结版禁止自我重启 | `_venv_python()` 在 `sys.frozen` 且无 venv 时返回 `None`（不再退回 `sys.executable`）—— 那时它是主程序自己，拿它跑 `-c` / `-m pip` 等于**再启动一份 AsterCore**（莫名多开一个面板） |

---

## 5. 打包配方（Windows 构建时照做）

1. `pyproject.toml` 加一个可选依赖组（如 `parser`），pin 上 §1 的版本。
2. PyInstaller spec：
   - `collect_all("yt_dlp")`、`collect_submodules("bilibili_api")`（动态导入子模块）
   - `curl_cffi` / `msgspec` / `lxml` / `PIL` 的二进制要收
   - 继续显式收 `web/static`（历史上漏过一次，面板 404）
3. 随包放 `tools/ffmpeg/bin/ffmpeg.exe`（`find_ffmpeg` 会自动认）。
4. 装 `qrcode-terminal` 时允许 sdist（见 §1）。

---

## 6. Backlog（审计发现、本次**未修**，按影响排序）

来自对 7 个插件 + `video_parser_core/`（33 个 parser 文件）的逐行审计，
`__pycache__` 忽略；已排除的"非问题"不在此列。

| 优先级 | 位置 | 问题 | 影响 |
|---|---|---|---|
| 高 | `parsers/__init__.py` + `download.py` | 19 行**无条件** import 16 个平台，任一依赖缺失 → 整包 ImportError | 视频解析整体不可用，且现场只留一行 error（现已有启动自检告警缓解） |
| 高 | `cookie.py:74-80`、`main.py:57-59,78` | cookie 落盘 `cj.save()` **无异常保护**，却被 13 个解析器的 `__init__` 调用 | Windows「文件被占用/OneDrive/只读」抛 OSError → `_register_parser` 中断，**该平台之后所有平台永不注册**，静默到重启 |
| 中 | `parsers/instagram.py:38` | 用 `sys.executable` 跑 `-m gallery_dl` | 冻结版 → 再启动一份 AsterCore；IG 图集 100% 失败 |
| 中 | `utils.py:57` | `stderr.decode()` 用 utf-8 解 Windows 本地编码输出 | 可能 `UnicodeDecodeError` 掩盖真实失败原因（正好是最需要日志的合流失败） |
| 中 | `jm_downloader.py` 清理定时器 | `threading.Timer` 未设 daemon（600s） | 退出时进程最多多挂 10 分钟 |
| 中 | `parsers/zhihu/card.py:299` | `datetime.fromtimestamp(ts, tz=<字符串>)`，TypeError 被 except 吞 | 知乎"发布时间"永远静默缺失（非 Windows 特有） |
| 低 | `parsers/bilibili/login.py:32,41`、`pixiv.py:506` | `write_text/read_text` 未指定 encoding / 未固定 `newline` | 边缘场景；CRLF 会让双端产物字节不一致 |
| 低 | `parsers/instagram.py:348,462` | 文件名后缀直接取自远端 URL | 后缀含 `* ? " < >` 时 `[WinError 123]`（当前 CDN 安全） |
| 低 | `parsers/iwara.py` | `curl_cffi.AsyncSession` 在 Proactor 下依赖新版兜底 | 旧版会 `loop.add_reader` 报错 → iwara 不可用；Windows 侧 pin `curl_cffi>=0.6` |
| 低 | `instances/<QQ>/venv`（Linux 拷来） | venv 是 Linux 的（无 `Scripts/python.exe`） | 已加启动告警；需删除重建才能用 JM |

### 已排查确认**不是**问题（省人天）

- 非法文件名 / Windows 保留名 / 超长路径：所有落盘名都是 **md5 哈希或平台 ID**
  （`md5(url)[:16]+suffix`、`acfun_{acid}.mp4`、`pixiv_{pid}.pdf`、`{bvid}-{page}.mp4`），
  全树**没有**"用视频标题拼文件名"的代码 → 结构上撞不到。
- `os.replace` / `os.rename` / `tempfile` / `chmod` / `geteuid` / `pwd` / `grp` /
  `fcntl` / `select` / `termios` / `signal` / `multiprocessing` / 软链接：插件树**零命中**。
- 唯一 `Path.replace` 在 `utils.py:100`，同目录不跨盘。
- 除 `jm_downloader` 外**没有**别处用 `os.path.join` 拼容器路径。
- `/tmp`、`/data`、`/etc` 字面量：零命中；`/` 开头字面量共 12 处，其余都是 URL 片段。
- `%a %b` 时间解析、`socket.inet_aton`：Windows 上均可用。

---

*相关：`docs/双端对齐.md`（平面清单）· `docs/PROGRESS.md`（总进度）·
`tests/test_windows_paths.py` · `tests/test_bootstrap.py`*
