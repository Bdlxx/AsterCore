# 栖星 AsterCore · 首启引导页 + 设置页（规格 §三）
#
# 为什么单独成页：这两页是**桌面首次运行**的入口，而共用面板资产（web/static/*）双端必须
# 字节一致，不能塞桌面专属的引导/设置进去。所以这里自带两个单文件页面：
#   /setup     首启：三选一（NapCat 推荐 / 内部协议有风险 / 稍后）＋ 左下角「设置」
#   /settings  设置卡片：安全设置（鉴权/密码）、页面模式与端口
#
# 复用的既有能力（不重复造）：
#   AppState.set_backend() / set_transport() / set_web_config()；web.auth.Auth
#
# 模式词汇按用户规格存 `state.data["setup_mode"]`（napcat / internal / later），
# 底层映射：napcat→onebot 后端、internal→lagrange 后端、later→先不建后端。

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable

log = logging.getLogger("astercore.web.setup")

DEFAULT_PORT = 34567          # 规格：默认用一个不常用端口

# 规格里的"使用方式"→ 底层运行方式
MODE_TO_BACKEND = {"napcat": "napcat", "internal": "lagrange", "later": "later"}
MODE_INFO = {
    "napcat": {"label": "NapCat（推荐）", "risk": 5,
               "desc": "后台运行官方 QQ 客户端 + NapCat 中间层，走官方登录通道，风控风险最低。"},
    "internal": {"label": "内部协议（不推荐，有风险）", "risk": 2,
                 "desc": "纯协议直登，免装客户端、扫码即用；但属非官方协议，存在限制登录/封号风险，"
                         "建议只用小号。选择前必须勾选已知晓风险。"},
    "later": {"label": "稍后选择（测试保留）", "risk": 5,
              "desc": "先不决定。主界面会显示「未选择使用方式」，随时可在设置里改。"},
}


def _ok(data: Any = None):
    from flask import jsonify
    return jsonify({"ok": True, "data": data})


def _err(msg: str, code: int = 400):
    from flask import jsonify
    return jsonify({"ok": False, "error": msg}), code


