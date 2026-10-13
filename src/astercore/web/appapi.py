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
from typing import Any, Callable

log = logging.getLogger("astercore.web.appapi")


def _ok(data: Any = None):
    from flask import jsonify
    return jsonify({"ok": True, "data": data})


def _err(msg: str, code: int = 400):
    from flask import jsonify
    return jsonify({"ok": False, "error": msg}), code


def add_app_routes(app, controller=None, state=None) -> None:
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
