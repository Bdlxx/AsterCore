# 栖星 AsterCore · 进程内 RPC 中枢
#
# 施工手册 §1.4「RPC 中枢：单一 dispatch」+ §1.2「js_api 进程内 RPC」+ §4.1「js_api 不鉴权」。
#
# 为什么要有这一层：
#   原来的"桌面壳"是把 HTTP 面板塞进一个 pywebview 窗口 —— 窗口加载
#   http://127.0.0.1:8080，Flask 永远在后台跑。于是桌面版必须占用一个端口，
#   而且"有没有登录态"变成浏览器那套东西。手册要求：**默认走进程内 RPC，
#   HTTP 只是可选项**。
#
# 做法（关键取舍）：**不重写 585 行的面板路由**，而是让进程内调用也走同一条路由：
#   js_api → RpcHub.dispatch → Flask test_client → 同一个 view 函数 → 同样的 JSON
#   好处：
#     · 两条通道天然返回一致（手册 §1.4 的验收就是这条），不会逻辑漂移
#     · 零端口、零网络、零跨域、零鉴权（进程内调用带可信标记，鉴权闸门直接放行）
#     · 加新功能仍然只写一个 Flask 路由，两条路自动都能用
#   test_client 不经过 socket，单次开销 ~0.1ms，比 HTTP 还快。

from __future__ import annotations

import json
import logging
import threading
from typing import Any, Callable

log = logging.getLogger("astercore.rpc")

# 进程内可信调用标记。**只在本进程内用 threading.local 打标**，不走任何 header ——
# 外部请求不可能伪造（没有可猜的 token），所以 HTTP 暴露时也不构成绕过面。
_trusted = threading.local()


def mark_trusted() -> None:
    _trusted.active = True


def clear_trusted() -> None:
    _trusted.active = False


def is_trusted() -> bool:
    return bool(getattr(_trusted, "active", False))


class RpcError(Exception):
    """参数/方法类错误（会原样回给前端，不含栈）"""


class RpcHub:
    """统一 RPC 中枢：注册语义方法 + 转发面板 HTTP 路径。

    方法名统一用点号（`web.test_port`）；注册表里存点号形式，
    内部转成下划线去查 `_m_<name>`，**只有一条替换规则**（手册 §1.4 的坑）。
    """

    def __init__(self) -> None:
        self._methods: dict[str, Callable[[dict], Any]] = {}
        self._inline: "InlinePanelClient | None" = None
        self._lock = threading.RLock()

    # ---------- 注册 ----------
    def register(self, name: str, fn: Callable[[dict], Any]) -> None:
        if not name or name.startswith("_"):
            raise ValueError(f"非法方法名: {name!r}（不能以下划线开头）")
        with self._lock:
            self._methods[name] = fn

    def bind_panel(self, panel) -> None:
        """绑定面板：之后 `dispatch('/api/...')` 会走进程内 test_client"""
        with self._lock:
            self._inline = InlinePanelClient(panel)

    def methods(self) -> list[str]:
        with self._lock:
            return sorted(self._methods)

    # ---------- 调用 ----------
    def dispatch(self, method: str, params: dict | None = None) -> dict:
        """统一入口。**永不抛异常**，一切错误都变成 {'ok': False, 'error': ...}，
        因为 js_api 的异常在前端只会变成一个没有上下文的 rejected promise。"""
        params = dict(params or {})
        if not isinstance(method, str) or not method:
            return {"ok": False, "error": "缺少 method"}
        with self._lock:
            fn = self._methods.get(method)
            inline = self._inline
        try:
            if fn is not None:
                data = fn(params)
                if isinstance(data, dict) and "ok" in data:
                    return data
                return {"ok": True, "data": data}
            if method.startswith("/") and inline is not None:
                # 面板 HTTP 路径：走同一条路由（进程内，无端口无网络）
                return inline.call(method,
                                   method_=params.get("method", "GET"),
                                   body=params.get("body"),
                                   query=params.get("query"))
            return {"ok": False, "error": f"未知方法: {method}"}
        except RpcError as e:
            return {"ok": False, "error": str(e)}
        except Exception as e:                      # noqa: BLE001 — 中枢必须兜住一切
            log.warning("RPC %s 执行失败: %s", method, e)
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}


class InlinePanelClient:
    """通过 Flask test_client 在进程内调用面板 API。

    与 HTTP 的唯一差别：调用期间打上"可信"标记，面板鉴权闸门会放行
    （手册 §4.1：进程内调用不需要密码；密码只保护对外暴露的 HTTP）。
    """

    def __init__(self, panel) -> None:
        self.panel = panel
        self._client = panel.app.test_client()
        self._lock = threading.Lock()

    def call(self, path: str, method_: str = "GET", body: Any = None,
             query: dict | None = None) -> dict:
        method_ = (method_ or "GET").upper()
        qs = ""
        if query:
            from urllib.parse import urlencode
            qs = "?" + urlencode(query)
        mark_trusted()
        try:
            with self._lock:      # test_client 不保证跨线程安全
                kw: dict[str, Any] = {}
                if body is not None:
                    kw["json"] = body
                r = self._client.open(path + qs, method=method_, **kw)
                raw = r.get_data(as_text=True)
                status = r.status_code
        finally:
            clear_trusted()
        try:
            payload = json.loads(raw) if raw else {}
        except (ValueError, TypeError):
            payload = {"ok": False, "error": "非 JSON 响应", "raw": raw[:500]}
        if not isinstance(payload, dict):
            payload = {"ok": True, "data": payload}
        # 保留 HTTP 语义：状态码非 2xx 且响应里没写 ok=false 时补上
        if status >= 400 and payload.get("ok") is not False:
            payload = {"ok": False, "error": payload.get("error") or f"HTTP {status}",
                       "data": payload.get("data")}
        payload.setdefault("status", status)
        return payload


def build_hub() -> RpcHub:
    """装配默认方法集（桌面壳自己的方法，与面板业务无关）"""
    hub = RpcHub()

    def web_test_port(p: dict) -> dict:
        """探测端口是否可用（手册 §3.3：面板上能测可用性体验最好）"""
        import socket
        host = str(p.get("host") or "127.0.0.1")
        try:
            port = int(p.get("port"))
        except (TypeError, ValueError):
            raise RpcError("端口必须是数字")
        if not (1 <= port <= 65535):
            raise RpcError("端口范围 1-65535")
        s = socket.socket()
        try:
            # 注意：Windows 上 SO_REUSEADDR 语义与 Linux 不同，会掩盖"正在被监听"，
            # 所以这里**不设** SO_REUSEADDR，才能测出真实占用（手册 §3.3 的坑）
            s.bind((host, port))
            return {"available": True}
        except OSError as e:
            return {"available": False, "reason": str(e)}
        finally:
            s.close()

    hub.register("web.test_port", web_test_port)
    return hub