def add_setup_routes(app, state, auth=None) -> None:
    """把 /setup、/settings 与它们的接口挂上（state 必须给；auth 可不给）"""
    if state is None:
        return

    def _mode() -> str:
        return str(state.data.get("setup_mode") or "")

    def _inited() -> bool:
        return (not state.needs_wizard()) and bool(_mode())

    def _page_mode() -> str:
        t = state.transport_mode()
        return "window" if t in ("webview", "auto") else "port"

    def _port() -> int:
        try:
            return int(state.web_config().get("port") or DEFAULT_PORT)
        except (TypeError, ValueError):
            return DEFAULT_PORT

    # ---------------- 页面 ----------------
    @app.get("/setup")
    def setup_page():
        from flask import Response
        return Response(SETUP_HTML, mimetype="text/html; charset=utf-8")

    @app.get("/settings")
    def settings_page():
        from flask import Response
        return Response(SETTINGS_HTML, mimetype="text/html; charset=utf-8")

    # ---------------- 状态 ----------------
    @app.get("/api/setup/state")
    def setup_state():
        return _ok({
            "inited": _inited(),
            "mode": _mode(),
            "modes": [{"key": k, **v} for k, v in MODE_INFO.items()],
            "backend_mode": state.backend_mode,
            "page_mode": _page_mode(),
            "port": _port(),
            "auth_mode": (auth.mode if auth is not None else "none"),
            "has_password": bool(auth is not None and auth.data.get("password_hash")),
        })

    # ---------------- 三选一 ----------------
    @app.post("/api/setup/mode")
    def setup_set_mode():
        from flask import request
        d = request.get_json(silent=True) or {}
        mode = str(d.get("mode") or "").strip()
        if mode not in MODE_INFO:
            return _err(f"未知使用方式: {mode or '(空)'}")
        if MODE_INFO[mode]["needs_ack"] if "needs_ack" in MODE_INFO[mode] else mode == "internal":
            if not bool(d.get("ack_risk")):
                return _err("内部协议为非官方通道，请先勾选「我已知晓风险」")
        if mode == "later":
            # 只是"还没决定"：标记引导完成，不动后端
            state.wizard_completed = True
            state.save()
        else:
            ok, msg = state.set_backend(MODE_TO_BACKEND[mode], ack_risk=(mode == "internal"))
            if not ok:
                return _err(msg)
        state.data["setup_mode"] = mode
        state.save()
        return _ok({"mode": mode, "inited": _inited()})

    # ---------------- 安全设置 ----------------
    @app.post("/api/setup/auth")
    def setup_set_auth():
        from flask import request
        d = request.get_json(silent=True) or {}
        if auth is None:
            return _err("当前环境没有鉴权组件", 501)
        action = str(d.get("action") or "set_mode")
        if action == "set_mode":
            mode = str(d.get("mode") or "").strip()
            if mode not in ("none", "password"):
                return _err("鉴权方式只能是 无鉴权 / 密码鉴权")
            if mode == "password":
                pw = str(d.get("password") or "")
                pw2 = str(d.get("password2") or "")
                if not pw:
                    return _err("密码不能为空")
                if pw != pw2:
                    return _err("两次输入的密码不一致")
                if len(pw) < 4:
                    return _err("密码至少 4 位")
                auth.set_password(pw)
            auth.set_mode(mode)
            auth.save()
            return _ok({"auth_mode": auth.mode})
        if action == "change_password":
            old = str(d.get("old_password") or "")
            pw = str(d.get("new_password") or "")
            pw2 = str(d.get("new_password2") or "")
            if not auth.password_ok(old):
                return _err("原密码不正确")
            if not pw:
                return _err("新密码不能为空")
            if pw != pw2:
                return _err("两次输入的新密码不一致")
            if len(pw) < 4:
                return _err("新密码至少 4 位")
            auth.set_password(pw)
            auth.save()
            return _ok({"changed": True})
        return _err(f"未知操作: {action}")

    # ---------------- 页面模式与端口 ----------------
    @app.post("/api/setup/page")
    def setup_set_page():
        from flask import request
        d = request.get_json(silent=True) or {}
        mode = str(d.get("page_mode") or "").strip()
        if mode not in ("window", "port"):
            return _err("页面模式只能是 窗口模式 / 端口模式")
        ok, msg = state.set_transport("webview" if mode == "window" else "http")
        if not ok:
            return _err(msg)
        if mode == "port":
            raw = d.get("port")
            # 只有"没填"才用默认端口；0/-1/70000 这类都是非法值（规格：1-65535）
            port = DEFAULT_PORT if raw is None or str(raw).strip() == "" else raw
            try:
                port = int(port)
            except (TypeError, ValueError):
                return _err("端口必须是数字")
            if not (1 <= port <= 65535):
                return _err("端口范围 1-65535")
            auth_mode = auth.mode if auth is not None else "none"
            ok, msg, cfg = state.set_web_config(
                {"enabled": True, "host": "127.0.0.1", "port": port}, auth_mode=auth_mode)
            if not ok:
                return _err(msg)
            return _ok({"page_mode": mode, "port": cfg["port"], "restart_needed": True})
        return _ok({"page_mode": mode, "port": _port(), "restart_needed": True})


# --------------------------------------------------------------------------- #
# 页面
# --------------------------------------------------------------------------- #
_BASE_CSS = """
 body{font:14px/1.6 system-ui,"Microsoft YaHei",sans-serif;background:#f5f6fa;color:#1e2230;margin:0;padding:32px}
 h1{font-size:22px;margin:0 0 6px} .sub{color:#6b7280;margin-bottom:20px}
 .card{background:#fff;border:1px solid #e5e7eb;border-radius:12px;padding:18px 20px;margin:14px 0;box-shadow:0 1px 2px rgba(0,0,0,.03)}
 .opt{display:block;border:1px solid #e5e7eb;border-radius:10px;padding:14px 16px;margin:10px 0;cursor:pointer}
 .opt:hover{border-color:#2b6cff;background:#f8faff}
 .opt b{font-size:15px} .risk{color:#d97706} .muted{color:#6b7280}
 button{background:#2b6cff;color:#fff;border:0;border-radius:8px;padding:9px 16px;cursor:pointer;font-size:14px}
 button.ghost{background:#eef1f6;color:#1e2230} button:disabled{opacity:.5;cursor:not-allowed}
 input{background:#fff;border:1px solid #d7dbe3;border-radius:8px;padding:8px 10px;width:180px}
 .ok{color:#059669} .bad{color:#dc2626} .row{display:flex;gap:10px;flex-wrap:wrap;align-items:center}
 #settingsBtn{position:fixed;left:18px;bottom:18px;z-index:9}
 .mask{position:fixed;inset:0;background:rgba(15,17,21,.45);display:none;align-items:center;justify-content:center}
 .mask.show{display:flex}
 .dlg{background:#fff;border-radius:12px;padding:20px;min-width:320px}
 .dlg h3{margin:0 0 12px;font-size:16px} .dlg .row{margin:8px 0} .dlg input{width:100%}
"""

