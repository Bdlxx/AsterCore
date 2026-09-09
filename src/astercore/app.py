# 栖星 AsterCore · 桌面壳启动逻辑（跨平台纯逻辑，窗口层编译后接入）
# 对应计划书 §3 首启向导：运行方式选择（napcat 稳定 / lagrange 内置直登实验 /
# null 演示）、风险确认、向导完成状态持久化于 <data_root>/runtime.json。

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger("astercore.app")

# 后端模式说明（供向导展示；文案留给前端，这里只给机器可读元数据）
BACKEND_MODES: dict[str, dict[str, Any]] = {
    "napcat": {
        "label": "NapCat 稳定模式",
        "risk": 5,          # 安全评级 1-5
        "desc": "后台运行官方 QQ 客户端 + NapCat 中间层，走官方登录通道，风控风险最低。",
        "needs_ack": False,
    },
    "lagrange": {
        "label": "内置协议直登（实验）",
        "risk": 2,
        "desc": "纯协议实现，免装客户端，扫码即用；但属非官方协议，存在限制登录/封号风险，建议小号。",
        "needs_ack": True,
    },
    "null": {
        "label": "演示模式（不连协议）",
        "risk": 5,
        "desc": "不连接任何 QQ 后端，用于开发/演示内核与插件。",
        "needs_ack": False,
    },
}

STATE_FILE = "runtime.json"
WIZARD_VERSION = 1  # 向导结构版本（升级需重走向导时递增）


@dataclass(slots=True)
class AppState:
    """启动状态（持久化 runtime.json）。"""

    data_root: Path
    backend_mode: str = "napcat"
    wizard_completed: bool = False
    wizard_version: int = 0
    risk_acknowledged: bool = False
    data: dict[str, Any] = field(default_factory=dict)

    # ---- 持久化 ----
    def load(self) -> None:
        p = self.data_root / STATE_FILE
        if p.exists():
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
                self.backend_mode = d.get("backend_mode", "napcat")
                self.wizard_completed = bool(d.get("wizard_completed"))
                self.wizard_version = int(d.get("wizard_version", 0))
                self.risk_acknowledged = bool(d.get("risk_acknowledged"))
                self.data = d.get("extra", {})
            except Exception:
                log.warning("runtime.json 解析失败，按默认启动")

    def save(self) -> None:
        self.data_root.mkdir(parents=True, exist_ok=True)
        d = {
            "backend_mode": self.backend_mode,
            "wizard_completed": self.wizard_completed,
            "wizard_version": self.wizard_version,
            "risk_acknowledged": self.risk_acknowledged,
            "extra": self.data,
        }
        (self.data_root / STATE_FILE).write_text(
            json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")

    # ---- 查询 ----
    def needs_wizard(self) -> bool:
        """是否需走首启向导（未完成或向导版本升级）"""
        return not self.wizard_completed or self.wizard_version < WIZARD_VERSION

    def startup_plan(self) -> dict[str, Any]:
        """桌面壳启动决策：wizard=True 时先走向导；否则按 backend_mode 直启"""
        if self.needs_wizard():
            return {"wizard": True, "reason": "首次运行或向导升级",
                    "backend_mode": self.backend_mode}
        mode = BACKEND_MODES.get(self.backend_mode)
        return {"wizard": False, "backend_mode": self.backend_mode,
                "risk": mode["risk"] if mode else None,
                "reason": "直接启动"}

    # ---- 变更（向导结果） ----
    def set_backend(self, mode: str, ack_risk: bool = False) -> tuple[bool, str]:
        """选择运行方式；needs_ack 模式必须 ack 风险。返回 (ok, 错误/说明)"""
        if mode not in BACKEND_MODES:
            return False, f"未知运行方式: {mode}"
        meta = BACKEND_MODES[mode]
        if meta["needs_ack"] and not ack_risk:
            return False, "该模式为实验性质，需确认已知晓账号风险（小号建议）"
        self.backend_mode = mode
        self.risk_acknowledged = ack_risk or self.risk_acknowledged
        self.wizard_completed = True
        self.wizard_version = WIZARD_VERSION
        self.save()
        log.info("运行方式已设为 %s（风险确认=%s）", mode, ack_risk)
        return True, ""

    def reset_wizard(self) -> None:
        """面板里重走向导"""
        self.wizard_completed = False
        self.save()
