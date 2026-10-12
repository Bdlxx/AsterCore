# 栖星 AsterCore · Windows 连接引导（计划书 §3.1）
#
# 独立页面 `/guide` + `/api/guide/*`：
#   - **不塞进两端共享的面板资产**（那几个文件双端必须字节一致，改它们会牵动 Linux 端），
#     这里自带一个单文件页面；桌面版首启可直接打开它。
#   - 组件（NapCat / Lagrange）的下载/配置/托管全部复用语 `astercore.components`，
#     与「内置协议直登」后端共用同一套实现与状态。
#
# 场景对照（计划书 §3.1）：
#   A 主路径：下载安装 → 自动写配置（127.0.0.1 + 自动端口 + 自动 token）→ 扫码 → 自动连接
#   B 已有自装：`/api/guide/discover` 自动发现（先看组件目录，再探本机默认端口）
#   C 远程/已有实例：`/api/guide/test` 三件套（ws/http/token）测连接

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any, Callable

from .. import components as C

log = logging.getLogger("astercore.web.guide")

# 安装任务进度（引导页轮询 /api/guide/status 时一起返回）
_JOBS: dict[str, dict[str, Any]] = {}


def _ok(data: Any = None):
    from flask import jsonify
    return jsonify({"ok": True, "data": data})


def _err(msg: str, code: int = 400):
    from flask import jsonify
    return jsonify({"ok": False, "error": msg}), code


