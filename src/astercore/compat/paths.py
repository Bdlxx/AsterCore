# 栖星 AsterCore · 老插件路径兼容
# 老插件把"NapCat 缓存目录"硬编码成了两种形态（Linux + Docker 时代的产物）：
#   写入：宿主目录  ~/napcat/cache/images 或 /root/napcat/cache/images
#   发送：容器路径  /app/cache/images/xxx.png
# Windows 上 NapCat 是原生程序，读的是本机路径，因此发出去的 /app/cache/images
# 必须映射成 NapCat 真正能读到的目录。
#
# 设计原则：**不改插件**（Linux/Windows 双端用同一份插件代码），只在兼容层出口
# 做一次映射，且默认"不改行为"：
#   · Linux + Docker：保持容器路径原样（线上现状，插件硬编码的就是对的）
#   · Windows 原生 NapCat：默认映射 /app/cache/images → %USERPROFILE%\napcat\cache\images
# 需要时可在账号 config.json 里显式指定：
#   NAPCAT_CACHE_PREFIX : 插件发出的前缀（默认 /app/cache/images）
#   NAPCAT_CACHE_HOST   : 本机真实目录（Windows 默认 %USERPROFILE%\napcat\cache\images）

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

log = logging.getLogger("astercore.compat.paths")

DEFAULT_PREFIX = "/app/cache/images"


def is_windows() -> bool:
    return os.name == "nt" or sys.platform.startswith("win")


def default_cache_host() -> Path:
    """本机 NapCat 图片缓存目录（老插件写文件也在这里）"""
    env = os.environ.get("ASTERCORE_NAPCAT_CACHE")
    if env:
        return Path(env).expanduser()
    return Path.home() / "napcat" / "cache" / "images"


def _cfg(rt, key: str, default=None):
    try:
        from astercore.compat.utils_pkg import _config_dict
        return _config_dict(rt).get(key, default)
    except Exception:
        return default


def path_map(rt) -> list[tuple[str, str]]:
    """返回 [(插件发出的前缀, 本机真实目录)]；空列表表示不做映射"""
    prefix = str(_cfg(rt, "NAPCAT_CACHE_PREFIX", DEFAULT_PREFIX) or "")
    host = str(_cfg(rt, "NAPCAT_CACHE_HOST", "") or "")
    explicit = bool(prefix and host)

    if not explicit:
        if not is_windows():
            return []                       # Linux/Docker：容器路径本来就是对的
        prefix, host = DEFAULT_PREFIX, str(default_cache_host())
    if not prefix or not host or prefix == host:
        return []
    return [(prefix, host)]


def map_path(value: str, rt) -> str:
    """把单条路径里的容器前缀换成本机目录（其余原样）"""
    if not isinstance(value, str) or not value:
        return value
    for prefix, host in path_map(rt):
        if value.startswith(prefix):
            rest = value[len(prefix):].lstrip("/\\")
            base = host.rstrip("/\\")
            if is_windows():
                return base + "\\" + rest.replace("/", "\\")
            return base + "/" + rest
    return value


def map_segments(segments: list[dict], rt) -> list[dict]:
    """映射消息段里的 file 字段（image/video/record/file 段）"""
    mapping = path_map(rt)
    if not mapping:
        return segments
    out = []
    for seg in segments or []:
        if isinstance(seg, dict) and isinstance(seg.get("data"), dict) \
                and isinstance(seg["data"].get("file"), str):
            seg = {"type": seg.get("type"), "data": dict(seg["data"])}
            seg["data"]["file"] = map_path(seg["data"]["file"], rt)
        out.append(seg)
    return out


def ensure_cache_dir(rt=None) -> Path | None:
    """确保本机缓存目录存在（老插件会在里面写图片/PDF）"""
    d = Path(str(_cfg(rt, "NAPCAT_CACHE_HOST", "") or "") or default_cache_host())
    try:
        d.mkdir(parents=True, exist_ok=True)
        return d
    except OSError as e:
        log.warning("缓存目录不可用 %s: %s", d, e)
        return None
