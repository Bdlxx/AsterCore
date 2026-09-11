# 栖星 AsterCore · `utils` 兼容垫片
# 老插件写的是 `from utils.api import send_message` 这类导入。这里在 sys.modules
# 里注入一个 `utils` 包，把老接口映射到 AsterCore 的账号运行时上：
#   utils.api            → 发送/撤回/合并转发/HTTP 查询（老插件唯一的出口）
#   utils.config         → 账号 config.json + backend.json 派生的配置读取
#   utils.log            → NapCat 风格分级日志（进内核环形日志，面板可见）
#   utils.ws             → ws.ws.send(json) 兼容层
#   utils.plugin_toggle  → 分群插件开关（data/plugin_toggle.json，默认关闭）
#   utils.command_registry → 指令注册表（老插件用它组织指令）
# 账号定位见 context.py；调用时解析，所以多账号互不串台。

from __future__ import annotations

import asyncio
import json
import logging
import sys
import threading
import time
import types
from pathlib import Path
from typing import Any

from astercore.compat import context
from astercore.compat.onebot import (normalize_message, segments_to_onebot_list)

log = logging.getLogger("astercore.compat.utils")

_installed = False
_lock = threading.Lock()

# 插件元数据注册表（面板显示 + 分群开关键名）。
# ⚠️ 键名必须与线上 Linux 版 `plugin_toggle` 的键完全一致，否则分群开关会
#    静默失效（例如伪人插件的文件叫 pseudo_persona.py，但开关键是 `pseudo`）。
PLUGIN_META_DEFAULT: dict[str, dict[str, str]] = {
    "wordlib": {"name_cn": "词库插件", "config_file": "wordlib_config.json",
                "description": "关键词匹配回复、签到好感度、自定义昵称"},
    "marry": {"name_cn": "结婚插件", "config_file": "marry_config.json",
              "description": "群内娶/嫁群友、离婚冷却、查看对象"},
    "pseudo": {"name_cn": "伪人插件", "config_file": "persona_config.json",
               "description": "群聊 AI 拟人回复（GLM/Gemini 双模型）",
               "name_en": "pseudo_persona"},
    "jm_downloader": {"name_cn": "JM下载", "config_file": "jm_downloader_config.json",
                      "description": "JM 漫画检索与打包下载（子进程 + PDF）"},
    "video_parser": {"name_cn": "视频解析", "config_file": "video_parser_config.json",
                     "description": "16 平台视频/图文解析、B站扫码登录"},
}


# ---------------------------------------------------------------- 运行时解析

def _rt(event: Any = None):
    rt = context.resolve(event)
    if rt is None:
        log.debug("兼容调用时没有可用账号上下文（事件=%s）", type(event).__name__)
    return rt


def _instance_dir(rt) -> Path | None:
    d = getattr(rt, "instance_dir", None)
    return Path(d) if d else None


def _data_dir(rt) -> Path | None:
    d = _instance_dir(rt)
    if d is None:
        dd = getattr(rt, "data_dir", None)
        return Path(dd) if dd else None
    p = d / "data"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _config_dict(rt) -> dict[str, Any]:
    """老配置：<instance>/config.json；缺项用账号连接信息补"""
    cfg: dict[str, Any] = {}
    d = _instance_dir(rt)
    if d is not None:
        p = d / "config.json"
        if p.exists():
            try:
                cfg = json.loads(p.read_text(encoding="utf-8")) or {}
            except Exception as e:
                log.warning("读取 config.json 失败: %s", e)
    backend = getattr(rt, "backend", None)
    bcfg = getattr(backend, "cfg", None)
    account_id = getattr(rt, "account_id", None)
    cfg.setdefault("BOT_QQ", int(account_id) if str(account_id).isdigit() else account_id)
    cfg.setdefault("BOT_NAME", str(getattr(rt, "display_name", "") or account_id or "Bot"))
    if bcfg is not None:
        cfg.setdefault("NAPCAT_HTTP", getattr(bcfg, "http_url", "") or "")
        cfg.setdefault("ACCESS_TOKEN", getattr(bcfg, "access_token", "") or "")
        cfg.setdefault("WS_URL", getattr(bcfg, "ws_url", "") or "")
    cfg.setdefault("MASTER_QQ", [])
    return cfg


