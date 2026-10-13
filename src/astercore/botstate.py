# 栖星 AsterCore · 程序机器人状态机 + 启停/重启控制器
#
# 托盘菜单与主界面按钮**共用这一个实例**，所以两边状态天然同步（规格 §四：实时同步）。
# 注意语义（用户明确过）：这里的"机器人状态"指**本程序内部机器人核心**的状态，
# 与 NapCat 是否在线无关。NapCat 掉线只体现在后端 connected 上，不影响这里的
# 已停止/启动中/运行中/停止中/错误。
#
# 状态迁移：
#   stopped --start--> starting --成功--> running        （失败 → error）
#   running --stop---> stopping --成功--> stopped        （失败 → error）
#   running --restart-> stopping -> starting -> running  （任一失败 → error）

from __future__ import annotations

import logging
import threading
from typing import Any, Callable

log = logging.getLogger("astercore.botstate")

STOPPED = "stopped"
STARTING = "starting"
RUNNING = "running"
STOPPING = "stopping"
ERROR = "error"

STATE_LABELS = {
    STOPPED: "已停止",
    STARTING: "启动中",
    RUNNING: "运行中",
    STOPPING: "停止中",
    ERROR: "错误",
}

# 启停按钮文案（规格：按状态自动变化）
BUTTON_LABELS = {
    STOPPED: "启动机器人",
    STARTING: "启动中…",
    RUNNING: "停止机器人",
    STOPPING: "停止中…",
    ERROR: "启动机器人",       # 错误态点击 = 尝试重新启动
}


class BotController:
    """把 AccountManager 的启停包装成"一个机器人"的状态机。

    manager: AccountManager
    loop_provider: () -> 主事件循环（可为 None：测试里直接 asyncio.run）
    state_get / state_set: 读写"当前账号"（持久化在 runtime.json）
    """

    def __init__(self, manager: Any, loop_provider: Callable[[], Any] | None = None,
                 state_get: Callable[[], str] | None = None,
                 state_set: Callable[[str], None] | None = None,
                 on_change: Callable[[], None] | None = None) -> None:
        self.manager = manager
        self._loop_provider = loop_provider
        self._state_get = state_get or (lambda: "")
        self._state_set = state_set or (lambda _aid: None)
        self.on_change = on_change
        self._transient: str | None = None      # starting / stopping
        self._error = ""
        self._lock = threading.Lock()

    # ---------------- 当前账号 ----------------
    def current_account(self) -> str:
        aid = str(self._state_get() or "")
        known = {str(r.get("account_id")) for r in self._rows()}
        if aid and aid in known:
            return aid
        return sorted(known)[0] if known else ""

    def set_current_account(self, aid: str) -> None:
        self._state_set(str(aid))
        self._notify()

    def _rows(self) -> list[dict[str, Any]]:
        try:
            return list(self.manager.scan()) if self.manager is not None else []
        except Exception:                        # noqa: BLE001 面板/托盘不能被扫崩
            log.exception("账号列表读取失败")
            return []

    # ---------------- 状态 ----------------
    def state(self) -> str:
        with self._lock:
            transient = self._transient
        if transient:
            return transient
        rows = self._rows()
        if not rows:
            return ERROR if self._error else STOPPED
        if any(r.get("running") for r in rows):
            return RUNNING
        return ERROR if self._error else STOPPED

    def snapshot(self) -> dict[str, Any]:
        """托盘与主界面都读它 —— 文案/禁用态只在服务端算一次，避免两边不一致"""
        st = self.state()
        rows = self._rows()
        aid = self.current_account()
        cur = next((r for r in rows if str(r.get("account_id")) == aid), None)
        nickname = (cur or {}).get("display_name") or aid or "（未配置账号）"
        return {
            "state": st,
            "state_label": STATE_LABELS.get(st, st),
            "button": BUTTON_LABELS.get(st, "启动机器人"),
            "button_enabled": st not in (STARTING, STOPPING),
            "restart_enabled": st == RUNNING,
            "account_id": aid,
            "nickname": str(nickname),
            "error": self._error,
            "accounts": [{
                "account_id": str(r.get("account_id")),
                "display_name": r.get("display_name") or str(r.get("account_id")),
                "running": bool(r.get("running")),
                "current": str(r.get("account_id")) == aid,
            } for r in rows],
        }

    # ---------------- 动作 ----------------
    def start(self) -> tuple[bool, str]:
        if self.state() in (STARTING, STOPPING):
            return False, "正在处理上一个操作"
        aid = self.current_account()
        if not aid:
            self._fail("还没有账号：请先在『连接引导』里建一个")
            return False, self._error
        if self.state() == RUNNING:
            return True, "已在运行"
        return self._do(STARTING, lambda: self.manager.start(aid), f"机器人已启动（{aid}）")

    def stop(self) -> tuple[bool, str]:
        if self.state() in (STARTING, STOPPING):
            return False, "正在处理上一个操作"
        aid = self.current_account()
        if not aid:
            return True, "没有账号"
        return self._do(STOPPING, lambda: self.manager.stop(aid), f"机器人已停止（{aid}）")

    def restart(self) -> tuple[bool, str]:
        """先停后起；由后台线程串起来，期间状态依次是 停止中 → 启动中 → 运行中"""
        if self.state() != RUNNING:
            return False, "只有运行中才能重启"

        def _job():
            with self._lock:
                self._transient = STOPPING
            self._notify()
            aid = self.current_account()
            ok_stop = self._await(self.manager.stop(aid))
            with self._lock:
                self._transient = STARTING
            self._notify()
            ok_start = self._await(self.manager.start(aid))
            with self._lock:
                self._transient = None
                if ok_stop and ok_start:
                    self._error = ""
                elif not self._error:
                    self._error = "重启失败，请看日志"
            self._notify()

        threading.Thread(target=_job, daemon=True, name="bot-restart").start()
        return True, "正在重启…"

    # ---------------- 内部 ----------------
    def _do(self, transient: str, call: Callable[[], Any], ok_msg: str) -> tuple[bool, str]:
        def _job():
            with self._lock:
                self._transient = transient
            self._notify()
            ok = self._await(call())
            with self._lock:
                self._transient = None
                if ok:
                    self._error = ""
                elif not self._error:
                    self._error = "操作失败，请看日志"
            self._notify()

        # 短操作也放线程：托盘回调不能阻塞（否则菜单卡住）
        threading.Thread(target=_job, daemon=True, name=f"bot-{transient}").start()
        return True, ok_msg

    def _await(self, coro: Any) -> bool:
        import asyncio
        try:
            loop = self._loop_provider() if self._loop_provider else None
        except Exception:                        # noqa: BLE001
            loop = None
        try:
            if loop is not None and getattr(loop, "is_running", lambda: False)():
                asyncio.run_coroutine_threadsafe(coro, loop).result(timeout=120)
            else:
                asyncio.run(coro)
            return True
        except Exception as e:                   # noqa: BLE001 起停失败要落到 error 态
            log.exception("机器人操作失败")
            with self._lock:
                self._error = f"{type(e).__name__}: {e}"
            return False

    def _fail(self, msg: str) -> None:
        with self._lock:
            self._error = msg
        self._notify()

    def _notify(self) -> None:
        cb = self.on_change
        if cb:
            try:
                cb()
            except Exception:                    # noqa: BLE001 通知失败不影响状态机
                log.debug("on_change 回调异常", exc_info=True)
