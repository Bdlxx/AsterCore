# 栖星 AsterCore · 首启引导（bootstrap）
# 目标：用户解压后双击即可用 —— 自动建好目录、播种示例插件，不需要手动准备任何文件。
#
# 目录约定（均相对基准目录，见 paths.app_base_dir）：
#   data/       运行状态、账号数据、插件配置
#   accounts/   账号连接配置（accounts/<账号>/backend.json）
#   plugins/    插件目录：把 .py / .pyd / .dll 放这里即被所有账号加载

from __future__ import annotations

import logging
import os
import shutil
import sys
from pathlib import Path

log = logging.getLogger("astercore.bootstrap")

PLUGIN_SUFFIXES = (".py", ".pyd", ".so", ".dll")


def ensure_layout(base_dir: Path) -> dict[str, Path]:
    """创建 data/ accounts/ plugins/ 目录（幂等），返回各目录路径"""
    base = Path(base_dir)
    out = {
        "base": base,
        "data": base / "data",
        "accounts": base / "accounts",
        "plugins": base / "plugins",
    }
    for key in ("data", "accounts", "plugins"):
        out[key].mkdir(parents=True, exist_ok=True)
    return out


def has_plugins(plugin_dir: Path) -> bool:
    """目录里是否已有插件文件（忽略 _ 开头的辅助文件）"""
    d = Path(plugin_dir)
    if not d.is_dir():
        return False
    return any(p.suffix in PLUGIN_SUFFIXES and not p.name.startswith("_")
               for p in d.iterdir() if p.is_file())


def seed_example_plugins(plugin_dir: Path, templates_dir: Path | None = None,
                         force: bool = False) -> list[str]:
    """播种内置示例插件。

    仅当目录内没有任何插件时执行（避免覆盖用户自己的插件）；
    force=True 时忽略"已有插件"检查照常播种，但**同名文件永不覆盖**。
    返回被复制的文件名列表。
    """
    from astercore.paths import plugin_templates_dir

    dst = Path(plugin_dir)
    src = Path(templates_dir) if templates_dir else plugin_templates_dir()
    dst.mkdir(parents=True, exist_ok=True)

    if not force and has_plugins(dst):
        return []
    if not src.is_dir():
        log.debug("未找到示例插件模板目录: %s", src)
        return []

    copied: list[str] = []
    for f in sorted(src.iterdir()):
        if not f.is_file() or f.suffix != ".py":
            continue
        target = dst / f.name
        if target.exists():      # 永不覆盖（用户可能已改过示例）
            continue
        try:
            shutil.copy2(f, target)
            copied.append(f.name)
        except OSError as e:  # 只读目录等：不影响启动
            log.warning("示例插件复制失败 %s: %s", f.name, e)
    if copied:
        log.info("已播种示例插件到 %s: %s", dst, ", ".join(copied))
    return copied


# 视频解析核心（video_parser_core）的第三方依赖。
# 这些都在系统/打包解释器里，缺任何一个都会让解析功能在"导入期"静默降级，
# 所以启动时主动探测一次并报告，而不是等用户发链接才发现没反应。
PARSER_DEPS = ("aiohttp", "yt_dlp", "bilibili_api", "curl_cffi", "msgspec",
               "PIL", "json5", "bs4", "httpx", "aiofiles", "apscheduler")

FFMPEG_HINT = (
    "视频解析需要 ffmpeg（合并音视频/去水印会调用它）。"
    "任选一种：① 把 ffmpeg.exe 放到 <安装目录>/tools/ffmpeg/bin/；"
    "② 安装后加入 PATH；③ 在环境变量 FFMPEG_PATH 里写完整路径。")


def find_ffmpeg(base_dir: Path) -> str | None:
    """定位 ffmpeg，并把所在目录插到 PATH 最前面。

    老插件用的是**裸命令名**调用（video_parser_core/utils.py 里 cmd 的第一项就是
    字符串 "ffmpeg"），所以只要让它在 PATH 里能被找到，插件一行都不用改。
    查找顺序：FFMPEG_PATH → <基准目录>/tools/ffmpeg/bin → <基准目录>/tools/ffmpeg
              → 系统 PATH
    """
    exe = "ffmpeg.exe" if os.name == "nt" else "ffmpeg"
    cands: list[Path] = []
    env = os.environ.get("FFMPEG_PATH")
    if env:
        cands.append(Path(env).expanduser())
    cands += [Path(base_dir) / "tools" / "ffmpeg" / "bin" / exe,
              Path(base_dir) / "tools" / "ffmpeg" / exe]
    for c in cands:
        try:
            if c.is_file():
                os.environ["PATH"] = str(c.parent) + os.pathsep + os.environ.get("PATH", "")
                return str(c)
        except OSError:
            continue
    return shutil.which("ffmpeg")