def add_guide_routes(app, base_dir: Callable[[], Path] | None = None,
                     state: Any | None = None,
                     manager: Any | None = None,
                     loop_provider: Callable[[], Any] | None = None,
                     mode_provider: Callable[[], str] | None = None,
                     mode_setter: Callable[[str, bool], tuple[bool, str]] | None = None,
                     ) -> None:
    """把引导页面与接口挂到面板 app 上（两端都调用；Linux 上同样可用）

    state: core.app.AppState —— 有它就能在**界面里**选运行方式/勾风险确认
    （windowed 模式没有控制台，控制台向导走不了，这一步必须在界面完成）。
    """
    from ..paths import app_base_dir

    get_base = base_dir or app_base_dir

    # 运行方式：优先用 state（面板默认传），其次用显式给的 provider/setter
    _get_mode = mode_provider or (lambda: getattr(state, "backend_mode", "") if state else "")
    _set_mode = mode_setter
    if _set_mode is None and state is not None:
        _set_mode = lambda mode, ack: state.set_backend(mode, ack_risk=ack)  # noqa: E731

    def _modes_meta() -> list[dict[str, Any]]:
        try:
            from ..app import BACKEND_MODES
            return [{"key": k, **v} for k, v in BACKEND_MODES.items()]
        except Exception:                       # noqa: BLE001 元数据拿不到不影响其它功能
            return []

    def _body() -> dict[str, Any]:
        from flask import request
        return request.get_json(silent=True) or {}

    def _comp_or_400(key: str):
        try:
            return C.get_component(key), None
        except KeyError as e:
            return None, _err(str(e), 400)

    # ---------------- 页面 ----------------
    @app.get("/guide")
    def guide_page():
        from flask import Response
        return Response(GUIDE_HTML, mimetype="text/html; charset=utf-8")

    # ---------------- 状态 / 发现 ----------------
    @app.get("/api/guide/mode")
    def guide_mode():
        """运行方式（界面里选；windowed 无控制台，这一步必须在界面完成）"""
        return _ok({
            "mode": _get_mode(),
            "modes": _modes_meta(),
            "can_set": _set_mode is not None,
        })

    @app.post("/api/guide/mode")
    def guide_set_mode():
        b = _body()
        mode = str(b.get("mode") or "").strip()
        ack = bool(b.get("ack_risk"))
        if not mode:
            return _err("缺 mode")
        if _set_mode is None:
            return _err("当前环境不支持在界面里切换运行方式", 501)
        ok, msg = _set_mode(mode, ack)
        if not ok:
            return _err(msg, 400)
        return _ok({"mode": _get_mode(), "msg": msg})

    @app.get("/api/guide/status")
    def guide_status():
        base = get_base()
        return _ok({
            "supported": True,
            "base_dir": str(base),
            "mode": _get_mode(),
            "components": {k: C.host_status(c, base) for k, c in C.COMPONENTS.items()},
            "jobs": dict(_JOBS),
        })

    @app.post("/api/guide/discover")
    def guide_discover():
        found = C.discover_local(get_base())
        return _ok({"found": found, "count": len(found)})

    @app.post("/api/guide/test")
    def guide_test():
        b = _body()
        http_url = str(b.get("http_url") or "").strip()
        if not http_url:
            return _err("缺 http_url")
        return _ok(C.probe_endpoint(http_url, str(b.get("token") or "")))

    # ---------------- 安装（后台线程，页面轮询进度） ----------------
    @app.post("/api/guide/install")
    def guide_install():
        b = _body()
        comp, bad = _comp_or_400(str(b.get("key") or ""))
        if bad:
            return bad
        job = _JOBS.setdefault(comp.key, {})
        if job.get("running"):
            return _ok({"started": False, "msg": "已有安装任务在进行", "job": job})
        job.clear()
        job.update(running=True, step="downloading", got=0, total=0, msg="")

        def _progress(got: int, total: int) -> None:
            job.update(got=got, total=total)

        base = get_base()
        url = b.get("url") or None
        zip_path = b.get("zip") or None

        def _run() -> None:
            try:
                ok, msg = C.provision(comp, base, url=url, zip_path=zip_path,
                                      progress=_progress)
                job.update(running=False, step="done" if ok else "failed", msg=msg)
            except Exception as e:                       # noqa: BLE001 后台失败要留痕
                log.exception("组件安装失败: %s", comp.key)
                job.update(running=False, step="failed",
                           msg=f"{type(e).__name__}: {e}")

        threading.Thread(target=_run, daemon=True, name=f"guide-install-{comp.key}").start()
        return _ok({"started": True, "job": job})

    # ---------------- 账号：一键建账号 + 账号级连接方式 ----------------
    _BACKENDS = [
        {"key": "onebot", "label": "NapCat（外部客户端，OneBot v11）",
         "desc": "推荐：走官方登录通道，风控最低。需要组件 + 本机 QQ。"},
        {"key": "lagrange", "label": "内置协议直登（实验）",
         "desc": "免装客户端，内核自托管 Lagrange.OneBot。非官方协议，建议小号。"},
        {"key": "null", "label": "演示模式（不连协议）",
         "desc": "只跑内核与插件，用于开发/演示。"},
    ]

    def _component_for_backend(name: str) -> str | None:
        """后端名 → 组件 key（内置协议要自己拉进程；napcat 走外部客户端但仍需组件配置）"""
        return {"lagrange": "lagrange", "onebot": "napcat"}.get(name)

    def _run_on_loop(coro):
        """面板主循环在跑就投过去；否则（测试/无循环）当前线程直接跑"""
        import asyncio
        loop = None
        if loop_provider is not None:
            try:
                loop = loop_provider()
            except Exception:                       # noqa: BLE001
                loop = None
        if loop is not None and getattr(loop, "is_running", lambda: False)():
            return asyncio.run_coroutine_threadsafe(coro, loop).result(timeout=120)
        return asyncio.run(coro)

    def _start_account(aid: str) -> tuple[bool, str]:
        try:
            _run_on_loop(manager.start(aid))
            return True, ""
        except Exception as e:                      # noqa: BLE001 起不来要如实回给界面
            log.warning("账号 %s 启动失败: %s", aid, e)
            return False, f"{type(e).__name__}: {e}"

    def _restart_account(aid: str) -> tuple[bool, str]:
        try:
            _run_on_loop(manager.restart(aid))
            return True, ""
        except Exception as e:                      # noqa: BLE001
            log.warning("账号 %s 重启失败: %s", aid, e)
            return False, f"{type(e).__name__}: {e}"

    @app.get("/api/guide/accounts")
    def guide_accounts():
        if manager is None:
            return _err("当前面板没有账号管理器", 501)
        base = get_base()
        out = []
        for row in manager.scan():
            aid = str(row.get("account_id"))
            cfg = manager.get_config(aid)
            comp_key = _component_for_backend(getattr(cfg, "backend_name", "") or "")
            comp = C.COMPONENTS.get(comp_key) if comp_key else None
            out.append({
                "account_id": aid,
                "display_name": row.get("display_name") or aid,
                "backend_name": getattr(cfg, "backend_name", "") or "onebot",
                "ws_url": getattr(cfg, "ws_url", ""),
                "http_url": getattr(cfg, "http_url", ""),
                "running": bool(row.get("running")),
                "component": comp_key,
                "component_installed": C.is_installed(comp, base) if comp else None,
            })
        return _ok({"accounts": out, "backends": _BACKENDS})

    @app.post("/api/guide/account")
    def guide_create_account():
        """一键建账号：写组件配置（端口/token 自动、与账号一致）→ 写 backend.json → 启动"""
        if manager is None:
            return _err("当前面板没有账号管理器", 501)
        b = _body()
        aid = str(b.get("account_id") or "").strip()
        if not aid:
            return _err("缺 account_id（QQ 号）")
        backend_name = str(b.get("backend_name") or "").strip()
        comp_key = str(b.get("key") or "").strip()
        if not backend_name:
            backend_name = {"lagrange": "lagrange", "napcat": "onebot",
                            "null": "null"}.get(comp_key, "onebot")
        if backend_name not in {x["key"] for x in _BACKENDS}:
            return _err(f"未知连接方式: {backend_name}")

        base = get_base()
        ws_url, http_url, token = "", "", ""
        comp_key = comp_key or _component_for_backend(backend_name)
        if comp_key and b.get("write_config", True):
            comp = C.COMPONENTS.get(comp_key)
            if comp is None:
                return _err(f"未知组件: {comp_key}")
            if not C.is_installed(comp, base):
                return _err(f"组件还没装：请先在第②步『下载并安装』{comp.label}", 409)
            cfg_data = _write_config(comp, {"qq": aid, "ws_port": b.get("ws_port"),
                                            "http_port": b.get("http_port"),
                                            "token": b.get("token")})
            ws_url, http_url = cfg_data["ws_url"], cfg_data["http_url"]
            token = cfg_data["access_token"]

        from ..core.manager import AccountConfig
        old = manager.get_config(aid)
        cfg = AccountConfig(
            account_id=aid,
            display_name=str(b.get("display_name") or (old.display_name if old else aid)),
            backend_name=backend_name,
            ws_url=ws_url or (old.ws_url if old else "ws://127.0.0.1:3001"),
            http_url=http_url or (old.http_url if old else "http://127.0.0.1:3000"),
            access_token=token or (old.access_token if old else ""),
        )
        manager.save_config(cfg)

        started, start_err = False, ""
        if b.get("start", True):
            started, start_err = _start_account(aid)

        # 全局运行方式跟着走，避免"账号是内置协议、模式还写着 napcat"
        if state is not None and backend_name in ("lagrange", "null"):
            try:
                state.set_backend(backend_name, ack_risk=True)
            except Exception:                       # noqa: BLE001
                pass
        return _ok({"account_id": aid, "backend_name": backend_name,
                    "ws_url": cfg.ws_url, "http_url": cfg.http_url,
                    "started": started, "start_error": start_err})

    @app.post("/api/guide/account/<aid>/backend")
    def guide_set_account_backend(aid: str):
        """账号级连接方式：改 backend.json；必要时补组件配置；在跑就重启"""
        if manager is None:
            return _err("当前面板没有账号管理器", 501)
        cfg = manager.get_config(aid)
        if cfg is None:
            return _err(f"账号不存在: {aid}", 404)
        b = _body()
        name = str(b.get("backend_name") or "").strip() or {
            "lagrange": "lagrange", "napcat": "onebot", "null": "null",
        }.get(str(b.get("key") or "").strip(), "")
        if name not in {x["key"] for x in _BACKENDS}:
            return _err(f"未知连接方式: {name or '(空)'}")

        base = get_base()
        comp_key = _component_for_backend(name)
        if comp_key and b.get("write_config", True):
            comp = C.COMPONENTS.get(comp_key)
            if not C.is_installed(comp, base):
                return _err(f"组件还没装：请先『下载并安装』{comp.label}", 409)
            data = _write_config(comp, {"qq": aid})     # 端口/token 自动，且与账号一致
            cfg.ws_url, cfg.http_url = data["ws_url"], data["http_url"]
            cfg.access_token = data["access_token"]

        cfg.backend_name = name
        manager.save_config(cfg)
        running = any(str(r.get("account_id")) == aid and r.get("running")
                      for r in manager.scan())
        restarted, err = (False, "")
        if running and b.get("restart", True):
            restarted, err = _restart_account(aid)
        return _ok({"account_id": aid, "backend_name": name, "ws_url": cfg.ws_url,
                    "http_url": cfg.http_url, "restarted": restarted, "error": err})

    # ---------------- 写配置 ----------------
    def _write_config(comp, b: dict[str, Any]) -> dict[str, Any]:
        """写配置（config 路由与 host 路由共用）：端口/token 不给就自动生成"""
        base = get_base()
        ws_port = int(b.get("ws_port") or C.free_port(3001))
        http_port = int(b.get("http_port") or C.free_port(3000, avoid=[ws_port]))
        token = str(b.get("token") if b.get("token") is not None else C.random_token())
        qq = str(b.get("qq") or "").strip() or "default"
        if comp.key == "napcat":
            path = C.write_napcat_config(base, qq, ws_port, http_port, token)
        else:
            path = C.write_lagrange_config(base, ws_port, token, http_port=http_port,
                                           uin=int(qq) if qq.isdigit() else None)
        return {"path": str(path), "ws_url": f"ws://127.0.0.1:{ws_port}",
                "http_url": f"http://127.0.0.1:{http_port}", "access_token": token,
                "account_id": qq}

    @app.post("/api/guide/config")
    def guide_config():
        b = _body()
        comp, bad = _comp_or_400(str(b.get("key") or ""))
        if bad:
            return bad
        try:
            return _ok(_write_config(comp, b))
        except OSError as e:
            return _err(f"写配置失败: {e}", 500)

    # ---------------- 起停 / 二维码 / 日志 ----------------
    @app.post("/api/guide/start")
    def guide_start():
        b = _body()
        comp, bad = _comp_or_400(str(b.get("key") or ""))
        if bad:
            return bad
        ok, msg = C.host_start(comp, get_base())
        return _ok({"ok": ok, "msg": msg}) if ok else _err(msg, 409)

    @app.post("/api/guide/stop")
    def guide_stop():
        b = _body()
        comp, bad = _comp_or_400(str(b.get("key") or ""))
        if bad:
            return bad
        ok, msg = C.host_stop(comp, get_base())
        return _ok({"ok": ok, "msg": msg})

    @app.get("/api/guide/qr")
    def guide_qr():
        from flask import request
        key = str(request.args.get("key") or "lagrange")
        comp, bad = _comp_or_400(key)
        if bad:
            return bad
        q = C.qr_code(get_base(), comp.key)
        if q.get("kind") == "image" and request.args.get("raw") == "1":
            from flask import send_file
            return send_file(q["path"])
        return _ok(q)

    @app.get("/api/guide/log")
    def guide_log():
        from flask import request
        key = str(request.args.get("key") or "lagrange")
        comp, bad = _comp_or_400(key)
        if bad:
            return bad
        limit = int(request.args.get("limit") or 60)
        return _ok({"lines": C.tail_log(get_base(), comp.key, limit=limit),
                    "path": str(C.host_log_path(get_base(), comp.key))})

    @app.post("/api/guide/host")
    def guide_host():
        """一步到位：写配置（可选）+ 拉起组件（引导页主按钮用）"""
        b = _body()
        comp, bad = _comp_or_400(str(b.get("key") or ""))
        if bad:
            return bad
        base = get_base()
        cfg_res = None
        if b.get("write_config", True):
            try:
                cfg_res = _write_config(comp, b)
            except OSError as e:
                return _err(f"写配置失败: {e}", 500)
        ok, msg = C.host_start(comp, base)
        if not ok:
            return _err(msg, 409)
        return _ok({"ok": True, "msg": msg, "config": cfg_res,
                    "status": C.host_status(comp, base),
                    "qr": C.qr_code(base, comp.key)})


