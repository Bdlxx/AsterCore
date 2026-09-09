# 栖星 AsterCore · Web 服务（Flask，可选依赖）
# 设计（计划书 §4.1）：单端口 + 路径前缀分层；HTTP 为可选模块，本地默认走进程内 bridge。
# v0.1：提供 JSON API（账号状态/插件启停/实时日志）+ 一个最小面板页。

from __future__ import annotations

import asyncio
import logging
import threading
from pathlib import Path

from astercore.core.manager import AccountConfig
from astercore.web.auth import AuthConfig
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

    def __init__(self, manager, loop_provider, static_dir=None,
                 auth_file: str | Path | None = None) -> None:
        if Flask is None:
            raise RuntimeError("需要 Flask：pip install astercore[web]")
        self.manager = manager
        self._loop_provider = loop_provider
        self.static_dir = Path(static_dir) if static_dir else Path(__file__).parent / "static"
        self.app = Flask("astercore-web", static_folder=str(self.static_dir),
                         static_url_path="/assets")
        # 鉴权（分级：none/password/token），文件默认 <accounts>/../web_auth.json
        auth_path = auth_file or (Path(manager.accounts_dir).parent / "web_auth.json")
        self.auth = AuthConfig(auth_path)
        self.app.secret_key = self.auth.secret
        self._routes()
        self._setup_auth_gate()

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
            run_coro_sync(self._loop(), mgr.remove(aid))
            return _ok({"deleted": True})

        @app.get("/api/accounts/<aid>/check")
        def api_check_account(aid: str):
            """测试连接：按配置创建后端并探测登录态（不启动常驻连接）"""
            from astercore.core.backend import BackendConfig as _BC, get_registry as _reg
            cfg = mgr.get_config(aid)
            if cfg is None:
                return _err("账号未配置", 404)
            try:
                backend = _reg().create(
                    cfg.backend_name,
                    _BC(ws_url=cfg.ws_url, http_url=cfg.http_url,
                        access_token=cfg.access_token),
                    account_id=aid,
                )
                res = run_coro_sync(self._loop(), backend.check(), timeout=8)
                return _ok(res.to_dict() if hasattr(res, "to_dict") else res)
            except Exception as e:
                return _ok({"ok": False, "error": f"探测失败: {e}"})

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

        @app.post("/api/accounts/<aid>/plugins/reload")
        def api_acct_plugins_reload(aid: str):
            rt = _rt_of(aid)
            if rt is None:
                return _err("账号未运行", 404)
            name = request.args.get("name")
            n = run_coro_sync(self._loop(), rt.reload_plugins(name))
            return _ok({"plugin_count": n})

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

    # ---------- 鉴权路由与前置检查 ----------
    def _setup_auth_gate(self) -> None:
        from flask import request, session, jsonify

        app = self.app
        auth = self.auth

        @app.post("/api/login")
        def api_login():
            d = request.get_json(force=True, silent=True) or {}
            mode = auth.mode
            if mode == "password":
                if auth.password_ok(d.get("password")):
                    session["ac_auth"] = True
                    return _ok({"ok": True})
                return _err("密码错误", 401)
            if mode == "token":
                if auth.token_ok(d.get("token")):
                    session["ac_auth"] = True
                    return _ok({"ok": True})
                return _err("Token 错误", 401)
            session["ac_auth"] = True
            return _ok({})  # none 模式

        @app.post("/api/logout")
        def api_logout():
            session.pop("ac_auth", None)
            return _ok({})

        @app.get("/api/auth/status")
        def api_auth_status():
            # none 模式视为已通过
            authed = auth.mode == "none" or bool(session.get("ac_auth"))
            return _ok({"authed": authed, "mode": auth.mode,
                        "need": auth.mode != "none" and not authed,
                        "config": auth.public()})

        @app.post("/api/settings/auth")
        def api_set_auth():
            # 变更鉴权需要已有权限（none 模式首次设置视为允许；否则需已登录或正确凭证）
            if auth.mode != "none" and not session.get("ac_auth"):
                return _err("需要先登录", 401)
            d = request.get_json(force=True, silent=True) or {}
            if "mode" in d:
                auth.set_mode(str(d["mode"]))
            if d.get("password"):
                auth.set_password(str(d["password"]))
            if d.get("rotate_token"):
                tok = auth.rotate_token()
                return _ok({"token": tok, **auth.public()})
            return _ok(auth.public())

        # 前置检查：密码/token 模式下 API 需要凭证
        @app.before_request
        def _gate():
            p = request.path
            if p.startswith("/api/login") or p.startswith("/api/auth/") \
               or p.startswith("/assets/"):
                return None
            if auth.mode == "none":
                return None
            if auth.mode == "token":
                tok = request.headers.get("Authorization", "")
                if tok.startswith("Bearer "):
                    tok = tok[7:]
                if not tok:
                    tok = request.args.get("token")
                if auth.token_ok(tok):
                    return None
                if p.startswith("/api/"):
                    return jsonify({"ok": False, "error": "需要 Token"}), 401
                # 页面：跳转登录由前端 401 处理；直接放行 index 由前端接管
                return None
            if p.startswith("/api/"):
                if not session.get("ac_auth"):
                    return jsonify({"ok": False, "error": "未登录"}), 401
            return None

    def serve(self, host: str = "127.0.0.1", port: int = 8080) -> None:
        self.app.run(host=host, port=port, threaded=True, use_reloader=False)
