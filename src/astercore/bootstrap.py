# 栖星 AsterCore · 首启引导（bootstrap）
# 目标：用户解压后双击即可用 —— 自动建好目录、播种示例插件，不需要手动准备任何文件。
#
# 目录约定（均相对基准目录，见 paths.app_base_dir）：
#   data/       运行状态、账号数据、插件配置
#   accounts/   账号连接配置（accounts/<账号>/backend.json）
#   plugins/    插件目录：把 .py / .pyd / .dll 放这里即被所有账号加载

from __future__ import annotations

import logging
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


def bootstrap(base_dir: Path, plugin_dir: Path | None = None) -> dict[str, object]:
    """首启引导总入口：建目录 + 播种示例插件，返回结果摘要"""
    dirs = ensure_layout(base_dir)
    target = Path(plugin_dir) if plugin_dir else dirs["plugins"]
    seeded = seed_example_plugins(target)
    dirs["seeded"] = seeded
    dirs["plugin_dir"] = target
    return dirs