SETUP_HTML = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<title>栖星 AsterCore · 首次设置</title><style>%s</style></head><body>
<h1>欢迎使用栖星 AsterCore</h1>
<div class="sub">先选一个使用方式，之后随时可以在「设置」里改。</div>
<div id="opts"></div>
<div class="muted" id="msg" style="margin-top:10px"></div>
<button id="settingsBtn" class="ghost" onclick="location.href='/settings'">设置</button>
<script>
const $=(id)=>document.getElementById(id);
async function api(p,b){const r=await fetch(p,{method:b?'POST':'GET',headers:{'Content-Type':'application/json'},body:b?JSON.stringify(b):undefined});try{return await r.json()}catch(e){return {ok:false,error:'HTTP '+r.status}}}
const esc=(s)=>String(s==null?'':s).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
async function load(){
  const j=await api('/api/setup/state');
  if(!j.ok){$('msg').innerHTML='<span class="bad">'+esc(j.error)+'</span>';return}
  const d=j.data;
  $('opts').innerHTML=(d.modes||[]).map(m=>`
    <label class="opt">
      <input type="radio" name="mode" value="${m.key}" ${m.key===d.mode?'checked':''} style="width:auto">
      <b>${esc(m.label)}</b> <span class="muted">安全评级 ${m.risk}/5</span>
      ${m.key==='internal'?'<span class="risk">（非官方协议，谨慎）</span>':''}
      <div class="muted" style="margin-left:22px">${esc(m.desc)}</div>
    </label>`).join('') + `
    <div class="row" style="margin-top:8px">
      <label class="muted"><input type="checkbox" id="ack" style="width:auto"> 我已知晓风险（选「内部协议」必须勾）</label>
      <button onclick="save()">就用这个，进入主界面</button>
      <button class="ghost" onclick="location.href='/settings'">先去设置</button>
    </div>`;
}
async function save(){
  const sel=document.querySelector('input[name=mode]:checked'); if(!sel)return;
  const j=await api('/api/setup/mode',{mode:sel.value,ack_risk:($('ack')&&$('ack').checked)||false});
  if(!j.ok){$('msg').innerHTML='<span class="bad">'+esc(j.error)+'</span>';return}
  $('msg').innerHTML='<span class="ok">已保存，正在进入主界面…</span>';
  setTimeout(()=>{location.href='/'},600);
}
load();
</script></body></html>
""" % _BASE_CSS

SETTINGS_HTML = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<title>栖星 AsterCore · 设置</title><style>%s</style></head><body>
<h1>设置</h1><div class="sub">改动即时保存；页面模式/端口改动需要重启程序生效。</div>

<div class="card">
  <h3 style="margin:0 0 8px">安全设置</h3>
  <div class="row">
    <label><input type="radio" name="auth" value="none" style="width:auto"> 无鉴权</label>
    <label><input type="radio" name="auth" value="password" style="width:auto"> 密码鉴权</label>
    <button onclick="pickAuth()">保存鉴权方式</button>
  </div>
  <div class="row" style="margin-top:10px">
    <button class="ghost" onclick="showDlg('setpw')">设置密码</button>
    <button class="ghost" onclick="showDlg('chpw')">修改密码</button>
    <span id="authMsg" class="muted"></span>
  </div>
</div>

<div class="card">
  <h3 style="margin:0 0 8px">页面模式与端口</h3>
  <div class="row">
    <label><input type="radio" name="page" value="window" style="width:auto"> 窗口模式（内嵌窗口）</label>
    <label><input type="radio" name="page" value="port" style="width:auto"> 端口模式（浏览器访问）</label>
  </div>
  <div class="row" style="margin-top:10px" id="portRow">
    <span>端口</span><input id="port" placeholder="34567">
    <button onclick="savePage()">保存页面设置</button>
    <span id="pageMsg" class="muted"></span>
  </div>
  <div class="muted">端口范围 1-65535，默认 34567（避开常用端口）。</div>
</div>

<div class="mask" id="mask"><div class="dlg" id="dlg"></div></div>
<script>
const $=(id)=>document.getElementById(id);
async function api(p,b){const r=await fetch(p,{method:b?'POST':'GET',headers:{'Content-Type':'application/json'},body:b?JSON.stringify(b):undefined});try{return await r.json()}catch(e){return {ok:false,error:'HTTP '+r.status}}}
const esc=(s)=>String(s==null?'':s).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
function showDlg(kind){
  const isSet=kind==='setpw';
  $('dlg').innerHTML=`<h3>${isSet?'设置密码':'修改密码'}</h3>
    ${isSet?'':'<div class="row"><input id="old" type="password" placeholder="原密码"></div>'}
    <div class="row"><input id="pw" type="password" placeholder="新密码"></div>
    <div class="row"><input id="pw2" type="password" placeholder="再次输入新密码"></div>
    <div class="row" style="justify-content:flex-end">
      <span id="dlgMsg" class="bad"></span>
      <button class="ghost" onclick="hideDlg()">取消</button>
      <button onclick="submitDlg('${kind}')">确认</button>
    </div>`;
  $('mask').classList.add('show');
}
function hideDlg(){ $('mask').classList.remove('show'); }
async function submitDlg(kind){
  const isSet=kind==='setpw';
  const body={action:isSet?'set_mode':'change_password'};
  if(isSet){ body.mode='password'; }
  else { body.old_password=$('old').value; body.new_password=$('pw').value; body.new_password2=$('pw2').value; }
  if(isSet){ body.password=$('pw').value; body.password2=$('pw2').value; }
  const j=await api('/api/setup/auth',body);
  if(!j.ok){ $('dlgMsg').textContent=j.error; return; }   // 不关弹窗，让用户改
  hideDlg(); $('authMsg').innerHTML='<span class="ok">已保存</span>'; load();
}
async function pickAuth(){
  const sel=document.querySelector('input[name=auth]:checked').value;
  if(sel==='password'){ showDlg('setpw'); return; }
  const j=await api('/api/setup/auth',{action:'set_mode',mode:'none'});
  $('authMsg').innerHTML=j.ok?'<span class="ok">已切换为无鉴权</span>':'<span class="bad">'+esc(j.error)+'</span>';
  load();
}
async function savePage(){
  const mode=document.querySelector('input[name=page]:checked').value;
  const j=await api('/api/setup/page',{page_mode:mode,port:$('port').value.trim()});
  $('pageMsg').innerHTML=j.ok
    ? '<span class="ok">已保存'+(j.data.restart_needed?'（重启程序后生效）':'')+'</span>'
    : '<span class="bad">'+esc(j.error)+'</span>';
  load();
}
async function load(){
  const j=await api('/api/setup/state'); if(!j.ok)return; const d=j.data;
  document.querySelectorAll('input[name=auth]').forEach(r=>r.checked=(r.value===d.auth_mode));
  document.querySelectorAll('input[name=page]').forEach(r=>r.checked=(r.value===d.page_mode));
  $('port').value=d.port||34567;
  $('portRow').style.display=(d.page_mode==='port')?'flex':'none';
  document.querySelectorAll('input[name=page]').forEach(r=>r.onchange=()=>{
    $('portRow').style.display=(document.querySelector('input[name=page]:checked').value==='port')?'flex':'none';});
}
load();
</script></body></html>
""" % _BASE_CSS
