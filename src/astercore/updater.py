# 栖星 AsterCore · 应用内更新器（计划书 §369）
#
# 流程：检查新版本 → 下载 → **sha256 校验** → 暂存 → 重启替换 → **失败回滚**。
#
# 为什么必须两阶段（Windows 上无法替换正在运行的 exe）：
#   ① 退出前把新版解压到 `<install 同级>/<install>_new`
#   ② 写一个交接脚本：等本进程退出 → 旧目录改名 `_old` → `_new` 改名为正式 → 拉起
#   ③ 新版本**启动成功后**写 `.boot-ok` 标记；下次启动若发现标记缺失且存在 `_old`
#      → 说明新版起不来 → 自动把 `_old` 还原回去（回滚）
#
# 只依赖标准库：早期版本要能在没装任何第三方包的情况下检查更新。

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import tempfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from typing import Any, Callable

log = logging.getLogger("astercore.updater")

REPO = "Bdlxx/AsterCore"
API_LATEST = f"https://api.github.com/repos/{REPO}/releases/latest"
ASSET_SUFFIX = ".zip"
CHECKSUM_SUFFIX = ".sha256"
BOOT_OK = ".boot-ok"
STAGED = ".update-staged.json"
EXE_NAME = "astercore.exe"


# ---------------------------------------------------------------- 版本比较
def parse_version(text: str) -> tuple[int, ...]:
    """'v0.6.9' / '0.6.9' / '0.6.9-beta1' → (0, 6, 9)；无法解析的部分忽略"""
    nums = re.findall(r"\d+", str(text or ""))
    return tuple(int(n) for n in nums[:4]) or (0,)


def is_newer(candidate: str, current: str) -> bool:
    a, b = parse_version(candidate), parse_version(current)
    n = max(len(a), len(b))
    a = a + (0,) * (n - len(a))
    b = b + (0,) * (n - len(b))
    return a > b


# ---------------------------------------------------------------- HTTP（可注入，便于测试）
def _urlopen(url: str, timeout: float = 20.0):
    req = urllib.request.Request(url, headers={
        "User-Agent": f"AsterCore-updater/{__import__('astercore').__version__}",
        "Accept": "application/vnd.github+json",
    })
    return urllib.request.urlopen(req, timeout=timeout)      # noqa: S310 固定 https