def legacy_parser_deps() -> list[str]:
    """返回**缺失**的解析核心依赖模块名（空列表 = 依赖齐全）"""
    import importlib.util
    missing = []
    for name in PARSER_DEPS:
        try:
            if importlib.util.find_spec(name) is None:
                missing.append(name)
        except (ImportError, ValueError):
            missing.append(name)
    return missing


def _is_windows_exe(path: Path) -> bool:
    """文件是否存在且是 PE 可执行文件（MZ 头）。

    为什么不能只看 exists()：从 Linux 拷来的实例目录里 venv/bin/python 是 ELF，
    存在但不是 Windows 可执行文件，选中它会在 Popen 时报 WinError 193。
    """
    try:
        if not path.is_file():
            return False
        with path.open("rb") as f:
            return f.read(2) == b"MZ"
    except OSError:
        return False


def force_utf8_stdio() -> bool:
    """把本进程的 stdout/stderr 切成 UTF-8 并容错。

    老插件里有 `print("[parser] ⚠ …")` 这种非 GBK 字符（U+26A0/U+274C 都不在
    cp936 里），中文 Windows 的默认控制台编码会让 print 直接抛 UnicodeEncodeError；
    而插件是跑在**主进程的线程池**里的，这个异常会被上游的 except 吞掉，
    表现成"某个平台之后所有解析器都不再注册"这种极难排查的静默故障。
    设过 PYTHONUTF8 环境变量只对**之后启动的子进程**生效，改不了当前进程，所以这里
    显式 reconfigure 一次（errors="replace" 保证任何情况下都不再抛编码异常）。
    Linux 本来就是 UTF-8，行为不变。
    """
    ok = False
    for stream in (sys.stdout, sys.stderr):
        try:
            if stream is not None and hasattr(stream, "reconfigure"):
                stream.reconfigure(encoding="utf-8", errors="replace")
                ok = True
        except (ValueError, OSError):
            pass                                   # 已被重定向/已关闭：不致命
    return ok


def prepare_venv_shims(base_dir: Path, is_windows: bool | None = None) -> dict[str, object]:
    """为老插件准备 venv 解释器副本（仅 Windows 需要）。

    jm_downloader._venv_python() 探测的是 **venv/bin/python（不带扩展名）**，
    而 Windows 的 venv 里只有 Scripts/python.exe。两个名字都要放：
      · bin/python      —— 满足 os.path.exists 探测（stat 不会自动补 .exe）
      · bin/python.exe  —— CreateProcess 对无扩展名的名字会自动补 .exe，
                           所以实际执行的就是这个副本
    副本放在 bin/ 下，pyvenv.cfg 仍能按上级目录找到，解释器照常可用。

    ⚠ 必须**覆盖非 Windows 可执行文件**：从 Linux 拷来的实例目录里
      venv/bin/python 是 ELF，os.path.exists 为真会被插件选中，Popen 时报
      [WinError 193] %1 不是有效的 Win32 应用程序 —— 每单 JM 任务必失败。
    """
    if is_windows is None:
        is_windows = os.name == "nt"
    info: dict[str, object] = {}
    if not is_windows:
        return info

    for venv in sorted(Path(base_dir).glob("**/venv")):
        scripts = venv / "Scripts" / "python.exe"
        if not scripts.exists():
            # 只有 lib/ 没有 Scripts/ 的十有八九是从 Linux 拷来的 venv
            if (venv / "lib").is_dir():
                log.warning("实例 venv 是 Linux 的（缺 Scripts/python.exe）: %s —— "
                            "Windows 上无法使用，JM 下载/子进程会失败，建议删除后重建",
                            venv)
            continue
        made = []
        for name in ("python.exe", "python"):
            target_py = venv / "bin" / name
            if _is_windows_exe(target_py):
                continue                          # 已是可用的 Windows 副本
            try:
                target_py.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(scripts, target_py)
                made.append(str(target_py))
            except OSError as e:
                log.warning("venv 解释器副本创建失败 %s: %s", target_py, e)
        if made:
            info["venv_shim"] = made[0]
            log.info("已为老插件准备解释器路径: %s", ", ".join(made))
    return info


