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


def prepare_legacy_env(base_dir: Path) -> dict[str, object]:
    """让 Linux 版老插件能在本机正常跑的环境准备（零插件改动）

    1) 子进程编码：jm_downloader 用 utf-8 读子进程 stdout，而 Windows 子进程默认
       cp936 → 中文/emoji 进度行会丢；设 PYTHONIOENCODING/PYTHONUTF8 让子进程继承
    2) NapCat 图片缓存目录：老插件按 ~/napcat/cache/images 写文件，提前建好
    3) venv 解释器路径：老插件按 venv/bin/python 找解释器，Windows 是
       venv\Scripts\python.exe → 在 venv/bin/ 放一个同名副本（保留 pyvenv.cfg 解析）
    """
    info: dict[str, object] = {}

    # 1) 子进程编码
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    os.environ.setdefault("PYTHONUTF8", "1")
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

    # 3) venv/bin/python（仅 Windows 且存在实例 venv 时）
    if os.name == "nt":
        for venv in sorted(base_dir.glob("**/venv")):
            scripts = venv / "Scripts" / "python.exe"
            legacy = venv / "bin" / "python.exe"
            if scripts.exists() and not legacy.exists():
                try:
                    legacy.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(scripts, legacy)
                    info["venv_shim"] = str(legacy)
                    log.info("已为老插件准备解释器路径: %s", legacy)
                except OSError as e:
                    log.warning("venv 解释器副本创建失败: %s", e)
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
