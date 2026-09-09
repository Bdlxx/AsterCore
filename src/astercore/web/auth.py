# 栖星 AsterCore · Web 面板鉴权分级
# 模式：none（本机免密，默认） / password（会话登录） / token（Bearer/查询参数）
# 配置持久化于 <data_root>/web_auth.json。

from __future__ import annotations

import hashlib
import json
import secrets
from pathlib import Path
from typing import Any


class AuthConfig:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.data: dict[str, Any] = {
            "mode": "none",          # none | password | token
            "password_hash": "",
            "token": "",
            "secret": secrets.token_hex(16),
        }
        self.load()

    def load(self) -> None:
        if self.path.exists():
            try:
                d = json.loads(self.path.read_text(encoding="utf-8"))
                self.data.update({k: d[k] for k in self.data if k in d})
            except Exception:
                pass

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, ensure_ascii=False, indent=2),
                             encoding="utf-8")

    # ---- 查询 ----
    @property
    def mode(self) -> str:
        return self.data.get("mode", "none")

    @property
    def secret(self) -> str:
        return self.data["secret"]

    def token_ok(self, provided: str | None) -> bool:
        if not provided:
            return False
        tok = self.data.get("token", "")
        return bool(tok) and secrets.compare_digest(provided, tok)

    def password_ok(self, provided: str | None) -> bool:
        if not provided:
            return False
        h = self.data.get("password_hash", "")
        return bool(h) and secrets.compare_digest(self._hash(provided), h)

    # ---- 设置（需管理员会话/免密模式） ----
    def set_mode(self, mode: str) -> None:
        mode = mode if mode in ("none", "password", "token") else "none"
        self.data["mode"] = mode
        self.save()

    def set_password(self, password: str) -> None:
        self.data["password_hash"] = self._hash(password)
        self.save()

    def rotate_token(self) -> str:
        tok = secrets.token_urlsafe(24)
        self.data["token"] = tok
        self.save()
        return tok

    @staticmethod
    def _hash(pw: str) -> str:
        return hashlib.sha256(pw.encode("utf-8")).hexdigest()

    def public(self) -> dict[str, Any]:
        """对外可见状态（不含密钥明文）"""
        return {
            "mode": self.mode,
            "has_password": bool(self.data.get("password_hash")),
            "has_token": bool(self.data.get("token")),
        }


def auth_required(secret: str):
    """供 Flask 使用：session 已登录标记"""
    from functools import wraps

    def deco(fn):
        @wraps(fn)
        def wrapper(*a, **kw):
            from flask import session
            if session.get("ac_auth"):
                return fn(*a, **kw)
            return {"ok": False, "error": "未登录"}, 401
        return wrapper
    return deco