def prepare_legacy_env(base_dir: Path) -> dict[str, object]:
    """让 Linux 版老插件能在本机正常跑的环境准备（零插件改动）

    0) 基准目录：固化 ASTERCORE_HOME —— 老插件里按 __file__ 层级推算"仓库根"的
       代码（video_parser_core 的共享 cookie 池）用它兜底，避免在 Windows 上
       推到安装目录之外去建目录
    1) 子进程编码：jm_downloader 用 utf-8 读子进程 stdout，而 Windows 子进程默认
       cp936 → 中文/emoji 进度行会丢；设 PYTHONIOENCODING/PYTHONUTF8 让子进程继承
    2) NapCat 图片缓存目录：老插件按 ~/napcat/cache/images 写文件，提前建好
    3) venv 解释器路径：老插件按 venv/bin/python 找解释器，Windows 是
       venv\\Scripts\\python.exe → 在 venv/bin/ 放一个同名副本（保留 pyvenv.cfg 解析）
    4) ffmpeg：找到就把目录塞进 PATH；找不到只报告，不阻塞启动
    5) 解析依赖自检：缺依赖时明确告警（否则功能是"静默半死"）
    """
    info: dict[str, object] = {}

    # 0) 基准目录固化
    try:
        os.environ.setdefault("ASTERCORE_HOME", str(Path(base_dir).resolve()))
        info["home"] = os.environ["ASTERCORE_HOME"]
    except OSError as e:
        log.warning("基准目录固化失败: %s", e)

    # 1) 子进程编码（环境变量只影响之后启动的子进程）
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    os.environ.setdefault("PYTHONUTF8", "1")
    # 本进程自己的编码要单独修：插件在主进程线程池里 print 非 GBK 字符会抛异常
    info["stdio_utf8"] = force_utf8_stdio()
    info["env_encoding"] = True

    # 2) 缓存目录
    try:
        from astercore.compat.paths import default_cache_host
        cache = default_cache_host()
        cache.mkdir(parents=True, exist_ok=True)
        info["cache_dir"] = str(cache)
    except OSError as e:
        log.warning("NapCat 缓存目录创建失败: %s", e)
        info["cache_dir"] = None

    # 3) venv 解释器路径（仅 Windows；细节见 prepare_venv_shims）
    info.update(prepare_venv_shims(base_dir))

    # 4) ffmpeg：老插件用裸命令名调用，找到就把目录塞进 PATH 最前面
    try:
        ff = find_ffmpeg(base_dir)
        info["ffmpeg"] = ff
        if ff:
            log.info("视频解析将使用 ffmpeg: %s", ff)
        else:
            log.warning("未找到 ffmpeg —— 视频解析无法合并音视频。%s", FFMPEG_HINT)
    except Exception as e:                     # 探测本身绝不能让启动失败
        log.warning("ffmpeg 探测失败: %s", e)
        info["ffmpeg"] = None

    # 5) 解析核心依赖自检：缺依赖时功能会"静默半死"，这里明确说出来
    try:
        missing = legacy_parser_deps()
        info["parser_deps_missing"] = missing
        if missing:
            log.warning("视频解析核心缺少依赖 %s —— 相关链接将无法解析（回退旧逻辑）",
                        ", ".join(missing))
    except Exception as e:
        log.warning("解析依赖自检失败: %s", e)
        info["parser_deps_missing"] = None

    return info


def bootstrap(base_dir: Path, plugin_dir: Path | None = None) -> dict[str, object]:
    """首启引导总入口：建目录 + 播种示例插件 + 兼容环境准备"""
    dirs = ensure_layout(base_dir)
    target = Path(plugin_dir) if plugin_dir else dirs["plugins"]
    seeded = seed_example_plugins(target)
    dirs["seeded"] = seeded
    dirs["plugin_dir"] = target
    dirs.update(prepare_legacy_env(base_dir))
    return dirs
