# 栖星 AsterCore · 账号管理器（多账号）
# 每个账号一个目录 accounts/<id>/，连接信息存 backend.json（计划书 §4.1）。
# 单事件循环可同时运行多个 AccountRuntime（各 backend 的 WS 线程回调统一投主 loop）。

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .backend import BackendConfig, get_registry
from .runtime import AccountRuntime

log = logging.getLogger("astercore.manager")

BACKEND_FILE = "backend.json"


@dataclass(slots=True)
class AccountConfig:
    """账号配置（持久化到 accounts/<id>/backend.json）"""

    account_id: str
    display_name: str = ""
    backend_name: str = "onebot"
    ws_url: str = "ws://127.0.0.1:3001"
    http_url: str = "http://127.0.0.1:3000"
    access_token: str = ""
    enabled: bool = True

    @classmethod
    def from_dict(cls, account_id: str, d: dict[str, Any]) -> "AccountConfig":
        return cls(
            account_id=str(account_id),
            display_name=d.get("display_name", str(account_id)),
            backend_name=d.get("backend", {}).get("name", "onebot"),
            ws_url=d.get("backend", {}).get("ws_url", "ws://127.0.0.1:3001"),
            http_url=d.get("backend", {}).get("http_url", "http://127.0.0.1:3000"),
            access_token=d.get("backend", {}).get("access_token", ""),
            enabled=bool(d.get("enabled", True)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "display_name": self.display_name,
            "enabled": self.enabled,
            "backend": {
                "name": self.backend_name,
                "ws_url": self.ws_url,
                "http_url": self.http_url,
                "access_token": self.access_token,
            },
        }


class AccountManager:
    """账号生命周期管理（创建/启停/状态）。"""

    def __init__(self, accounts_dir: str | Path, plugin_dir: str | Path | None = None,
                 data_root: str | Path | None = None) -> None:
        self.accounts_dir = Path(accounts_dir)
        self.accounts_dir.mkdir(parents=True, exist_ok=True)
        # 账号数据目录：默认 accounts/<id>/data；也可统一放 data/<id>（由调用方传 data_root）
        self.data_root = Path(data_root) if data_root else self.accounts_dir
        self.plugin_dir = Path(plugin_dir) if plugin_dir else None
        self.runtimes: dict[str, AccountRuntime] = {}

    # ---------- 查询 ----------
    def scan(self) -> list[dict[str, Any]]:
        """列出账号（含配置与运行状态）"""
        out: list[dict[str, Any]] = []
        for d in sorted(self.accounts_dir.iterdir()):
            if not d.is_dir():
                continue
            cfg = self._load_config(d.name)
            if cfg is None:
                continue
            rt = self.runtimes.get(d.name)
            out.append({
                "account_id": d.name,
                "display_name": cfg.display_name,
                "backend_name": cfg.backend_name,
                "running": rt is not None and rt.backend.running,
            })
        return out

    def get_config(self, account_id: str) -> AccountConfig | None:
        return self._load_config(account_id)

    # ---------- 创建/保存 ----------
    def save_config(self, cfg: AccountConfig) -> Path:
        d = self.accounts_dir / cfg.account_id
        d.mkdir(parents=True, exist_ok=True)
        p = d / BACKEND_FILE
        p.write_text(json.dumps(cfg.to_dict(), ensure_ascii=False, indent=2),
                     encoding="utf-8")
        return p

    async def remove(self, account_id: str) -> None:
        """停掉并从目录移除"""
        await self.stop(account_id)
        import shutil
        shutil.rmtree(self.accounts_dir / account_id, ignore_errors=True)

    # ---------- 生命周期 ----------
    async def start(self, account_id: str) -> AccountRuntime:
        """启动账号（未配置则 KeyError）"""
        if account_id in self.runtimes:
            return self.runtimes[account_id]
        cfg = self._load_config(account_id)
        if cfg is None:
            raise KeyError(f"账号未配置: {account_id}")
        backend = get_registry().create(
            cfg.backend_name,
            BackendConfig(ws_url=cfg.ws_url, http_url=cfg.http_url,
                          access_token=cfg.access_token),
            account_id=account_id,
        )
        rt = AccountRuntime(
            account_id=account_id,
            data_dir=self.data_root / str(account_id),
            backend=backend,
            # 插件目录：显式指定优先；否则默认 data/<id>/plugins（与 runtime 一致）
            plugin_dir=self.plugin_dir,
        )
        await rt.start()
        self.runtimes[account_id] = rt
        return rt

    async def stop(self, account_id: str) -> None:
        rt = self.runtimes.pop(account_id, None)
        if rt is not None:
            await rt.stop()

    async def stop_all(self) -> None:
        for aid in list(self.runtimes):
            await self.stop(aid)

    async def restart(self, account_id: str) -> AccountRuntime:
        await self.stop(account_id)
        return await self.start(account_id)

    # ---------- 工具 ----------
    def _load_config(self, account_id: str) -> AccountConfig | None:
        p = self.accounts_dir / account_id / BACKEND_FILE
        if not p.exists():
            return None
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            log.warning("账号配置解析失败: %s", p)
            return None
        return AccountConfig.from_dict(account_id, d)