def _run_action(rt, action: str, params: dict[str, Any], timeout: float = 20.0):
    """在账号事件循环上执行动作（老插件是同步代码，这里等待结果）"""
    if rt is None:
        log.warning("无账号上下文，动作 %s 丢弃", action)
        return None
    coro = rt.action(action, params)
    loop = getattr(rt, "loop", None)
    if loop is None or not loop.is_running():
        try:
            return asyncio.run(coro)
        except Exception as e:
            log.warning("动作 %s 执行失败: %s", action, e)
            return None
    try:
        asyncio.get_running_loop()
        in_loop = True
    except RuntimeError:
        in_loop = False
    if in_loop:
        # 已在事件循环线程里：不能阻塞等待，改为投递任务
        loop.create_task(coro)
        return None
    try:
        fut = asyncio.run_coroutine_threadsafe(coro, loop)
        return fut.result(timeout=timeout)
    except Exception as e:
        log.warning("动作 %s 执行失败: %s", action, e)
        return None


def _result_dict(res: Any, action: str) -> dict[str, Any]:
    """把 ActionResult 转成老插件期望的 NapCat 响应 dict"""
    ok = bool(getattr(res, "ok", False))
    data = getattr(res, "data", None)
    return {
        "status": "ok" if ok else "failed",
        "retcode": 0 if ok else -1,
        "data": data if data is not None else {"message_id": None},
        "message": getattr(res, "error", "") or "",
        "echo": getattr(res, "echo", f"compat_{action}"),
    }


# ---------------------------------------------------------------- utils.api

def _install_api(pkg: types.ModuleType) -> types.ModuleType:
    m = types.ModuleType("utils.api")

    def _target(event: dict) -> tuple[str, dict] | None:
        mt = (event or {}).get("message_type")
        if mt == "private" and event.get("user_id") is not None:
            return "send_private", {"user_id": event["user_id"]}
        if mt == "group" and event.get("group_id") is not None:
            return "send_group", {"group_id": event["group_id"]}
        return None

    def _has_forward(segs) -> bool:
        return any(s.type == "forward" for s in segs)

    def _finish(rt, segs) -> list[dict]:
        """统一出口：消息段规整 + 老插件容器路径映射（Windows 原生 NapCat）"""
        from astercore.compat import paths as _cpaths
        return _cpaths.map_segments(segments_to_onebot_list(segs), rt)

    def send_message(event, message):
        rt = _rt(event)
        tgt = _target(event or {})
        if tgt is None:
            return False
        action, params = tgt
        segs = normalize_message(message)
        if _has_forward(segs):   # 合并转发走专门动作（OneBot 里 send_msg 不支持）
            return _send_forward(rt, event, segs)
        params["message"] = _finish(rt, segs)
        res = _run_action(rt, action, params)
        return bool(getattr(res, "ok", False))

    def ws_send(event, message, on_ok=None, echo=None):
        rt = _rt(event)
        tgt = _target(event or {})
        if tgt is None:
            return False
        action, params = tgt
        segs = normalize_message(message)
        params["message"] = _finish(rt, segs)
        res = _run_action(rt, action, params)
        if on_ok is not None:
            try:
                on_ok(_result_dict(res, action))
            except Exception:
                log.exception("ws_send 回调异常")
        return bool(getattr(res, "ok", False))

    def _send_forward(rt, event, segs) -> bool:
        tgt = _target(event or {})
        if tgt is None:
            return False
        seg = next(s for s in segs if s.type == "forward")
        nodes = seg.data.get("messages") or []
        news = seg.data.get("news")
        action = "send_group_forward" if tgt[0] == "send_group" else "send_private_forward"
        params = dict(tgt[1])
        params["messages"] = nodes
        if news:
            params["news"] = news
        res = _run_action(rt, action, params)
        return bool(getattr(res, "ok", False))

    def send_forward_msg(event, nodes, news=None):
        rt = _rt(event)
        tgt = _target(event or {})
        if tgt is None:
            return
        messages = []
        for node in nodes or []:
            messages.append({"type": "node", "data": {
                "name": node.get("name", "机器人"),
                "uin": node.get("uin", ""),
                "content": node.get("content", []),
            }})
        action = "send_group_forward" if tgt[0] == "send_group" else "send_private_forward"
        params = dict(tgt[1])
        params["messages"] = messages
        if news:
            params["news"] = news
        _run_action(rt, action, params)

    def ws_delete_msg(message_id):
        rt = _rt()
        res = _run_action(rt, "recall_message", {"message_id": message_id})
        return bool(getattr(res, "ok", False))

    def http_get(action, params=None):
        rt = _rt()
        if rt is None:
            return None
        cfg = _config_dict(rt)
        base = str(cfg.get("NAPCAT_HTTP") or "").rstrip("/")
        if not base:
            return None
        q = dict(params or {})
        token = cfg.get("ACCESS_TOKEN")
        if token:
            q["access_token"] = token
        try:
            import requests
            r = requests.get(f"{base}/{action}", params=q, timeout=5)
            if r.status_code == 200:
                return r.json()
            log.warning("HTTP %s 失败: %s", action, r.status_code)
        except Exception as e:
            log.warning("HTTP %s 异常: %s", action, e)
        return None

    def handle_ws_echo(event):   # AsterCore 内部已按 echo 等待，这里无需分发
        return None

    def get_forward_nodes(message) -> list:
        """老插件工具：从消息里取合并转发节点"""
        segs = normalize_message(message)
        for s in segs:
            if s.type == "forward":
                return s.data.get("messages") or []
        return []

    m.send_message = send_message
    m.ws_send = ws_send
    m.send_forward_msg = send_forward_msg
    m.ws_delete_msg = ws_delete_msg
    m.http_get = http_get
    m.handle_ws_echo = handle_ws_echo
    m.get_forward_nodes = get_forward_nodes
    m._echo_callbacks = {}
    _bind(m, pkg, "api")
    return m