def http_json(url: str, timeout: float = 20.0) -> dict:
    with _urlopen(url, timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def http_text(url: str, timeout: float = 20.0) -> str:
    with _urlopen(url, timeout) as r:
        return r.read().decode("utf-8", "replace").strip()


def http_download(url: str, dest: Path, on_progress: Callable[[int, int], None] | None = None,
                  timeout: float = 60.0, chunk: int = 1 << 16) -> tuple[str, int]:
    """流式下载并**边下边算 sha256**；返回 (sha256, 字节数)"""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    h = hashlib.sha256()
    total = 0
    with _urlopen(url, timeout) as r:
        size = int(r.headers.get("Content-Length") or 0)
        with open(dest, "wb") as f:
            while True:
                buf = r.read(chunk)
                if not buf:
                    break
                f.write(buf)
                h.update(buf)
                total += len(buf)
                if on_progress:
                    try:
                        on_progress(total, size)
                    except Exception:                    # noqa: BLE001 进度回调不许影响下载
                        pass
    return h.hexdigest(), total


def sha256_file(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for buf in iter(lambda: f.read(1 << 20), b""):
            h.update(buf)
    return h.hexdigest()


# ---------------------------------------------------------------- 检查更新
def check(current_version: str, timeout: float = 20.0) -> dict[str, Any]:
    """读 GitHub Releases/latest，返回是否有新版（不抛异常，把错误塞进 error）"""
    out: dict[str, Any] = {"available": False, "current": str(current_version), "error": ""}
    try:
        rel = http_json(API_LATEST, timeout)
    except Exception as e:                            # noqa: BLE001 网络问题只提示不崩
        out["error"] = f"{type(e).__name__}: {e}"
        return out
    tag = str(rel.get("tag_name") or "")
    out["version"] = tag.lstrip("v")
    out["notes"] = str(rel.get("body") or "")[:2000]
    out["page"] = str(rel.get("html_url") or "")
    for a in rel.get("assets") or []:
        name = str(a.get("name") or "")
        if name.endswith(ASSET_SUFFIX) and EXE_NAME.split(".")[0] in name.lower() + "astercore":
            out["asset_name"] = name
            out["asset_url"] = a.get("browser_download_url")
            out["asset_size"] = int(a.get("size") or 0)
            break
    # 校验文件（CI 会为每个 zip 附一份 <zip>.sha256）
    out["sha256"] = None
    if out.get("asset_url"):
        try:
            txt = http_text(str(out["asset_url"]) + CHECKSUM_SUFFIX, timeout)
            m = re.search(r"\b([0-9a-fA-F]{64})\b", txt)
            if m:
                out["sha256"] = m.group(1).lower()
        except Exception:                             # noqa: BLE001 没附校验文件就如实说
            log.info("Release 未附 %s，跳过校验值预取", CHECKSUM_SUFFIX)
    out["available"] = bool(out.get("asset_url")) and is_newer(out["version"] or "0", current_version)
    return out


# ---------------------------------------------------------------- 暂存
def stage(zip_path: str | Path, install_dir: str | Path,
          on_progress: Callable[[int, int], None] | None = None) -> dict:
    """把新版解压到 `<install>_new`（不碰正式目录），写暂存标记"""
    install = Path(install_dir).resolve()
    new_dir = install.parent / (install.name + "_new")
    if new_dir.exists():
        shutil.rmtree(new_dir, ignore_errors=True)
    new_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as z:
        names = z.namelist()
        total = max(1, len(names))
        for i, name in enumerate(names, 1):
            # zip-slip 防护：目标必须落在 new_dir 内
            target = (new_dir / name).resolve()
            if not str(target).startswith(str(new_dir)):
                raise ValueError(f"压缩包内含越界路径: {name}")
            z.extract(name, new_dir)
            if on_progress:
                on_progress(i, total)
    # 有些包多一层根目录 → 拍平
    children = [p for p in new_dir.iterdir()]
    if len(children) == 1 and children[0].is_dir() and not (new_dir / EXE_NAME).exists():
        inner = children[0]
        for p in list(inner.iterdir()):
            shutil.move(str(p), str(new_dir / p.name))
        inner.rmdir()
    ok = (new_dir / EXE_NAME).is_file()
    meta = {"staged_dir": str(new_dir), "install_dir": str(install), "has_exe": ok}
    (install.parent / (install.name + "_new" + ".json")).write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    if not ok:
        raise ValueError(f"暂存目录里没有 {EXE_NAME}，包结构不对：{new_dir}")
    return meta


def write_handoff(install_dir: str | Path, script_path: str | Path | None = None,
                  exe_name: str = EXE_NAME) -> Path:
    """写交接脚本：等进程退出 → 旧目录改名 _old → _new 改名为正式 → 拉起"""
    install = Path(install_dir).resolve()
    new_dir = install.parent / (install.name + "_new")
    old_dir = install.parent / (install.name + "_old")
    script = Path(script_path) if script_path else (install.parent / "apply-update.bat")
    script.write_text(f"""@echo off
chcp 65001 >nul
set "TARGET={install}"
set "NEW={new_dir}"
set "OLD={old_dir}"
:wait
tasklist /FI "IMAGENAME eq {exe_name}" 2>nul | find /I "{exe_name}" >nul && (
  timeout /t 2 /nobreak >nul
  goto wait
)
if exist "%OLD%" rmdir /S /Q "%OLD%"
move "%TARGET%" "%OLD%" || exit /b 1
move "%NEW%" "%TARGET%" || (
  move "%OLD%" "%TARGET%"
  exit /b 1
)
start "" "%TARGET%\\{exe_name}"
""", encoding="utf-8")
    return script


# ---------------------------------------------------------------- 回滚 / 收尾
def mark_boot_ok(install_dir: str | Path) -> Path:
    """新版本启动成功后调用：写标记，下次启动就不会回滚"""
    p = Path(install_dir).resolve() / BOOT_OK
    p.write_text("ok", encoding="utf-8")
    return p


def rollback_if_needed(install_dir: str | Path, exe_name: str = EXE_NAME) -> str:
    """启动早期调用：新版起不来（缺 .boot-ok）但存在 _old → 还原并返回 'rolled-back'"""
    install = Path(install_dir).resolve()
    old_dir = install.parent / (install.name + "_old")
    if not old_dir.is_dir():
        return ""
    if (install / BOOT_OK).exists():
        return ""                                    # 新版已确认能起 → 不动
    log.warning("检测到未确认的更新且存在旧版本，执行回滚：%s → %s", old_dir, install)
    backup = install.parent / (install.name + "_broken")
    shutil.rmtree(backup, ignore_errors=True)
    try:
        install.rename(backup)
        old_dir.rename(install)
    except OSError as e:                              # 回滚失败要把话说明白
        log.error("回滚失败：%s", e)
        return ""
    return "rolled-back"


def cleanup_old(install_dir: str | Path) -> None:
    """确认新版正常后清掉 _old 与 _broken（省磁盘）"""
    install = Path(install_dir).resolve()
    for suffix in ("_old", "_broken"):
        shutil.rmtree(install.parent / (install.name + suffix), ignore_errors=True)


def update_flow(current_version: str, download_dir: str | Path,
                install_dir: str | Path,
                on_progress: Callable[[int, int], None] | None = None) -> dict:
    """检查→下载→校验→暂存 的一站式封装（供接口/面板调用；替换走交接脚本）"""
    info = check(current_version)
    if not info.get("available"):
        return {"ok": True, "available": False, "error": info.get("error", "")}
    dest = Path(download_dir) / str(info.get("asset_name") or "astercore-new.zip")
    digest, size = http_download(str(info["asset_url"]), dest, on_progress=on_progress)
    expect = info.get("sha256")
    if expect and digest.lower() != str(expect).lower():
        dest.unlink(missing_ok=True)
        return {"ok": False, "available": True, "error": f"sha256 不匹配（下载 {digest[:12]}… 期望 {str(expect)[:12]}…）"}
    meta = stage(dest, install_dir)
    script = write_handoff(install_dir)
    return {"ok": True, "available": True, "version": info.get("version"),
            "verified": bool(expect), "sha256": digest, "size": size,
            "staged": meta["staged_dir"], "script": str(script)}
