# 栖星 AsterCore · Web 服务（Flask，可选依赖）
# 设计（计划书 §4.1）：单端口 + 路径前缀分层；HTTP 为可选模块，本地默认走进程内 bridge。
# v0.1：提供 JSON API（账号状态/插件启停/实时日志）+ 一个最小面板页。

from __future__ import annotations

import asyncio
import logging
import threading
from pathlib import Path

from astercore.core.manager import AccountConfig
from typing import Any, Awaitable, Callable

log = logging.getLogger("astercore.web")

try:
    from flask import Flask, jsonify, request, send_from_directory
except ImportError:  # pragma: no cover
    Flask = None


def run_coro_sync(loop: asyncio.AbstractEventLoop,
                  coro: Awaitable[Any], timeout: float = 10.0) -> Any:
    """把协程投到 runtime 的事件循环并同步等待（供 Flask 线程调用）"""
    fut = asyncio.run_coroutine_threadsafe(coro, loop)
    return fut.result(timeout=timeout)


def _ok(data: Any = None):
    return jsonify({"ok": True, "data": data})


def _err(msg: str, code: int = 400):
    return jsonify({"ok": False, "error": msg}), code


class WebPanel:
    """包装单个账号 runtime 的 HTTP 面板。

    provider: 提供 (runtime, loop) 的可调用对象；多账号后改为 account_id 参数化。
    """

    def __init__(self, provider: Callable[[], tuple[Any, asyncio.AbstractEventLoop] | None],
                 static_dir: str | Path | None = None) -> None:
        if Flask is None:
            raise RuntimeError("需要 Flask：pip install astercore[web]")
        self.provider = provider
        self.static_dir = Path(static_dir) if static_dir else Path(__file__).parent / "static"
        self.app = Flask(
            "astercore-web",
            static_folder=str(self.static_dir),
            static_url_path="/assets",
        )
        self._routes()

    # ---------- 路由 ----------
    def _routes(self) -> None:
        app = self.app

        @app.get("/")
        def index():
            return send_from_directory(self.static_dir, "index.html")

        @app.get("/api/status")
        def api_status():
            rt = self._rt()
            if rt is None:
                return _err("无运行中的账号", 503)
            return _ok({
                "account_id": str(rt.account_id),
                "backend": rt.backend.name,
                "backend_running": rt.backend.running,
                "plugin_count": len(rt.list_plugins()),
                "version": "0.1.0",
            })

        @app.get("/api/plugins")
        def api_plugins():
            rt = self._rt()
            if rt is None:
                return _err("无运行中的账号", 503)
            return _ok(rt.list_plugins())

        @app.post("/api/plugins/<name>/enable")
        def api_enable(name: str):
            rt = self._rt()
            if rt is None:
                return _err("无运行中的账号", 503)
            ok = run_coro_sync(self._loop(), rt.enable_plugin(name))
            return _ok({"enabled": ok}) if ok else _err("插件不存在", 404)

        @app.post("/api/plugins/<name>/disable")
        def api_disable(name: str):
            rt = self._rt()
            if rt is None:
                return _err("无运行中的账号", 503)
            ok = run_coro_sync(self._loop(), rt.disable_plugin(name))
            return _ok({"disabled": ok}) if ok else _err("插件不存在", 404)

        @app.post("/api/plugins/reload")
        def api_reload():
            rt = self._rt()
            if rt is None:
                return _err("无运行中的账号", 503)
            n = run_coro_sync(self._loop(), rt.reload_plugins())
            return _ok({"plugin_count": n})

        @app.get("/api/logs")
        def api_logs():
            rt = self._rt()
            if rt is None:
                return _err("无运行中的账号", 503)
            try:
                after = float(request.args.get("after", 0))
            except (TypeError, ValueError):
                after = 0.0
            limit = min(int(request.args.get("limit", 200)), 2000)
            if after > 0:
                items = rt.recent_logs(limit)
                items = [e for e in items if e["ts"] > after]
            else:
                items = rt.recent_logs(limit)
            return _ok({"logs": items})

    # ---------- 工具 ----------
    def _rt(self):
        try:
            pair = self.provider()
            return pair[0] if pair else None
        except Exception:
            return None

    def _loop(self):
        try:
            pair = self.provider()
            return pair[1] if pair else None
        except Exception:
            return None

    # ---------- 运行 ----------
    def serve(self, host: str = "127.0.0.1", port: int = 8080, debug: bool = False) -> None:
        """阻塞运行（线程模式由调用方决定）"""
        self.app.run(host=host, port=port, debug=debug, threaded=True,
                     use_reloader=False)


def serve_in_background(panel: WebPanel, host: str = "127.0.0.1",
                        port: int = 8080) -> threading.Thread:
    """后台线程启动 HTTP 服务（主程序保持 asyncio 循环）"""
    t = threading.Thread(target=panel.serve, args=(host, port),
                         daemon=True, name="astercore-web")
    t.start()
    return t