# ---------------------------------------------------------------- utils.config

def _install_config(pkg: types.ModuleType) -> types.ModuleType:
    m = types.ModuleType("utils.config")
    _overrides: dict[str, Any] = {}

    def set_cli_params(bot_name=None, bot_qq=None):
        if bot_name:
            _overrides["BOT_NAME"] = bot_name
        if bot_qq:
            _overrides["BOT_QQ"] = bot_qq

    def load_config() -> dict:
        rt = _rt()
        cfg = _config_dict(rt) if rt is not None else {}
        cfg.update(_overrides)
        return cfg

    def get_config(key, default=None):
        return load_config().get(key, default)

    def get_bot_name():
        return str(get_config("BOT_NAME") or "Bot")

    def get_master_qq():
        v = get_config("MASTER_QQ", [])
        if v is None:
            return []
        if isinstance(v, (list, tuple, set)):
            return [int(x) if str(x).isdigit() else x for x in v]
        return [int(v) if str(v).isdigit() else v]

    def get_bot_qq():
        v = get_config("BOT_QQ")
        try:
            return int(v)
        except (TypeError, ValueError):
            return v

    def get_napcat_http():
        return str(get_config("NAPCAT_HTTP") or "")

    def get_access_token():
        return str(get_config("ACCESS_TOKEN") or "")

    def get_52api_key():
        return get_config("52api_key", "")

    def get_52api_secret():
        return get_config("52api_secret", "")

    def get_ws_url():
        return str(get_config("WS_URL") or "")

    for f in (set_cli_params, load_config, get_config, get_bot_name, get_master_qq,
              get_bot_qq, get_napcat_http, get_access_token, get_52api_key,
              get_52api_secret, get_ws_url):
        setattr(m, f.__name__, f)
    _bind(m, pkg, "config")
    return m


# ---------------------------------------------------------------- utils.log

_LOG_LEVELS = {"debug": logging.DEBUG, "info": logging.INFO,
               "warn": logging.WARNING, "warning": logging.WARNING,
               "error": logging.ERROR}


