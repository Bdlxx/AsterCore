# 栖星 AsterCore · 桌面端"机器人状态/账号浮层"接口（供共用面板顶栏调用）
#
# 为什么在 astercore 而不是共用面板里：面板（web/static/*）双端字节一致，Linux 端
# 没有"程序机器人"这个概念。所以**接口只在这里提供**，面板通过能力探测决定渲染与否：
#   GET /api/app/state 有响应 → 渲染桌面顶栏；404（Linux 端）→ 不渲染。
#
# 状态由 `botstate.BotController` 统一提供 —— 与托盘菜单读的是**同一份 snapshot()**，
# 所以"托盘菜单与主界面按钮状态实时同步"是结构上保证的，不是两边各算一遍。

from __future__ import annotations

import logging
import re
from typing import Any, Callable

log = logging.getLogger("astercore.web.appapi")


def _ok(data: Any = None):
    from flask import jsonify
    return jsonify({"ok": True, "data": data})


def _err(msg: str, code: int = 400):
    from flask import jsonify
    return jsonify({"ok": False, "error": msg}), code


_SESSIONS: dict[str, Any] = {}


def _session():
    """更新会话（按安装目录缓存；HTTP 与 RPC 共用同一份状态与进度）"""
    from astercore import __version__, updater
    from astercore.paths import app_base_dir
    base = str(app_base_dir())
    if base not in _SESSIONS:
        _SESSIONS[base] = updater.UpdateSession(__version__, base)
    return _SESSIONS[base]


def add_app_routes(app, controller=None, state=None, quit_cb=None) -> None:
    """挂 /api/app/*；controller 为 None 时只提供只读空状态（不假装能启停）"""
    if controller is None and state is None:
        return

    def _snap() -> dict:
        if controller is not None:
            return controller.snapshot()
        return {"state": "stopped", "state_label": "已停止", "button": "启动机器人",
                "button_enabled": True, "restart_enabled": False, "nickname": "—",
                "accounts": []}

    @app.get("/api/app/state")
    def app_state():
        return _ok(_snap())

    @app.post("/api/app/start")
    def app_start():
        if controller is None:
            return _err("当前环境不支持启停", 501)
        ok, msg = controller.start()
        return _ok({"ok": ok, "msg": msg, "state": _snap()}) if ok else _err(msg, 409)

    @app.post("/api/app/stop")
    def app_stop():
        if controller is None:
            return _err("当前环境不支持启停", 501)
        ok, msg = controller.stop()
        return _ok({"ok": ok, "msg": msg, "state": _snap()}) if ok else _err(msg, 409)

    @app.post("/api/app/restart")
    def app_restart():
        if controller is None:
            return _err("当前环境不支持重启", 501)
        ok, msg = controller.restart()
        return _ok({"ok": ok, "msg": msg, "state": _snap()}) if ok else _err(msg, 409)

    @app.post("/api/app/account/<aid>/current")
    def app_set_current(aid: str):
        if controller is None:
            return _err("当前环境不支持切换账号", 501)
        ids = {str(a.get("account_id")) for a in _snap().get("accounts", [])}
        if aid not in ids:
            return _err(f"账号不存在: {aid}", 404)
        controller.set_current_account(aid)
        return _ok(_snap())

    # ---------------- 打开外部链接（引导页"去官网下载"用） ----------------
    @app.post("/api/app/open-url")
    def app_open_url():
        import webbrowser
        from flask import request
        d = request.get_json(silent=True) or {}
        url = str(d.get("url") or "").strip()
        if not re.match(r"^https?://", url):
            return _err("只允许 http/https 链接")
        try:
            ok = webbrowser.open(url, new=2)
        except Exception as e:                      # noqa: BLE001
            return _err(f"打开浏览器失败：{e}", 500)
        return _ok({"opened": bool(ok), "url": url})

    # ---------------- 应用内更新（计划书 §369） ----------------
    @app.get("/api/app/update/status")
    def update_status():
        return _ok(_session().status())

    @app.post("/api/app/update/check")
    def update_check():
        return _ok(_session().check())

    @app.post("/api/app/update/start")
    def update_start():
        return _ok(_session().start())

    @app.post("/api/app/update/apply")
    def update_apply():
        from astercore import updater
        st = _session().status()
        script = st.get("script") or ""
        if st.get("state") != "done" or not script:
            return _err("还没有备好更新（先检查并下载）", 409)
        if not updater.apply_now(script):
            return _err("交接脚本不存在或无法执行", 500)
        if quit_cb is not None:
            try:
                quit_cb()                          # 关掉本进程，让脚本替换目录并重新拉起
            except Exception:                      # noqa: BLE001
                log.exception("退出以应用更新失败")
        return _ok({"applied": True, "hint": "程序将退出并应用更新，稍后会自动重新打开"})

    @app.get("/api/app/accounts")
    def app_accounts():
        """账号浮层用：头像（首字母）、昵称、QQ、当前项"""
        rows = []
        for a in _snap().get("accounts", []):
            name = str(a.get("display_name") or a.get("account_id") or "")
            rows.append({
                "account_id": str(a.get("account_id")),
                "display_name": name,
                "short": name[:1] or "?",
                "running": bool(a.get("running")),
                "current": bool(a.get("current")),
            })
        return _ok({"accounts": rows})
