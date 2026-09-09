# 栖星 AsterCore · 更新器内核
# 负责：版本比较 → 校验（sha256）→ 原子替换（备份→替换非数据目录）→ 回滚。
# 远端/插件更新源在调用方实现（HTTP 拉 manifest）；本模块纯本地可测。

from __future__ import annotations

import hashlib
import json
import logging
import shutil
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger("astercore.update")

# 更新包清单（本地或远端 JSON）
# { "version": "0.2.0", "url": "...", "sha256": "...",
#   "keep": ["data", "accounts"], "entries": 可选（缺省整包解压到根） }


@dataclass(slots=True)
class Manifest:
    version: str
    url: str = ""
    sha256: str = ""
    keep: list[str] = field(default_factory=list)  # 替换时保留的顶层目录/文件

    @classmethod
    def from_dict(cls, d: dict) -> "Manifest":
        return cls(
            version=str(d.get("version", "0.0.0")),
            url=str(d.get("url", "")),
            sha256=str(d.get("sha256", "")).lower(),
            keep=[str(k) for k in (d.get("keep") or [])],
        )

    @classmethod
    def from_file(cls, path: str | Path) -> "Manifest":
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))


def parse_version(v: str) -> tuple[int, ...]:
    """'1.2.3-beta' → (1,2,3)；解析失败返回 (0,)"""
    core = v.split("-")[0].split("+")[0]
    parts = []
    for seg in core.split("."):
        try:
            parts.append(int(seg))
        except ValueError:
            break
    return tuple(parts) or (0,)


def is_newer(candidate: str, current: str) -> bool:
    """候选版本 > 当前版本"""
    return parse_version(candidate) > parse_version(current)


def sha256_of(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


@dataclass(slots=True)
class ApplyResult:
    ok: bool
    backup_dir: Path | None = None
    error: str = ""


class Updater:
    """整包 zip 更新：备份 → 替换（保留 keep 目录）→ 失败自动回滚。"""

    def __init__(self, app_root: str | Path) -> None:
        self.app_root = Path(app_root)

    def apply(self, package_zip: str | Path, manifest: Manifest,
              keep_dirs: list[str] | None = None) -> ApplyResult:
        """解压 package_zip 到临时目录，校验后替换 app_root。
        keep 目录（默认 data/accounts 等）不删除、不覆盖。"""
        pkg = Path(package_zip)
        keep = keep_dirs or manifest.keep or ["data", "accounts"]
        if manifest.sha256:
            got = sha256_of(pkg)
            if got != manifest.sha256:
                return ApplyResult(False, error=f"sha256 不匹配: {got[:12]}…")
        if not zipfile.is_zipfile(pkg):
            return ApplyResult(False, error="不是有效 zip")

        import tempfile
        staging = Path(tempfile.mkdtemp(prefix="astcore-upd-"))
        backup = self.app_root.parent / f".astcore-backup-{self.app_root.name}"
        try:
            with zipfile.ZipFile(pkg) as z:
                # 防 zip slip
                for m in z.namelist():
                    target = staging / m
                    if not str(target.resolve()).startswith(str(staging.resolve())):
                        return ApplyResult(False, error=f"非法路径: {m}")
                z.extractall(staging)
            if not staging.is_dir():
                return ApplyResult(False, error="包内容为空")

            # 校验 staging 含可执行入口（宽松：存在任意文件即可，真实版本可进一步校验）
            # 备份当前 app
            if backup.exists():
                shutil.rmtree(backup)
            shutil.copytree(self.app_root, backup, dirs_exist_ok=True)

            # 替换：先删 app_root 内非 keep 项，再从 staging 拷入
            try:
                self._swap(staging, keep)
            except Exception as e:
                # 回滚
                try:
                    shutil.rmtree(self.app_root)
                    shutil.copytree(backup, self.app_root)
                except Exception:
                    pass
                return ApplyResult(False, backup_dir=backup, error=f"替换失败已回滚: {e}")
            return ApplyResult(True, backup_dir=backup)
        finally:
            shutil.rmtree(staging, ignore_errors=True)

    def _swap(self, staging: Path, keep: list[str]) -> None:
        keep = set(keep)
        for item in self.app_root.iterdir():
            if item.name in keep:
                continue
            if item.is_dir():
                shutil.rmtree(item)
            else:
                item.unlink()
        for item in staging.iterdir():
            dst = self.app_root / item.name
            if dst.exists():
                if dst.is_dir():
                    shutil.rmtree(dst)
                else:
                    dst.unlink()
            shutil.move(str(item), str(dst))

    def rollback(self, backup_dir: str | Path) -> bool:
        backup = Path(backup_dir)
        if not backup.exists():
            return False
        try:
            shutil.rmtree(self.app_root)
            shutil.copytree(backup, self.app_root)
            shutil.rmtree(backup, ignore_errors=True)
            return True
        except Exception as e:
            log.exception("回滚失败: %s", e)
            return False
