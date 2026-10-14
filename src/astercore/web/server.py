# 栖星 AsterCore · Web 服务（Flask，可选依赖）
# 设计（计划书 §4.1）：单端口 + 路径前缀分层；HTTP 为可选模块，本地默认走进程内 bridge。
# v0.1：提供 JSON API（账号状态/插件启停/实时日志）+ 一个最小面板页。

from __future__ import annotations

import asyncio
import logging
import threading
from pathlib import Path

from astercore import __version__
from astercore.core.manager import AccountConfig
from astercore.web.auth import AuthConfig
from astercore.web import ui_pages as _ui      # 面板页面片段（内容由后端生成，前端只挂载）
from typing import Any, Awaitable, Callable

log = logging.getLogger("astercore.web")

try:
    # session 必须模块级导入：_page_ctx 要读会话（鉴权=密码/令牌时判断是否已登录）。
    # 只在 _setup_auth_gate 里局部导入的话，_page_ctx 引用它会 NameError，
    # 表现是"开了密码鉴权之后，设置页整页 500"（默认 none 模式走不到这行，所以很难发现）。
    from flask import Flask, jsonify, request, send_from_directory, session
except ImportError:  # pragma: no cover
    Flask = None


def run_coro_sync(loop: asyncio.AbstractEventLoop,
                  coro: Awaitable[Any], timeout: float = 10.0) -> Any:
    """把协程投到 runtime 的事件循环并同步等待（供 Flask 线程调用）"""
    fut = asyncio.run_coroutine_threadsafe(coro, loop)
    return fut.result(timeout=timeout)


def _info_payload(rt=None, data_dir: str = "", accounts_dir: str = "",
                  plugin_dir=None) -> dict:
    """版本 + 各目录信息（面板显示 / 排障）。两种面板共用。"""
    from astercore import paths as _paths
    if rt is not None:
        pd = str(rt.plugin_loader.plugins_dir)
    elif plugin_dir:
        pd = str(plugin_dir)
    else:
        pd = str(_paths.default_plugins_dir())
    return {
        "version": __version__,
        "frozen": _paths.is_frozen(),
        "base_dir": str(_paths.app_base_dir()),
        "data_dir": str(data_dir or ""),
        "accounts_dir": str(accounts_dir or ""),
        "plugin_dir": pd,
    }


def _ok(data: Any = None):
    return jsonify({"ok": True, "data": data})