class ManagerWebPanel:
    """多账号面板：管理 AccountManager 中的所有账号。

    manager: AccountManager
    loop_provider: () -> 主事件循环（manager.start 等协程在此 loop 执行）
    """

    def __init__(self, manager, loop_provider, static_dir=None) -> None:
        if Flask is None:
            raise RuntimeError("需要 Flask：pip install astercore[web]")
        self.manager = manager
        self._loop_provider = loop_provider
        self.static_dir = Path(static_dir) if static_dir else Path(__file__).parent / "static"
        self.app = Flask("astercore-web", static_folder=str(self.static_dir),
                         static_url_path="/assets")
        self._routes()

    def _loop(self):
        try:
            return self._loop_provider()
        except Exception:
            return None

    def _routes(self) -> None:
        app = self.app
        mgr = self.manager

        @app.get("/")
        def index():
            return send_from_directory(self.static_dir, "index.html")

        # ---------- 账号 ----------
        @app.get("/api/accounts")
        def api_accounts():
            return _ok(mgr.scan())

        @app.post("/api/accounts")
        def api_create_account():
            d = request.get_json(force=True, silent=True) or {}
            account_id = str(d.get("account_id") or "").strip()
            if not account_id:
                return _err("缺少 account_id", 400)
            b = d.get("backend") or {}
            cfg = AccountConfig(
                account_id=account_id,
                display_name=str(d.get("display_name") or account_id),
                backend_name=str(b.get("name") or "onebot"),
                ws_url=str(b.get("ws_url") or "ws://127.0.0.1:3001"),
                http_url=str(b.get("http_url") or "http://127.0.0.1:3000"),
                access_token=str(b.get("access_token") or ""),
            )
            mgr.save_config(cfg)
            return _ok({"account_id": account_id})

        @app.post("/api/accounts/<aid>/start")
        def api_start(aid: str):
            try:
                rt = run_coro_sync(self._loop(), mgr.start(aid))
            except KeyError:
                return _err("账号未配置", 404)
            except Exception as e:
                return _err(f"启动失败: {e}", 500)
            return _ok({"running": rt.backend.running})

        @app.post("/api/accounts/<aid>/stop")
        def api_stop(aid: str):
            run_coro_sync(self._loop(), mgr.stop(aid))
            return _ok({"stopped": True})

        @app.post("/api/accounts/<aid>/delete")
        def api_delete(aid: str):
            mgr.remove(aid)
            return _ok({"deleted": True})

        # ---------- 账号内：插件 / 日志 ----------
        def _rt_of(aid: str):
            rt = mgr.runtimes.get(aid)
            return rt

        @app.get("/api/accounts/<aid>/status")
        def api_account_status(aid: str):
            rt = _rt_of(aid)
            if rt is None:
                return _ok({"running": False})
            return _ok({"running": True, "backend": rt.backend.name,
                        "plugin_count": len(rt.list_plugins()),
                        "account_id": str(rt.account_id)})

        @app.get("/api/accounts/<aid>/plugins")
        def api_account_plugins(aid: str):
            rt = _rt_of(aid)
            if rt is None:
                return _err("账号未运行", 404)
            items = rt.list_plugins()
            # 附加原生宿主崩溃计数
            for it in items:
                lp = rt.plugin_loader.loaded.get(it["name"])
                if lp is not None and getattr(lp, "native_host", None) is not None:
                    it["crashes"] = getattr(lp.native_host, "crash_count", 0)
            return _ok(items)

        @app.post("/api/accounts/<aid>/plugins/<name>/enable")
        def api_acct_plugin_enable(aid: str, name: str):
            rt = _rt_of(aid)
            if rt is None:
                return _err("账号未运行", 404)
            ok = run_coro_sync(self._loop(), rt.enable_plugin(name))
            return _ok({"enabled": ok}) if ok else _err("插件不存在", 404)

        @app.post("/api/accounts/<aid>/plugins/<name>/disable")
        def api_acct_plugin_disable(aid: str, name: str):
            rt = _rt_of(aid)
            if rt is None:
                return _err("账号未运行", 404)
            ok = run_coro_sync(self._loop(), rt.disable_plugin(name))
            return _ok({"disabled": ok}) if ok else _err("插件不存在", 404)

        @app.get("/api/accounts/<aid>/plugins/<name>/config")
        def api_acct_plugin_config_get(aid: str, name: str):
            rt = _rt_of(aid)
            if rt is None:
                return _err("账号未运行", 404)
            return _ok(rt.get_plugin_config(name))

        @app.post("/api/accounts/<aid>/plugins/<name>/config")
        def api_acct_plugin_config_save(aid: str, name: str):
            rt = _rt_of(aid)
            if rt is None:
                return _err("账号未运行", 404)
            data = request.get_json(force=True, silent=True) or {}
            ok = run_coro_sync(self._loop(), rt.save_plugin_config(name, data))
            return _ok({"saved": ok}) if ok else _err("保存失败", 400)

        @app.get("/api/accounts/<aid>/logs")
        def api_acct_logs(aid: str):
            rt = _rt_of(aid)
            if rt is None:
                return _err("账号未运行", 404)
            try:
                after = float(request.args.get("after", 0))
            except (TypeError, ValueError):
                after = 0.0
            limit = min(int(request.args.get("limit", 200)), 2000)
            items = rt.recent_logs(limit)
            if after > 0:
                items = [e for e in items if e["ts"] > after]
            return _ok({"logs": items})

    def serve(self, host: str = "127.0.0.1", port: int = 8080) -> None:
        self.app.run(host=host, port=port, threaded=True, use_reloader=False)