def _install_log(pkg: types.ModuleType) -> types.ModuleType:
    m = types.ModuleType("utils.log")
    plog = logging.getLogger("astercore.plugin")
    _cur_plugin: list[str | None] = [None]
    _name_cache: dict[tuple, tuple[str, float]] = {}
    _TTL = 600

    def _fmt_time():
        return time.strftime("%m-%d %H:%M:%S")

    def bot_name():
        try:
            rt = _rt()
            return str(getattr(rt, "display_name", "") or "Bot")
        except Exception:
            return "Bot"

    def set_current_plugin(name):
        _cur_plugin[0] = name

    def get_current_plugin():
        return _cur_plugin[0]

    def bot_label():
        p = _cur_plugin[0]
        return f"{bot_name()}「{p}」" if p else bot_name()

    def log(level, msg):
        lv = _LOG_LEVELS.get(str(level).lower(), logging.INFO)
        plog.log(lv, "%s | %s", bot_label(), msg)

    def plugin_log(plugin, level, msg):
        lv = _LOG_LEVELS.get(str(level).lower(), logging.INFO)
        plog.log(lv, "%s「%s」| %s", bot_name(), plugin, msg)

    def info(plugin, msg):
        plugin_log(plugin, "info", msg)

    def warn(plugin, msg):
        plugin_log(plugin, "warn", msg)

    def error(plugin, msg):
        plugin_log(plugin, "error", msg)

    def debug(plugin, msg):
        plugin_log(plugin, "debug", msg)

    def summarize_message(message, max_len=60):
        """把消息段/文本压成一行摘要（日志用）"""
        from astercore.compat.onebot import segments_to_cq
        segs = normalize_message(message)
        parts = []
        for s in segs:
            if s.type == "text":
                parts.append(str(s.data.get("text", "")))
            elif s.type == "image":
                parts.append("[图片]")
            elif s.type == "at":
                parts.append(f"@{s.data.get('qq')}")
            elif s.type == "face":
                parts.append("[表情]")
            else:
                parts.append(f"[{s.type}]")
        text = " ".join(x for x in parts if x).strip()
        return text[:max_len] if len(text) > max_len else text

    def _lookup(kind: str, key) -> str:
        ck = (kind, str(key))
        now = time.time()
        hit = _name_cache.get(ck)
        if hit and now - hit[1] < _TTL:
            return hit[0]
        api = sys.modules.get("utils.api")
        name = str(key)
        if api is not None:
            try:
                if kind == "group":
                    d = api.http_get("get_group_info", {"group_id": key}) or {}
                    name = str((d.get("data") or {}).get("group_name") or key)
                else:
                    d = api.http_get("get_stranger_info", {"user_id": key}) or {}
                    name = str((d.get("data") or {}).get("nickname") or key)
            except Exception:
                name = str(key)
        _name_cache[ck] = (name, now)
        return name

    def get_group_name(gid):
        return _lookup("group", gid)

    def get_nickname(uid):
        return _lookup("user", uid)

    def log_msg_event(event, direction="接收", msg_content=None):
        """NapCat 风格的一行消息日志（老插件用得很频繁）"""
        try:
            e = event or {}
            mt = "群聊" if e.get("message_type") == "group" else "私聊"
            if e.get("message_type") == "group" and e.get("group_id"):
                where = f"[{get_group_name(e['group_id'])}({e['group_id']})]"
            else:
                where = ""
            who = ""
            if e.get("user_id"):
                who = f"[{get_nickname(e['user_id'])}({e['user_id']})]"
            body = summarize_message(msg_content) if msg_content is not None else ""
            arrow = "<-" if direction == "接收" else "->"
            plog.info("%s %s %s %s %s %s", bot_label(), direction, arrow, mt, where, who + " " + body)
        except Exception:
            plog.info("%s %s %s", bot_label(), direction, msg_content)

    for f in (log, plugin_log, info, warn, error, debug, summarize_message,
              get_group_name, get_nickname, log_msg_event, set_current_plugin,
              get_current_plugin, bot_name, bot_label, _fmt_time):
        setattr(m, f.__name__, f)
    _bind(m, pkg, "log")
    return m


# ---------------------------------------------------------------- utils.ws

class _WsProxy:
    """老插件里 `utils.ws.ws.send(json_str)` 的兼容对象"""

    def send(self, payload: str) -> bool:
        try:
            req = json.loads(payload) if isinstance(payload, str) else dict(payload)
        except Exception:
            log.warning("ws.send 收到非法 JSON")
            return False
        action = req.get("action", "")
        params = dict(req.get("params") or {})
        rt = _rt(params)
        mapped = {
            "send_group_msg": "send_group",
            "send_private_msg": "send_private",
            "delete_msg": "recall_message",
            "send_group_forward_msg": "send_group_forward",
            "send_private_forward_msg": "send_private_forward",
            "send_msg": "send_message",
        }.get(action)
        if mapped is None:
            log.debug("ws.send 暂不支持的原始动作: %s", action)
            return False
        if action in ("send_group_forward_msg", "send_private_forward_msg"):
            params["messages"] = params.get("messages") or []
        res = _run_action(rt, mapped, params)
        return bool(getattr(res, "ok", False))

    def close(self, *a, **kw):
        return None


def _install_ws(pkg: types.ModuleType) -> types.ModuleType:
    m = types.ModuleType("utils.ws")
    proxy = _WsProxy()

    class _WsModule(types.ModuleType):
        @property
        def ws(self):
            return proxy      # 恒为真：连接状态由后端管理，动作失败会记日志

    real = _WsModule("utils.ws")
    real._proxy = proxy
    _bind(real, pkg, "ws")
    return real