def _err(msg: str, code: int = 400, extra: dict | None = None):
    body = {"ok": False, "error": msg}
    if extra:
        body.update(extra)
    return jsonify(body), code


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

        # 面板用相对路径引用两端共用的表单资产（cfg_form.js/css），这样
        # file:// 桌面模式与浏览器模式都能加载；这里给出对应的 HTTP 路由。
        @app.get("/cfg_form.js")
        def shared_cfg_form_js():
            return send_from_directory(self.static_dir, "cfg_form.js")

        @app.get("/cfg_form.css")
        def shared_cfg_form_css():
            return send_from_directory(self.static_dir, "cfg_form.css")

        # 面板外壳与样式同样两端共用（file:// 桌面模式也走相对路径）
        @app.get("/panel.js")
        def shared_panel_js():
            return send_from_directory(self.static_dir, "panel.js")

        @app.get("/panel.css")
        def shared_panel_css():
            return send_from_directory(self.static_dir, "panel.css")

        # NapCat 官方图标（侧栏常驻入口 + 账号弹窗的连接方式选择卡用）：
        # 取自官方文档站 favicon（https://napneko.github.io/favicon.ico），
        # 原样转 PNG 放进 static/，随包分发（spec 收的是整个 static 目录）。
        @app.get("/napcat.png")
        def napcat_icon():
            return send_from_directory(self.static_dir, "napcat.png")

        # 连接引导（计划书 §3.1）：组件下载/写配置/托管 + 独立页面 /guide
        # 单独模块，避免把引导塞进两端必须字节一致的共享面板资产
        try:
            from .guide import add_guide_routes
            self.guide_api = add_guide_routes(
                app, state=getattr(self, "app_state", None),
                manager=getattr(self, "manager", None),
                loop_provider=getattr(self, "_loop_provider", None)) or {}
        except Exception:                       # noqa: BLE001 引导坏了不能拖垮面板
            log.exception("连接引导路由注册失败")

        # 首启引导页 /setup 与设置页 /settings（规格 §三；桌面专属页面，不碰共用资产）
        try:
            from .setup import add_setup_routes
            add_setup_routes(app, getattr(self, "app_state", None), getattr(self, "auth", None))
        except Exception:                       # noqa: BLE001
            log.exception("首启引导/设置路由注册失败")

        # 桌面端"机器人状态/账号浮层"（共用面板顶栏靠能力探测决定是否渲染；
        # Linux 端没有这个模块 → 404 → 面板自动隐藏顶栏）
        try:
            from .appapi import add_app_routes
            add_app_routes(app, getattr(self, "bot_controller", None),
                           getattr(self, "app_state", None))
        except Exception:                       # noqa: BLE001
            log.exception("桌面状态接口注册失败")

        @app.get("/api/info")
        def api_info():
            return _ok(_info_payload(self._rt()))

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
                "version": __version__,
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
                 auth_file: str | Path | None = None,
                 app_state=None, bot_controller=None) -> None:
        """app_state: core.app.AppState（运行方式/向导状态），可 None"""
        if Flask is None:
            raise RuntimeError("需要 Flask：pip install astercore[web]")
        self.manager = manager
        self._loop_provider = loop_provider
        self.app_state = app_state
        self.bot_controller = bot_controller   # 托盘/主界面共用的机器人状态机
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

    # ---------- 运行方式（接口与页面片段共用同一份口径） ----------
    def _runtime_payload(self) -> dict:
        """app_state 缺失时如实降级为空（例如测试里只给 manager 不给 app_state）"""
        st = self.app_state
        if st is None:
            return {"state": {}, "modes": {}}
        from astercore.app import BACKEND_MODES
        return {
            "state": {
                "backend_mode": st.backend_mode,
                "wizard_completed": st.wizard_completed,
                "needs_wizard": st.needs_wizard(),
                "risk_acknowledged": st.risk_acknowledged,
            },
            "modes": {k: {kk: vv for kk, vv in v.items()
                          if kk in ("label", "risk", "desc", "needs_ack")}
                      for k, v in BACKEND_MODES.items()},
        }

    def _routes(self) -> None:
        app = self.app
        mgr = self.manager

        @app.get("/")
        def index():
            return send_from_directory(self.static_dir, "index.html")

        # 面板用相对路径引用两端共用的表单资产（cfg_form.js/css），这样
        # file:// 桌面模式与浏览器模式都能加载；这里给出对应的 HTTP 路由。
        @app.get("/cfg_form.js")
        def shared_cfg_form_js():
            return send_from_directory(self.static_dir, "cfg_form.js")

        @app.get("/cfg_form.css")
        def shared_cfg_form_css():
            return send_from_directory(self.static_dir, "cfg_form.css")

        # 面板外壳与样式同样两端共用（file:// 桌面模式也走相对路径）
        @app.get("/panel.js")
        def shared_panel_js():
            return send_from_directory(self.static_dir, "panel.js")

        @app.get("/panel.css")
        def shared_panel_css():
            return send_from_directory(self.static_dir, "panel.css")

        # NapCat 官方图标（侧栏常驻入口 + 账号弹窗的连接方式选择卡用）：
        # 取自官方文档站 favicon（https://napneko.github.io/favicon.ico）。
        @app.get("/napcat.png")
        def napcat_icon():
            return send_from_directory(self.static_dir, "napcat.png")

        # ---------- 账号 ----------
        # 连接引导（计划书 §3.1）：组件下载/写配置/托管 + 独立页面 /guide
        # 单独模块，避免把引导塞进两端必须字节一致的共享面板资产
        try:
            from .guide import add_guide_routes
            self.guide_api = add_guide_routes(
                app, state=getattr(self, "app_state", None),
                manager=getattr(self, "manager", None),
                loop_provider=getattr(self, "_loop_provider", None)) or {}
        except Exception:                       # noqa: BLE001 引导坏了不能拖垮面板
            log.exception("连接引导路由注册失败")

        # 首启引导页 /setup 与设置页 /settings（规格 §三；桌面专属页面，不碰共用资产）
        try:
            from .setup import add_setup_routes
            add_setup_routes(app, getattr(self, "app_state", None), getattr(self, "auth", None))
        except Exception:                       # noqa: BLE001
            log.exception("首启引导/设置路由注册失败")

        # 桌面端"机器人状态/账号浮层"（共用面板顶栏靠能力探测决定是否渲染；
        # Linux 端没有这个模块 → 404 → 面板自动隐藏顶栏）
        try:
            from .appapi import add_app_routes
            add_app_routes(app, getattr(self, "bot_controller", None),
                           getattr(self, "app_state", None))
        except Exception:                       # noqa: BLE001
            log.exception("桌面状态接口注册失败")

        @app.get("/api/info")
        def api_info():
            rt = None
            for _r in mgr.runtimes.values():
                rt = _r
                break
            return _ok(_info_payload(
                rt,
                data_dir=getattr(mgr, "data_root", ""),
                accounts_dir=getattr(mgr, "accounts_dir", ""),
                plugin_dir=getattr(mgr, "plugin_dir", None),
            ))

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

        @app.post("/api/accounts/<aid>/update")
        def api_update_account(aid: str):
            """编辑账号 backend 配置（改 ws/http/token/显示名）"""
            d = request.get_json(force=True, silent=True) or {}
            cfg = mgr.get_config(aid)
            if cfg is None:
                return _err("账号未配置", 404)
            b = d.get("backend") or {}
            if d.get("display_name"):
                cfg.display_name = str(d["display_name"])
            if "name" in b:
                cfg.backend_name = str(b["name"])
            if "ws_url" in b:
                cfg.ws_url = str(b["ws_url"])
            if "http_url" in b:
                cfg.http_url = str(b["http_url"])
            if "access_token" in b:
                cfg.access_token = str(b["access_token"])
            mgr.save_config(cfg)
            return _ok({"account_id": aid})

        # 面板的「添加/编辑/删除/启停账号」按钮打的是 /api/app/account-*。
        # 桌面 file:// 模式下这几条走 RPC（launch.py 注册），可面板**也能用浏览器打开**
        # （端口模式 / 多账号面板）：那时只有 HTTP，不在这里补路由就 404「接口不存在」——
        # 用户报的"点保存没反应"就是这个。
        def _acct_act_http(action: str):
            d = request.get_json(force=True, silent=True) or {}
            aid = str(d.get("account_id") or "").strip()
            if not aid:
                return _err("缺少 account_id", 400)
            try:
                run_coro_sync(self._loop(), getattr(mgr, action)(aid))
            except KeyError:
                return _err("账号未配置", 404)
            except Exception as e:                  # noqa: BLE001
                return _err(f"{action} 失败: {e}", 500)
            return _ok({"account_id": aid, action: True})

        @app.post("/api/app/account-save")
        def api_app_account_save():
            d = request.get_json(force=True, silent=True) or {}
            aid = str(d.get("account_id") or "").strip()
            if not aid:
                return _err("缺少 account_id", 400)
            b = d.get("backend") or {}
            old = mgr.get_config(aid)
            mgr.save_config(AccountConfig(
                account_id=aid,
                display_name=str(d.get("display_name") or (old.display_name if old else aid)),
                backend_name=str(b.get("name") or (old.backend_name if old else "onebot")),
                ws_url=str(b.get("ws_url") or (old.ws_url if old else "ws://127.0.0.1:3001")),
                http_url=str(b.get("http_url") or (old.http_url if old else "http://127.0.0.1:3000")),
                access_token=str(b.get("access_token") if "access_token" in b
                                 else (old.access_token if old else "")),
            ))
            return _ok({"account_id": aid, "saved": True, "created": old is None})

        @app.post("/api/app/account-delete")
        def api_app_account_delete():
            d = request.get_json(force=True, silent=True) or {}
            aid = str(d.get("account_id") or "").strip()
            if not aid:
                return _err("缺少 account_id", 400)
            fn = getattr(mgr, "delete", None) or getattr(mgr, "remove", None)
            if fn is None:
                return _err("当前管理器不支持删除账号", 501)
            try:
                run_coro_sync(self._loop(), fn(aid))
            except KeyError:
                return _err("账号未配置", 404)
            return _ok({"account_id": aid, "deleted": True})

        @app.post("/api/app/account-start")
        def api_app_account_start():
            return _acct_act_http("start")

        @app.post("/api/app/account-stop")
        def api_app_account_stop():
            return _acct_act_http("stop")

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
                return _ok({"running": False, "connected": False})
            try:
                st = rt.backend.status()
            except Exception:
                st = {}
            return _ok({"running": True, "backend": rt.backend.name,
                        "plugin_count": len(rt.list_plugins()),
                        "account_id": str(rt.account_id), **st})

        @app.get("/api/accounts/<aid>/plugins")
        def api_account_plugins(aid: str):
            rt = _rt_of(aid)
            if rt is None:
                return _err("账号未运行", 404)
            items = rt.list_plugins()
            # 供面板显示人类可读的名字（共用外壳按 `display` 取值，与 Linux 端同约定）
            for it in items:
                it["display"] = it.get("name_cn") or it.get("name")
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

        @app.get("/api/accounts/<aid>/plugins/<name>/schema")
        def api_acct_plugin_schema(aid: str, name: str):
            """插件可配置项声明（面板据此渲染表单）。

            没有声明的插件返回按当前值推断的字段 + declared=false —— 面板会注明
            "这是自动推断的"，并保留 JSON 原文编辑兜底，所以不会出现"空页面"。
            """
            rt = _rt_of(aid)
            if rt is None:
                return _err("账号未运行", 404)
            payload = rt.get_plugin_schema(name)
            # 面板标题与保存提示用人类可读的插件名（取不到就退回插件 key）
            payload["plugin_name"] = name
            for it in rt.list_plugins():
                if it.get("name") == name:
                    payload["plugin_name"] = it.get("name_cn") or name
                    break
            return _ok(payload)

        @app.post("/api/accounts/<aid>/plugins/<name>/config")
        def api_acct_plugin_config_save(aid: str, name: str):
            rt = _rt_of(aid)
            if rt is None:
                return _err("账号未运行", 404)
            data = request.get_json(force=True, silent=True) or {}
            want_raw = request.args.get("raw") in ("1", "true", "yes")
            if want_raw:
                ok = run_coro_sync(self._loop(), rt.save_plugin_config(name, data))
                return _ok({"saved": ok, "validated": False}) if ok else _err("保存失败", 400)

            from astercore.core.config_schema import validate_and_merge
            payload = rt.get_plugin_schema(name)
            # 只有插件**自己声明**了 schema 时才做严格校验/类型转换；
            # 推断出来的字段只用于渲染，不能拿它当法律去改写用户数据。
            declared = bool(payload.get("declared"))
            fields = ({f["name"]: f for f in payload.get("fields", [])}
                      if declared else {})
            current = rt.get_plugin_config(name)
            ok, merged, errors = validate_and_merge(
                fields, current, data, strict=declared)
            if not ok:
                # 精确到字段的错误，前端可以标红到控件上
                return _err("配置校验未通过", 400, {"field_errors": errors})
            saved = run_coro_sync(self._loop(), rt.save_plugin_config(name, merged))
            if not saved:
                return _err("保存失败", 400)
            return _ok({"saved": True, "validated": declared, "config": merged})

        @app.post("/api/accounts/<aid>/plugins/reload")
        def api_acct_plugins_reload(aid: str):
            rt = _rt_of(aid)
            if rt is None:
                return _err("账号未运行", 404)
            name = request.args.get("name")
            n = run_coro_sync(self._loop(), rt.reload_plugins(name))
            return _ok({"plugin_count": n})


        # ---------- 页面片段：骨架在共用的 panel.js，页面内容由这里生成 ----------
        def _acct_status(aid: str) -> dict:
            rt = _rt_of(aid)
            if rt is None:
                return {"running": False, "connected": False}
            try:
                st = rt.backend.status()
            except Exception:
                st = {}
            return {"running": True, "backend": rt.backend.name,
                    "plugin_count": len(rt.list_plugins()),
                    "account_id": str(rt.account_id), **st}

        def _page_ctx(page: str) -> dict:
            """只取该页真正要用的数据（少查一点就快一点）"""
            aid = (request.args.get("aid") or "").strip()
            ctx = {"current": aid}
            cfg = mgr.get_config(aid) if aid else None
            if cfg is not None:
                ctx["account"] = {
                    "account_id": aid,
                    "display_name": cfg.display_name,
                    "backend_name": cfg.backend_name,
                    **_acct_status(aid),
                }
            if page == "accounts":
                # 账号行要带上连接信息：编辑弹窗靠这些 data-* 预填，
                # 缺了就退回默认值 —— 用户只是改个显示名，ws/token 就被默认值覆盖了。
                # 只喂页面 ctx（/api/accounts 仍走 mgr.scan()，不把 token 撒到接口上）。
                rows = []
                for row in mgr.scan():
                    c = mgr.get_config(row.get("account_id"))
                    if c is not None:
                        row = dict(row, ws_url=c.ws_url, http_url=c.http_url,
                                   access_token=c.access_token)
                    rows.append(row)
                ctx["accounts"] = rows
                edit = (request.args.get("edit") or "").strip()
                ecfg = mgr.get_config(edit) if edit else None
                if ecfg is not None:
                    ctx["editing"] = {
                        "account_id": ecfg.account_id,
                        "display_name": ecfg.display_name,
                        "ws_url": getattr(ecfg, "ws_url", ""),
                        "http_url": getattr(ecfg, "http_url", ""),
                        "access_token": getattr(ecfg, "access_token", ""),
                    }
            if page == "dashboard":
                ctx["info"] = _info_payload(
                    _rt_of(aid) if aid else None,
                    data_dir=getattr(mgr, "data_root", ""),
                    accounts_dir=getattr(mgr, "accounts_dir", ""),
                    plugin_dir=getattr(mgr, "plugin_dir", None))
                rt = _rt_of(aid)
                ctx["plugins"] = rt.list_plugins() if rt is not None else []
            if page == "config":
                pkey = (request.args.get("plugin") or "").strip()
                rt = _rt_of(aid)
                items = rt.list_plugins() if rt is not None else []
                if not pkey and items:
                    pkey = items[0].get("name") or ""
                ctx["plugin_key"] = pkey
                for it in items:
                    if it.get("name") == pkey:
                        # 操作栏标题用人类可读名（`list_plugins()` 里是 name_cn）
                        ctx["plugin_name"] = (it.get("display") or it.get("name_cn")
                                              or pkey)
            if page == "napcat":
                # NapCat 管理页首屏要显示装没装/跑没跑（刷新按钮走 /api/guide/status）
                try:
                    from astercore import components as _comp
                    from astercore.paths import app_base_dir as _abd
                    ctx["components"] = {"napcat": _comp.host_status(
                        _comp.get_component("napcat"), _abd())}
                except Exception:                # noqa: BLE001 组件层坏了不该整页 500
                    ctx["components"] = {}
            # 「设置」是合并页（ui_pages._settings_all = 运行方式 + 安全设置 +
            # 页面与端口）。三个分页各自要的 ctx 必须同样喂给它，否则合并页
            # 只剩各段自己的**兜底默认值**：后端模式显示"当前没有可选项"、
            # 鉴权方式退回"不鉴权"、端口退回 34567 —— 用户点保存就把这些默认值
            # 写回配置（等于静默关掉鉴权 / 改错端口）。分页正常、合并页不正常，
            # 是最容易漏掉的一种"只有一处接线漏了"的 bug。
            if page in ("settings-runtime", "settings"):
                ctx["runtime"] = self._runtime_payload()
            if page in ("settings-page", "settings"):
                st = self.app_state
                # 端口只回填"用户显式设过的值"；没设过就留空，默认值只当灰色占位。
                # 以前拿 web_config()（含内部默认 18750）当已填值显示，用户分不清
                # 那是自己设的还是默认的（用户要求：此处默认为空、灰色显示默认端口）。
                pg = {"mode": "window", "port": "", "default_port": _ui.DEFAULT_PAGE_PORT}
                if st is not None:
                    try:
                        cfg = st.page_config()
                    except Exception:            # noqa: BLE001 老配置缺字段也不该整页 500
                        cfg = {}
                    pg["mode"] = "port" if cfg.get("mode") == "port" else "window"
                    raw = st.data.get("page") if isinstance(st.data.get("page"), dict) else {}
                    web = st.data.get("web") if isinstance(st.data.get("web"), dict) else {}
                    if (raw or {}).get("port") or (web or {}).get("port"):
                        pg["port"] = cfg.get("port") or ""
                ctx["page"] = pg
            if page in ("settings-auth", "settings"):
                authed = self.auth.mode == "none" or bool(session.get("ac_auth"))
                ctx["auth"] = {"authed": authed, "mode": self.auth.mode,
                               "config": self.auth.public()}
            if page in ("settings", "settings-window"):
                # 点 ✕ 的默认动作（设置页可改；自建关闭弹窗也读它）
                ctx["window"] = (self.app_state.window_config() if self.app_state is not None
                                 else {"close_action": "ask"})
            return ctx

        @app.get("/ui/page/<page>")
        def ui_page(page: str):
            if page not in _ui.PAGES:
                return _err(f"未知页面: {page}", 404)
            return _ok(_ui.render_page(page, _page_ctx(page)))

        @app.get("/ui/status")
        def ui_status():
            """状态区片段（通用形状 {html:{id:内容}, text:{id:文本}}，与 Linux 端同一套）"""
            return _ok(_ui.render_status(_page_ctx("dashboard")))

        @app.get("/api/accounts/<aid>/groups")
        def api_acct_groups(aid: str):
            """群列表（调试台）：调 backend get_group_list"""
            rt = _rt_of(aid)
            if rt is None:
                return _err("账号未运行", 404)
            res = run_coro_sync(self._loop(), rt.action("get_group_list", {}), timeout=8)
            return _ok(res.data if res.ok else [])

        @app.post("/api/accounts/<aid>/send")
        def api_acct_send(aid: str):
            """调试台发消息：{group_id 或 user_id, text}"""
            rt = _rt_of(aid)
            if rt is None:
                return _err("账号未运行", 404)
            d = request.get_json(force=True, silent=True) or {}
            text = str(d.get("text") or "").strip()
            if not text:
                return _err("缺少 text", 400)
            if d.get("group_id"):
                res = run_coro_sync(self._loop(), rt.action(
                    "send_group", {"group_id": d["group_id"],
                                   "message": [{"type": "text", "data": {"text": text}}]}),
                    timeout=10)
            elif d.get("user_id"):
                res = run_coro_sync(self._loop(), rt.action(
                    "send_private", {"user_id": d["user_id"],
                                     "message": [{"type": "text", "data": {"text": text}}]}),
                    timeout=10)
            else:
                return _err("缺少 group_id 或 user_id", 400)
            return _ok(res.to_dict() if hasattr(res, "to_dict") else res)

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

    # ---------- 统一错误为 JSON（前端可读，避免 500 HTML 卡死 fetch） ----------
    def _add_error_handlers(self) -> None:
        app = self.app

        @app.errorhandler(404)
        def _404(e):
            if request.path.startswith("/api/"):
                return jsonify({"ok": False, "error": "接口不存在"}), 404
            return ("Not Found", 404)

        @app.errorhandler(500)
        def _500(e):
            return jsonify({"ok": False, "error": f"服务器错误: {e}"}), 500

        @app.errorhandler(Exception)
        def _exc(e):
            from werkzeug.exceptions import HTTPException
            if isinstance(e, HTTPException):
                return jsonify({"ok": False, "error": e.description or e.name}), e.code or 500
            return jsonify({"ok": False, "error": f"异常: {type(e).__name__}: {e}"}), 500

    # ---------- 运行方式（桌面壳 AppState Web 化） ----------
    def _add_runtime_routes(self) -> None:
        if self.app_state is None:
            return
        from astercore.app import BACKEND_MODES
        app = self.app
        st = self.app_state

        @app.get("/api/settings/runtime")
        def api_runtime_get():
            return _ok(self._runtime_payload())

        @app.post("/api/settings/runtime")
        def api_runtime_set():
            if st.backend_mode != "none" and st.wizard_completed and not self._session_authed():
                return _err("需先登录", 401)
            d = request.get_json(force=True, silent=True) or {}
            if d.get("action") == "reset_wizard":
                st.reset_wizard()
                return _ok({"needs_wizard": st.needs_wizard()})
            mode = d.get("backend_mode")
            if not mode:
                return _err("缺少 backend_mode", 400)
            ok, msg = st.set_backend(str(mode), ack_risk=bool(d.get("ack_risk")))
            if not ok:
                return _err(msg, 400)
            return _ok(st.startup_plan())

    def _session_authed(self) -> bool:
        from flask import session
        if self.auth.mode == "none":
            return True
        return bool(session.get("ac_auth"))

    # ---------- 鉴权路由与前置检查 ----------
    def _setup_auth_gate(self) -> None:
        self._add_error_handlers()
        self._add_runtime_routes()
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
            # 开启密码/令牌鉴权前必须先有对应凭证，否则保存完就再也进不来了
            # （面板改成"改完即存"后，误点一下就能把自己锁在外面，所以这里硬拦）
            want = str(d.get("mode") or auth.mode)
            if want == "password" and not (str(d.get("password") or "")
                                           or auth.data.get("password_hash")):
                return _err("要启用密码鉴权，请先填一个密码")
            if want == "token" and not auth.data.get("token"):
                return _err("要启用令牌鉴权，请先点「重新生成令牌」")
            if "mode" in d:
                auth.set_mode(str(d["mode"]))
            if d.get("password"):
                auth.set_password(str(d["password"]))
            if d.get("rotate_token"):
                tok = auth.rotate_token()
                return _ok({"token": tok, **auth.public()})
            # 刚开启鉴权：当前这个会话本来就能改配置，顺手标记为已登录。
            # 否则面板改成"改完即存"后，勾一下密码鉴权，紧接着的下一次
            # /api/* 就 401 —— 用户会以为"存不上"（实际是被自己刚设的闸门挡住）。
            if auth.mode != "none":
                session["ac_auth"] = True
            return _ok(auth.public())

        # 前置检查：密码/token 模式下 API 需要凭证
        @app.before_request
        def _gate():
            p = request.path
            # 进程内 RPC（js_api → RpcHub → test_client）：可信调用，跳过鉴权。
            # 手册 §4.1「js_api 不鉴权」——它就在本进程里，只有本机程序能调，
            # 加鉴权只会让桌面版莫名其妙要用户输密码。标记用 threading.local，
            # **不走 header**，所以对外暴露 HTTP 时也不构成绕过面。
            from astercore.rpc import is_trusted
            if is_trusted():
                return None
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
        """启动 HTTP 服务。

        优先 **waitress**（手册 §1.3 的坑：Flask 内置服务器在多连接下会出怪问题；
        waitress 默认只有 4 个线程，面板的轮询/日志流一开就占满，所以 threads=32 起步）。
        没装 waitress 就退回 Flask 内置服务器，并**明确告警**而不是静默降级 ——
        否则用户会遇到"面板偶尔卡死"这种查不出原因的现象。
        """
        try:
            from waitress import serve as _waitress_serve
        except ImportError:
            log.warning("未安装 waitress，退回 Flask 内置服务器（多连接/轮询下不稳）；"
                        "建议 pip install waitress")
            self.app.run(host=host, port=port, threaded=True, use_reloader=False)
            return
        log.info("Web 服务已启动: http://%s:%s（waitress, 32 线程）", host, port)
        _waitress_serve(self.app, host=host, port=port, threads=32)
