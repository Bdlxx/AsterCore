# 栖星 AsterCore · 桌面壳启动逻辑（跨平台纯逻辑，窗口层编译后接入）
# 对应计划书 §3 首启向导：运行方式选择（napcat 稳定 / lagrange 内置直登实验 /
# null 演示）、风险确认、向导完成状态持久化于 <data_root>/runtime.json。

from __future__ import annotations

import json
import logging
import os
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

# Web 服务默认值（施工手册 §3.1「默认开关」/ §3.2「默认 host」/ §3.3 端口）
#   默认**关闭**：单机用户占多数，默认关减少攻击面，也少一个防火墙弹窗
#   默认 host = 127.0.0.1：只有本机能连；对外监听必须用户主动改并设密码
WEB_DEFAULTS: dict[str, Any] = {
    "enabled": False,
    "host": "127.0.0.1",
    "port": 18750,
}


# 传输通道配置（《WebView2 支持检测说明》§四）：允许用户覆盖自动检测结果
TRANSPORT_MODES = ("auto", "webview", "http")
DEFAULT_TRANSPORT = "auto"


def validate_transport(mode: str) -> str | None:
    """校验 transport 取值；返回错误说明或 None"""
    if mode not in TRANSPORT_MODES:
        return f"transport 只能是 {'/'.join(TRANSPORT_MODES)}（收到 {mode!r}）"
    return None


def validate_web_cfg(cfg: dict, auth_mode: str = "none") -> str | None:
    """校验 Web 配置；返回错误说明或 None（施工手册 §4.3「强制规则」）。

    这条规则的意义：用户经常忘记开密码就对外监听，一次疏忽就可能账号被盗。
    所以在**启动服务之前**拦住，而不是等出事。
    """
    if not cfg.get("enabled"):
        return None
    host = str(cfg.get("host") or "127.0.0.1").strip()
    if host not in ("127.0.0.1", "localhost", "::1"):
        if auth_mode == "none":
            return "对外监听必须开启密码或 Token（当前为免密模式）"
    try:
        port = int(cfg.get("port"))
    except (TypeError, ValueError):
        return "端口必须是数字"
    if not (1 <= port <= 65535):
        return "端口范围 1-65535"
    if not host:
        return "监听地址不能为空"
    return None


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
        """**原子写入**（施工手册 §5.2）。

        为什么：崩溃/断电时配置损坏会非常痛苦，而成本极低 —— 先写 .tmp，
        再 os.replace 覆盖。os.replace 在同一目录内是原子的，所以要么是旧文件、
        要么是新文件，永远不会是半个 JSON。
        """
        self.data_root.mkdir(parents=True, exist_ok=True)
        d = {
            "backend_mode": self.backend_mode,
            "wizard_completed": self.wizard_completed,
            "wizard_version": self.wizard_version,
            "risk_acknowledged": self.risk_acknowledged,
            "extra": self.data,
        }
        target = self.data_root / STATE_FILE
        tmp = target.with_suffix(target.suffix + ".tmp")
        tmp.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, target)

    # ---- 传输通道（WebView2 检测说明 §四） ----
    def transport_mode(self) -> str:
        """auto / webview / http（缺省或非法值一律按 auto 处理）"""
        got = self.data.get("transport")
        if isinstance(got, dict):
            got = got.get("mode")
        mode = str(got or DEFAULT_TRANSPORT).strip().lower()
        return mode if mode in TRANSPORT_MODES else DEFAULT_TRANSPORT

    def set_transport(self, mode: str) -> tuple[bool, str]:
        mode = str(mode or "").strip().lower()
        err = validate_transport(mode)
        if err:
            return False, err
        self.data["transport"] = {"mode": mode}
        self.save()
        log.info("传输通道已设为 %s", mode)
        return True, ""

    # ---- Web 服务开关（手册 §3.1/§3.2/§4.3） ----
    def web_config(self) -> dict[str, Any]:
        """Web 服务配置（含默认值，缺项自动补齐）"""
        cfg = dict(WEB_DEFAULTS)
        got = self.data.get("web")
        if isinstance(got, dict):
            cfg.update({k: v for k, v in got.items() if v is not None})
        return cfg

    def set_web_config(self, patch: dict, auth_mode: str = "none") -> tuple[bool, str, dict]:
        """改 Web 配置；**先校验再落盘**（校验不过不改、不启动）。返回 (ok, err, cfg)"""
        cfg = self.web_config()
        for k in ("enabled", "host", "port"):
            if k in patch and patch[k] is not None:
                cfg[k] = patch[k]
        cfg["enabled"] = bool(cfg.get("enabled"))
        try:
            cfg["port"] = int(cfg["port"])
        except (TypeError, ValueError):
            return False, "端口必须是数字", cfg
        err = validate_web_cfg(cfg, auth_mode)
        if err:
            return False, err, cfg
        self.data["web"] = cfg
        self.save()
        log.info("Web 服务配置已更新: %s", cfg)
        return True, "", cfg

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