GUIDE_HTML = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>栖星 AsterCore · 连接引导</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
 body{font:14px/1.6 system-ui,"Microsoft YaHei",sans-serif;background:#0f1115;color:#e6e6e6;margin:0;padding:24px}
 h1{font-size:20px;margin:0 0 4px} .sub{color:#9aa4b2;margin-bottom:16px}
 .card{background:#171a21;border:1px solid #262b36;border-radius:10px;padding:14px 16px;margin:12px 0}
 .row{display:flex;gap:10px;flex-wrap:wrap;align-items:center}
 button{background:#2b6cff;color:#fff;border:0;border-radius:8px;padding:8px 14px;cursor:pointer;font-size:13px}
 button.ghost{background:#2a2f3a} button:disabled{opacity:.5;cursor:not-allowed}
 input,select{background:#0f1115;color:#e6e6e6;border:1px solid #2f3644;border-radius:8px;padding:7px 10px;width:150px}
 code,pre{background:#0b0d12;border:1px solid #232936;border-radius:8px;padding:8px;display:block;
   white-space:pre-wrap;word-break:break-all;color:#b9c6d8;font-size:12px}
 .ok{color:#4ade80} .bad{color:#f87171} .warn{color:#fbbf24} .muted{color:#8b95a5}
 table{border-collapse:collapse;width:100%} td,th{border-bottom:1px solid #232936;padding:6px 4px;text-align:left;font-size:13px}
</style></head><body>
<h1>栖星 AsterCore · 连接引导</h1>
<div class="sub">按顺序走完就能用：① 选方式 → ② 装组件 → ③ 写配置 → ④ 启动扫码 → ⑤ 核对连接</div>

<div class="card">
  <b>① 运行方式</b>
  <div class="muted" style="margin:6px 0">两种都随时可改（改完重启账号生效）。这一步以前是控制台向导在问，
    现在挪到界面里 —— 窗口模式本来就没有控制台可问。</div>
  <div id="modeBox" class="muted">加载中…</div>
</div>

<div class="card">
  <b>② 组件</b>
  <table id="compTable"><tr><th>组件</th><th>状态</th><th>操作</th></tr></table>
  <div id="jobBox" class="muted" style="margin-top:8px"></div>
</div>

<div class="card">
  <b>③ 写配置（自动端口 + 自动 token，只改我们负责的键）</b>
  <div class="row" style="margin-top:8px">
    <label>QQ（NapCat 用）<input id="qq" placeholder="比如 123456789"></label>
    <label>WS 端口 <input id="ws" placeholder="自动"></label>
    <label>HTTP 端口 <input id="http" placeholder="自动"></label>
    <button onclick="writeCfg('napcat')">写 NapCat 配置</button>
    <button onclick="writeCfg('lagrange')">写 Lagrange 配置</button>
  </div>
  <pre id="cfgOut" class="muted">尚未写配置</pre>
</div>

<div class="card">
  <b>④ 启动 / 扫码 / 日志</b>
  <div class="row" style="margin-top:8px">
    <button onclick="host('napcat')">启动 NapCat</button>
    <button onclick="host('lagrange')">启动 Lagrange（内置协议）</button>
    <button class="ghost" onclick="stopHost('napcat')">停止 NapCat</button>
    <button class="ghost" onclick="stopHost('lagrange')">停止 Lagrange</button>
  </div>
  <div id="qrBox" style="margin-top:10px"></div>
  <pre id="logOut" class="muted">（没启动时没有日志）</pre>
</div>

<div class="card">
  <b>⑤ 测试连接（已有/远程实例走这里）</b>
  <div class="row" style="margin-top:8px">
    <input id="thttp" value="http://127.0.0.1:3000">
    <input id="ttoken" placeholder="access_token（可空）">
    <button onclick="testConn()">测试连接</button>
    <button class="ghost" onclick="discover()">自动发现本机</button>
  </div>
  <pre id="testOut" class="muted">—</pre>
</div>

<div class="card">
  <b>⑥ 账号（连接方式可逐个账号单独设）</b>
  <div class="row" style="margin-top:8px">
    <input id="newQq" placeholder="QQ 号（账号ID）">
    <input id="newName" placeholder="显示名（可空）">
    <select id="newBackend"></select>
    <button onclick="createAccount()">一键建好并启动</button>
    <span id="acctMsg" class="muted"></span>
  </div>
  <table id="acctTable" style="margin-top:10px">
    <tr><th>账号</th><th>连接方式</th><th>状态</th><th>操作</th></tr>
  </table>
  <div class="muted" style="margin-top:6px">改连接方式会把组件配置（端口/token）对齐到该账号；
    账号正在运行时会自动重启。NapCat 需要组件已安装且本机已装 QQ。</div>
</div>

<script>
const $ = (id) => document.getElementById(id);

let BACKENDS = [];
async function loadAccounts() {
  const j = await api('/api/guide/accounts');
  if (!j.ok) { $('acctTable').innerHTML = '<tr><td class="bad">' + esc(j.error) + '</td></tr>'; return; }
  BACKENDS = j.data.backends || [];
  const sel = $('newBackend');
  if (sel && !sel.options.length) sel.innerHTML = BACKENDS.map(b => `<option value="${b.key}">${esc(b.label)}</option>`).join('');
  const rows = ['<tr><th>账号</th><th>连接方式</th><th>状态</th><th>操作</th></tr>'];
  for (const a of (j.data.accounts || [])) {
    const opts = BACKENDS.map(b => `<option value="${b.key}" ${b.key === a.backend_name ? 'selected' : ''}>${esc(b.label)}</option>`).join('');
    const warn = (a.component && a.component_installed === false) ? ' <span class="warn">组件未装</span>' : '';
    rows.push(`<tr>
      <td>${esc(a.display_name)}<div class="muted">${esc(a.account_id)}${warn}</div></td>
      <td><select onchange="setBackend('${a.account_id}', this.value)">${opts}</select></td>
      <td>${a.running ? '<span class="ok">运行中</span>' : '<span class="muted">已停止</span>'}
          <div class="muted">${esc(a.ws_url || '')}</div></td>
      <td><button class="ghost" onclick="acctAct('${a.account_id}','start')">启动</button>
          <button class="ghost" onclick="acctAct('${a.account_id}','stop')">停止</button></td></tr>`);
  }
  $('acctTable').innerHTML = rows.join('');
}
async function createAccount() {
  const j = await api('/api/guide/account', {
    account_id: $('newQq').value.trim(),
    display_name: $('newName').value.trim(),
    backend_name: $('newBackend').value});
  if (!j.ok) { $('acctMsg').innerHTML = '<span class="bad">' + esc(j.error) + '</span>'; loadAccounts(); return; }
  $('acctMsg').innerHTML = `<span class="ok">已建 ${esc(j.data.account_id)}（${esc(j.data.backend_name)}）`
    + (j.data.started ? ' 并已启动' : '')
    + (j.data.start_error ? ` <span class="warn">启动告警：${esc(j.data.start_error)}</span>` : '')
    + '</span>';
  loadAccounts(); refresh();
}
async function setBackend(aid, name) {
  const j = await api(`/api/guide/account/${aid}/backend`, {backend_name: name});
  $('acctMsg').innerHTML = j.ok
    ? `<span class="ok">${esc(aid)} → ${esc(name)}${j.data.restarted ? '（已重启）' : ''}</span>`
      + (j.data.error ? ` <span class="warn">${esc(j.data.error)}</span>` : '')
    : '<span class="bad">' + esc(j.error) + '</span>';
  loadAccounts();
}
async function acctAct(aid, act) {
  const j = await api(`/api/accounts/${aid}/${act}`, {});
  $('acctMsg').innerHTML = j.ok ? `<span class="ok">${esc(aid)} ${act} 完成</span>`
                                : '<span class="bad">' + esc(j.error || '失败') + '</span>';
  loadAccounts();
}

async function api(path, body, method) {
  const r = await fetch(path, {method: method || (body ? 'POST' : 'GET'),
    headers: {'Content-Type': 'application/json'}, body: body ? JSON.stringify(body) : undefined});
  let j = {}; try { j = await r.json(); } catch (e) { j = {ok: false, error: 'HTTP ' + r.status}; }
  return j;
}
const esc = (s) => String(s == null ? '' : s).replace(/[&<>]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
async function refresh() {
  const j = await api('/api/guide/status');
  if (!j.ok) { $('compTable').innerHTML = '<tr><td class="bad">' + esc(j.error) + '</td></tr>'; return; }
  const d = j.data, rows = ['<tr><th>组件</th><th>状态</th><th>操作</th></tr>'];
  for (const [k, c] of Object.entries(d.components)) {
    const st = c.installed ? '<span class="ok">已安装</span>' : '<span class="warn">未安装</span>';
    const run = c.running ? '<span class="ok">运行中</span>' : '<span class="muted">未运行</span>';
    rows.push(`<tr><td>${esc(c.label)}<div class="muted">${esc(c.note || '')}</div></td>
      <td>${st} / ${run}<div class="muted">${esc(c.executable || '')}</div></td>
      <td><button onclick="install('${k}')">下载并安装</button></td></tr>`);
  }
  $('compTable').innerHTML = rows.join('');
  const jobs = Object.entries(d.jobs || {});
  $('jobBox').textContent = jobs.length ? jobs.map(([k, j]) =>
    `${k}: ${j.step} ${j.total ? '(' + Math.round(j.got / j.total * 100) + '%)' : ''} ${j.msg || ''}`).join('\\n') : '';
}
async function showMode() {
  const j = await api('/api/guide/mode');
  if (!j.ok) { $('modeBox').innerHTML = '<span class="bad">' + esc(j.error) + '</span>'; return; }
  const d = j.data;
  const rows = (d.modes || []).map(m => `
    <label style="display:block;margin:6px 0;cursor:pointer">
      <input type="radio" name="mode" value="${m.key}" ${m.key === d.mode ? 'checked' : ''} style="width:auto">
      <b>${esc(m.label)}</b> <span class="muted">风险 ${m.risk}/5${m.needs_ack ? ' · 需确认风险' : ''}</span>
      <div class="muted" style="margin-left:22px">${esc(m.desc)}</div>
    </label>`).join('');
  $('modeBox').innerHTML = rows + `
    <div class="row" style="margin-top:8px">
      <label class="muted"><input type="checkbox" id="ack" style="width:auto"> 我已知晓风险（选「内置协议直登」必须勾）</label>
      <button onclick="saveMode()">保存运行方式</button>
      <span id="modeMsg" class="muted"></span>
    </div>`;
}
async function saveMode() {
  const sel = document.querySelector('input[name=mode]:checked');
  if (!sel) return;
  const j = await api('/api/guide/mode',
    {mode: sel.value, ack_risk: ($('ack') && $('ack').checked) || false});
  $('modeMsg').innerHTML = j.ok
    ? '<span class="ok">已保存：' + esc(j.data.mode) + '</span>'
    : '<span class="bad">' + esc(j.error) + '</span>';
  refresh();
}
async function install(key) {
  const j = await api('/api/guide/install', {key});
  $('jobBox').textContent = j.ok ? '已开始下载安装：' + key + '（大包 30~120MB，请等进度）' : '失败：' + j.error;
  refresh();
}
async function writeCfg(key) {
  const body = {key, qq: $('qq').value.trim(), ws_port: $('ws').value.trim() || undefined,
                http_port: $('http').value.trim() || undefined};
  const j = await api('/api/guide/config', body);
  $('cfgOut').textContent = j.ok ? JSON.stringify(j.data, null, 2) : '失败：' + j.error;
}
async function host(key) {
  const j = await api('/api/guide/host', {key});
  $('logOut').textContent = j.ok ? (j.data.msg + '\\n' + JSON.stringify(j.data.status, null, 2)) : '失败：' + j.error;
  loadQr(key); refresh();
}
async function stopHost(key) { const j = await api('/api/guide/stop', {key}); $('logOut').textContent = j.ok ? j.data.msg : '失败：' + j.error; refresh(); }
async function loadQr(key) {
  const j = await api('/api/guide/qr?key=' + key);
  if (!j.ok) { $('qrBox').innerHTML = ''; return; }
  const q = j.data;
  if (q.kind === 'image') $('qrBox').innerHTML = `<img src="/api/guide/qr?key=${key}&raw=1&t=${Date.now()}" style="max-width:220px;border-radius:8px">`;
  else if (q.kind === 'text') $('qrBox').innerHTML = '<pre>' + esc(q.text) + '</pre>';
  else $('qrBox').innerHTML = '<span class="muted">暂无二维码（还没登录/进程没起）</span>';
  const l = await api('/api/guide/log?key=' + key);
  if (l.ok) $('logOut').textContent = (l.data.lines || []).slice(-25).join('\\n') || '（暂无日志）';
}
async function testConn() {
  const j = await api('/api/guide/test', {http_url: $('thttp').value.trim(), token: $('ttoken').value.trim()});
  $('testOut').textContent = j.ok ? (j.data.ok ? '✅ ' + j.data.msg : '❌ ' + j.data.msg) + '\\n' + JSON.stringify(j.data.data || {}, null, 2) : '失败：' + j.error;
}
async function discover() {
  const j = await api('/api/guide/discover', {});
  $('testOut').textContent = j.ok ? JSON.stringify(j.data.found, null, 2) : ('失败：' + j.error);
}
refresh(); showMode(); loadAccounts();
setInterval(refresh, 3000);
</script></body></html>
"""
