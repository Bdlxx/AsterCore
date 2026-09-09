# 栖星 AsterCore · OneBot v11 后端（WebSocket 正向）
# 翻译 OneBot 事件 → 统一 Event；统一 Action → OneBot API（WS 下发 + HTTP 兜底/文件）。

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

import websocket  # websocket-client

from ..core.backend import Backend, BackendConfig, register_backend
from ..core.models import (
    ActionResult,
    Event,
    Segment,
    SEG_AT,
    SEG_FILE,
    SEG_FORWARD,
    SEG_IMAGE,
    SEG_RECORD,
    SEG_REPLY,
    SEG_TEXT,
    SEG_VIDEO,
)

log = logging.getLogger("astercore.backend.onebot")

# OneBot 消息段 → 统一 Segment
_SEG_MAP = {
    "text": SEG_TEXT,
    "image": SEG_IMAGE,
    "video": SEG_VIDEO,
    "record": SEG_RECORD,
    "file": SEG_FILE,
    "at": SEG_AT,
    "reply": SEG_REPLY,
    "forward": SEG_FORWARD,
}


@register_backend
class OneBotV11Backend(Backend):
    """正向 WebSocket OneBot v11 后端（NapCat / Lagrange.OneBot / go-cqhttp 兼容）。"""

    name = "onebot"

    def __init__(self, cfg: BackendConfig, account_id: int | str | None = None) -> None:
        super().__init__(cfg, account_id)
        self._ws: websocket.WebSocketApp | None = None
        self._stop_event = asyncio.Event()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._seq = 0
        self._echo_waiters: dict[str, asyncio.Future] = {}

    # ---------------- 生命周期 ----------------
    async def start(self) -> None:
        self._running = True
        self._loop = asyncio.get_running_loop()
        url = self.cfg.ws_url
        if self.cfg.access_token and "?" not in url:
            import urllib.parse
            sep = "&" if "?" in url else "?"
            url = f"{url}{sep}access_token={urllib.parse.quote(self.cfg.access_token)}"

        headers = {}
        if self.cfg.access_token and "access_token" not in url:
            headers["Authorization"] = f"Bearer {self.cfg.access_token}"

        def _on_open(ws):  # noqa: ANN001
            log.info("OneBot WS 已连接: %s", self.cfg.ws_url)

        def _on_message(ws, message):  # noqa: ANN001
            self._handle_message(str(message))

        def _on_error(ws, error):  # noqa: ANN001
            log.warning("OneBot WS 错误: %s", error)

        def _on_close(ws, code, msg):  # noqa: ANN001
            log.info("OneBot WS 关闭 (%s %s)", code, msg)

        self._ws = websocket.WebSocketApp(
            url,
            header=headers or None,
            on_open=_on_open,
            on_message=_on_message,
            on_error=_on_error,
            on_close=_on_close,
        )

        # 后台线程跑 WS（同步库）；事件经 loop.call_soon_threadsafe 回投
        def _run():
            self._ws.run_forever(
                ping_interval=20, ping_timeout=10,
                reconnect=5,  # websocket-client 自动重连(秒)
            )
            # run_forever 退出（stop）后通知协程
            self._loop.call_soon_threadsafe(self._stop_event.set)

        import threading
        threading.Thread(target=_run, daemon=True, name=f"onebot-ws-{self.account_id}").start()

    async def stop(self) -> None:
        self._running = False
        if self._ws is not None:
            try:
                self._ws.close()
            except Exception:
                pass
        self._stop_event.set()

    async def check(self) -> ActionResult:
        """探测：HTTP 调 get_login_info（有 http_url 时）"""
        if not self.cfg.http_url:
            return ActionResult.fail("未配置 HTTP 地址，无法探测")
        try:
            import requests
            url = f"{self.cfg.http_url}/get_login_info"
            params = {}
            if self.cfg.access_token:
                params["access_token"] = self.cfg.access_token
            r = requests.get(url, params=params, timeout=5)
            j = r.json()
            if j.get("status") == "ok" and j.get("data"):
                return ActionResult.success(j["data"])
            return ActionResult.fail(f"登录态异常: {j.get('message', r.text[:80])}")
        except Exception as e:
            return ActionResult.fail(f"探测失败: {e}")

    # ---------------- 消息处理 ----------------
    def _handle_message(self, message: str) -> None:
        try:
            payload = json.loads(message)
        except json.JSONDecodeError:
            return
        # echo 响应（我们 API 调用的回包）→ 分发给等待者
        if "echo" in payload:
            self._resolve_echo(payload)
            return
        if payload.get("post_type") == "message":
            self._emit(self._translate_message(payload))
        elif payload.get("post_type") in ("notice", "request"):
            self._emit(self._translate_generic(payload))

    def _translate_message(self, p: dict[str, Any]) -> Event:
        mt = p.get("message_type", "group")
        ev = Event(
            type="message",
            account_id=self.account_id or p.get("self_id") or 0,
            platform="onebot",
            time=int(p.get("time") or time.time()),
            user_id=p.get("user_id"),
            group_id=p.get("group_id") if mt == "group" else None,
            self_id=p.get("self_id"),
            message_type=mt,
            raw=p.get("raw_message", ""),
            segments=self._translate_segments(p.get("message") or []),
        )
        return ev

    @staticmethod
    def _translate_segments(ob_segs: list[dict]) -> list[Segment]:
        out: list[Segment] = []
        for s in ob_segs:
            typ = _SEG_MAP.get(s.get("type"), s.get("type"))
            data = dict(s.get("data") or {})
            out.append(Segment(type=typ, data=data))
        return out

    def _translate_generic(self, p: dict[str, Any]) -> Event:
        pt = p.get("post_type")
        st = p.get("notice_type") or p.get("request_type") or ""
        if pt == "notice" and st in ("group_increase", "group_decrease"):
            etype = st
        else:
            etype = "notice" if pt == "notice" else "request"
        return Event(
            type=etype,
            account_id=self.account_id or p.get("self_id") or 0,
            platform="onebot",
            time=int(p.get("time") or time.time()),
            user_id=p.get("user_id"),
            group_id=p.get("group_id"),
            self_id=p.get("self_id"),
            extra={"notice_type": st},
        )

    # ---------------- 动作 ----------------
    async def action(self, action: str, params: dict[str, Any]) -> ActionResult:
        ob = self._to_onebot(action, params)
        if ob is None:
            return ActionResult.fail(f"不支持的动作: {action}")
        return await self._ws_call(ob["action"], ob["params"], wait=ob.get("wait", True))

    def _to_onebot(self, action: str, params: dict[str, Any]) -> dict | None:
        from .onebot_mapping import map_action
        return map_action(action, params)

    async def _ws_call(self, ob_action: str, params: dict[str, Any], wait: bool = True,
                       timeout: float = 10.0) -> ActionResult:
        """经 WS 发 API；echo 等待回包。WS 未连时返回错误。"""
        if self._ws is None:
            return ActionResult.fail("WebSocket 未连接")
        self._seq += 1
        echo = f"ac_{self._seq}"
        fut: asyncio.Future | None = None
        if wait:
            fut = self._loop.create_future()
            self._echo_waiters[echo] = fut
        req = {"action": ob_action, "params": params, "echo": echo}
        try:
            self._ws.send(json.dumps(req, ensure_ascii=False))
        except Exception as e:
            if fut:
                self._echo_waiters.pop(echo, None)
            return ActionResult.fail(f"发送失败: {e}")
        if not wait:
            return ActionResult.success({"echo": echo})
        try:
            resp = await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError:
            self._echo_waiters.pop(echo, None)
            return ActionResult.fail(f"动作超时: {ob_action}")
        if resp.get("status") == "ok":
            return ActionResult.success(resp.get("data"))
        return ActionResult.fail(resp.get("message") or resp.get("wording") or "API 失败")

    def _resolve_echo(self, payload: dict[str, Any]) -> None:
        echo = payload.get("echo")
        fut = self._echo_waiters.pop(echo, None)
        if fut and not fut.done():
            self._loop.call_soon_threadsafe(fut.set_result, payload)


# lagrange（内置协议直登）复用 OneBot v11 协议：协议一致，仅运行方式/托管不同
# （进程托管见 core.process_mgr；此处只注册别名，连接参数同 OneBot）
register_backend(OneBotV11Backend, name="lagrange")