# ---------------------------------------------------------------- plugin_toggle

def _install_plugin_toggle(pkg: types.ModuleType) -> types.ModuleType:
    m = types.ModuleType("utils.plugin_toggle")

    def _toggle_file(rt=None) -> Path | None:
        rt = rt or _rt()
        d = _data_dir(rt) if rt is not None else None
        return (d / "plugin_toggle.json") if d else None

    def _load(data_dir=None) -> dict:
        p = Path(data_dir) / "plugin_toggle.json" if data_dir else _toggle_file()
        if p and p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8")) or {}
            except Exception:
                pass
        return {}

    def _save(data: dict, data_dir=None) -> None:
        p = Path(data_dir) / "plugin_toggle.json" if data_dir else _toggle_file()
        if p is None:
            return
        p.parent.mkdir(parents=True, exist_ok=True)
        note = data.get("_note")
        if note is None and p.exists():
            try:
                note = (json.loads(p.read_text(encoding="utf-8")) or {}).get("_note")
            except Exception:
                note = None
        if note:
            data["_note"] = note
        p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def is_enabled(group_id, plugin) -> bool:
        """默认关闭（与 Linux 版语义一致）"""
        return _load().get(str(group_id), {}).get(plugin, False) is True

    def set_enabled(group_id, plugin, enabled):
        data = _load()
        data.setdefault(str(group_id), {})[plugin] = bool(enabled)
        _save(data)

    def get_all_toggles(plugins_list=None) -> dict:
        data = _load()
        names = list(plugins_list) if plugins_list else list(PLUGIN_META_DEFAULT)
        return {str(g): {n: bool(v.get(n, False)) for n in names}
                for g, v in data.items() if isinstance(v, dict)}

    def get_toggles_matrix(data_dir=None, plugins_list=None) -> dict:
        data = _load(data_dir)
        names = list(plugins_list) if plugins_list else list(PLUGIN_META_DEFAULT)
        return {str(g): {n: bool(v.get(n, False)) for n in names}
                for g, v in data.items() if isinstance(v, dict)}

    def set_group_toggles(group_id, toggles: dict):
        data = _load()
        data[str(group_id)] = {k: bool(v) for k, v in (toggles or {}).items()}
        _save(data)

    def set_batch_toggles(toggles_data: dict, data_dir=None):
        data = _load(data_dir)
        for gid, toggles in (toggles_data or {}).items():
            data[str(gid)] = {k: bool(v) for k, v in (toggles or {}).items()}
        _save(data, data_dir)

    def get_all_groups_summary() -> list:
        return [{"group_id": g, "toggles": v} for g, v in _load().items()
                if isinstance(v, dict)]

    def _scan_plugins_dir(plugins_dir) -> dict:
        out: dict[str, dict] = {}
        p = Path(plugins_dir)
        if not p.is_dir():
            return out
        for f in sorted(p.iterdir()):
            if f.suffix in (".py", ".pyd", ".so", ".dll") and not f.name.startswith("_"):
                key = f.stem
                meta = dict(PLUGIN_META_DEFAULT.get(key) or {})
                meta.setdefault("name_cn", key)
                meta.setdefault("name_en", key)
                meta["file"] = f.name
                out[key] = meta
        return out

    def get_plugin_meta(plugin_key=None, plugins_dir=None) -> dict:
        meta = dict(PLUGIN_META_DEFAULT)
        if plugins_dir:
            meta.update(_scan_plugins_dir(plugins_dir))
        if plugin_key:
            return meta.get(plugin_key, {})
        return meta

    def scan_plugin_metadata(plugins_dir=None, plugin_key=None):
        return get_plugin_meta(plugin_key, plugins_dir)

    def get_available_plugins(plugins_dir=None) -> list:
        return list(get_plugin_meta(None, plugins_dir))

    for f in (is_enabled, set_enabled, get_all_toggles, get_toggles_matrix,
              set_group_toggles, set_batch_toggles, get_all_groups_summary,
              get_plugin_meta, scan_plugin_metadata, get_available_plugins,
              _scan_plugins_dir, _load, _save):
        setattr(m, f.__name__, f)
    m.PLUGIN_META = dict(PLUGIN_META_DEFAULT)
    m.DATA_DIR = ""          # 运行时按账号解析，这里仅占位（老代码只读不写）
    m.TOGGLE_FILE = ""
    _bind(m, pkg, "plugin_toggle")
    return m


# ---------------------------------------------------------------- command_registry

def _install_command_registry(pkg: types.ModuleType) -> types.ModuleType:
    m = types.ModuleType("utils.command_registry")

    class Command:
        def __init__(self, name, keywords, desc, handler, master_only=False, kind="exact"):
            self.name = name
            self.keywords = list(keywords) if isinstance(keywords, (list, tuple)) else [keywords]
            self.desc = desc
            self.handler = handler
            self.master_only = master_only
            self.kind = kind

        def match(self, raw):
            for kw in self.keywords:
                if not kw:
                    continue
                if self.kind == "exact" and raw == kw:
                    return kw
                if self.kind == "suffix" and raw.endswith(kw):
                    return kw
                if self.kind == "prefix" and raw.startswith(kw):
                    return kw
            return None

    class CommandRegistry:
        def __init__(self, plugin_name=""):
            self.plugin_name = plugin_name
            self._commands: list[Command] = []
            self._lock = threading.Lock()

        def register(self, name, keywords, desc, handler,
                     master_only=False, kind="exact"):
            cmd = Command(name, keywords, desc, handler,
                          master_only=master_only, kind=kind)
            with self._lock:
                self._commands.append(cmd)
            return cmd

        def match(self, raw):
            if not raw:
                return None, None
            with self._lock:
                for c in self._commands:
                    kw = c.match(raw)
                    if kw is not None:
                        return c, kw
            return None, None

        def dispatch(self, event, raw, is_master, master_cmds_only=False):
            c, kw = self.match(raw)
            if c is None:
                return False
            if c.master_only and not is_master:
                return False
            if master_cmds_only and not c.master_only:
                return False
            try:
                return bool(c.handler(event, raw, kw))
            except Exception:
                import traceback
                traceback.print_exc()
                return True

        def commands_table(self, title=None):
            """生成指令说明表（与 Linux 版输出格式一致）"""
            lines = []
            if title:
                lines.append(f"【{title}】")
            elif self.plugin_name:
                lines.append(f"【{self.plugin_name}指令】")
            for c in self._commands:
                perm = "仅主人" if c.master_only else "所有人"
                lines.append(f"  {'/'.join(str(k) for k in c.keywords)}  — {c.name}：{c.desc}（{perm}）")
            return "\n".join(lines)

        def all(self):
            """全部指令定义（老插件/面板会调用）"""
            return list(self._commands)

        def labels(self):
            """{触发词: 指令中文名}——插件导入期就会调用（写回配置供面板显示）"""
            labels = {}
            with self._lock:
                for c in self._commands:
                    for kw in c.keywords:
                        labels[kw] = c.name
            return labels

    m.Command = Command
    m.CommandRegistry = CommandRegistry
    _bind(m, pkg, "command_registry")
    return m


# ---------------------------------------------------------------- 装配

def _bind(mod: types.ModuleType, pkg: types.ModuleType, name: str) -> None:
    setattr(pkg, name, mod)
    sys.modules[f"utils.{name}"] = mod
    mod.__package__ = "utils"
    mod.__name__ = f"utils.{name}"


def install() -> types.ModuleType:
    """安装兼容 `utils` 包（幂等）。返回 utils 模块。"""
    global _installed
    with _lock:
        pkg = sys.modules.get("utils")
        if getattr(pkg, "__astercore_compat__", False):
            return pkg

        # 若用户环境真的存在 utils 包，不覆盖它，但告知（避免静默串味）
        if pkg is not None and not getattr(pkg, "__astercore_compat__", False):
            log.warning("sys.modules 已存在 utils（%s），兼容层将被其覆盖或冲突",
                        getattr(pkg, "__file__", "?"))
        pkg = types.ModuleType("utils")
        pkg.__astercore_compat__ = True
        pkg.__path__ = []            # 标记为包，允许 utils.xxx 子模块
        pkg.__doc__ = "AsterCore Linux 版兼容层（自动生成）"
        sys.modules["utils"] = pkg

        _install_api(pkg)
        _install_config(pkg)
        _install_log(pkg)
        _install_ws(pkg)
        _install_plugin_toggle(pkg)
        _install_command_registry(pkg)
        _installed = True
        log.info("已注入 utils 兼容层（老插件可直接运行）")
        return pkg


def is_installed() -> bool:
    return bool(getattr(sys.modules.get("utils"), "__astercore_compat__", False))
