# -*- coding: utf-8 -*-
"""栖星 AsterCore · Python 插件 SDK（单文件 / 零依赖 / 双端通用）

契约就是你熟悉的那套（《插件开发 SDK》）：
    __plugin_name_cn__ / __plugin_name_en__ / __plugin_version__ / __plugin_desc__ / __plugin_author__
    def handle(event: dict) -> bool          # 返回 True = 已处理（停链）
    from utils.api import send_message, http_get
    from utils.plugin_toggle import is_enabled, set_enabled
本文件**不引入新协议**，只把上面这些包得好写一点，并且**不 import 任何 astercore 内部**
（那样写出来的插件在 Linux 版上跑不了）。

三种用法，随便挑：

  ① 只用规则匹配（最省事）：
        from plugins import nap_sdk as sdk          # 把本文件拷进插件目录即可

        @sdk.meta(name_cn="示例·签到", name_en="sign_in", version="1.0.0")
        @sdk.rule(sdk.cmd("签到"))
        def sign_in(event):
            sdk.reply(event, "今天也要元气满满～")
            return True

     `@sdk.rule` 会自动在模块里生成 `handle`（你自己写了 handle 就不动它）。

  ② 配置 + 分群开关 + 帮助：

        sdk.declare_config({
            "commands": {"sign1": ("签到", "每日签到"), "rank": ("签到排行", "查看排行榜")},
            "settings": {"limit": ("每日上限", 3, "number"), "auto": ("自动签到", True, "bool")},
            "messages": {"welcome": ("欢迎语", "你好呀～")},
        })

        @sdk.rule(sdk.cmd("签到"))
        def sign_in(event):
            if not sdk.enabled(event):           # 分群开关（私聊永远开）
                return False
            n = sdk.cfg("settings.limit", 3)
            sdk.reply(event, sdk.cfg("messages.welcome", "你好呀～") + f"（上限 {n}）")
            return True

        sdk.help_rule()                          # 回一条命令表（可选）

  ③ 离线自测（不用起机器人）：

        if __name__ == "__main__":
            sdk.dev(globals(), raw_message="签到")     # 打屏看命中与回复

    命令行也行：python nap_sdk.py --run sign_in.py --text 签到
"""

from __future__ import annotations

import argparse
import inspect
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Callable, Sequence

SDK_VERSION = "1.0.0"

# --------------------------------------------------------------------------- #
# utils 接入（宿主里是真的 utils；离线自测时只在缺失时补一个替身，绝不覆盖真货）
# --------------------------------------------------------------------------- #
_REPLIES: list[dict] = []          # 离线自测收集到的"发出去的消息"


def _real_utils() -> bool:
    return getattr(sys.modules.get("utils"), "__astercore_compat__", False) or \
        getattr(sys.modules.get("utils"), "__file__", None) is not None


def _install_stub_utils() -> None:
    """离线跑插件时补一个 utils 替身（只有真的没有 utils 才装）"""
    import types

    if "utils" in sys.modules:
        return
    pkg = types.ModuleType("utils")
    pkg.__path__ = []
    pkg.__doc__ = "nap_sdk 离线替身（仅自测用）"

    api = types.ModuleType("utils.api")

    def send_message(event, message):
        _REPLIES.append({"event": event, "message": message})
        return {"ok": True}

    def http_get(action, params=None):
        _REPLIES.append({"http_get": action, "params": params or {}})
        return {"ok": True, "data": {}}

    api.send_message = send_message
    api.http_get = http_get
    api.ws_send = lambda event, message, on_ok=None, echo=None: {"ok": True}

    cfg = types.ModuleType("utils.config")
    _cfg_store: dict = {}
    cfg.get_config = lambda key, default=None: _cfg_store.get(key, default)
    cfg.get_bot_name = lambda: _cfg_store.get("BOT_NAME", "测试机")
    cfg.get_bot_qq = lambda: _cfg_store.get("BOT_QQ", 10000)
    cfg.get_master_qq = lambda: _cfg_store.get("MASTER_QQ", 10000)
    cfg.load_config = lambda: dict(_cfg_store)

    tog = types.ModuleType("utils.plugin_toggle")
    _toggles: dict = {}
    tog.is_enabled = lambda group_id, plugin: bool(_toggles.get((str(group_id), str(plugin)), False))
    tog.set_enabled = lambda group_id, plugin, enabled=True: _toggles.__setitem__(
        (str(group_id), str(plugin)), bool(enabled))

    for name, mod in (("api", api), ("config", cfg), ("plugin_toggle", tog)):
        setattr(pkg, name, mod)
        sys.modules[f"utils.{name}"] = mod
    sys.modules["utils"] = pkg


def _utils(name: str):
    """取 utils 子模块；宿主里是真的，离线时是替身"""
    _install_stub_utils()
    return sys.modules.get(f"utils.{name}")


def reply(event: dict, message) -> Any:
    """回消息（= utils.api.send_message）"""
    return _utils("api").send_message(event, message)


def send(event: dict, message) -> Any:
    return reply(event, message)


def http_get(action: str, params: dict | None = None):
    """调 NapCat 的 HTTP API（= utils.api.http_get）"""
    return _utils("api").http_get(action, params or {})


def event_text(event: dict) -> str:
    """纯文本：raw_message（老插件的写法），缺了就用 message 段兜底"""
    if not isinstance(event, dict):
        return ""
    raw = event.get("raw_message")
    if raw:
        return str(raw)
    out = []
    for seg in (event.get("message") or []):
        if isinstance(seg, dict) and seg.get("type") == "text":
            out.append(str((seg.get("data") or {}).get("text", "")))
    return "".join(out)


# --------------------------------------------------------------------------- #
# 分群开关（名字自动取 __plugin_name_en__）
# --------------------------------------------------------------------------- #
def _caller_globals():
    """向上找到第一个不是本 SDK 的调用帧（插件调 sdk.* 时，那就是插件模块）"""
    try:
        f = sys._getframe(1)
    except Exception:                          # noqa: BLE001
        return None
    while f is not None:
        if f.f_globals.get("__name__") not in (__name__, "nap_sdk"):
            return f.f_globals
        f = f.f_back
    return None


def _scope_mod(scope: Any = None):
    """解析作用域：显式 scope > 调用者帧 > SDK 自己"""
    if scope is not None:
        return _mod_of(scope)
    return _caller_globals() or _mod_of(None)


def plugin_name(scope: Any = None) -> str:
    """插件英文名（__plugin_name_en__）；插件里裸调时会自动认到自己那个模块"""
    for src in (_scope_mod(scope), sys.modules.get(__name__)):
        if not src:
            continue
        nm = src.get("__plugin_name_en__") if isinstance(src, dict) else getattr(src, "__plugin_name_en__", None)
        if nm:
            return str(nm)
    return ""


def enabled(event: dict, name: str = "") -> bool:
    """本群是否开启本插件（私聊/无群号一律 True）"""
    gid = (event or {}).get("group_id") if isinstance(event, dict) else None
    if not gid:
        return True
    key = name or plugin_name((event or {}).get("__plugin_scope__")) or plugin_name()
    if not key:
        return True
    try:
        from utils.plugin_toggle import is_enabled
    except ImportError:
        return True
    try:
        return bool(is_enabled(gid, key))
    except Exception:                          # noqa: BLE001 老实现可能没有这个键
        return True


def toggle(event: dict, on: bool = True, name: str = "") -> bool:
    """开/关本群的本插件（= utils.plugin_toggle.set_enabled）"""
    gid = (event or {}).get("group_id") if isinstance(event, dict) else None
    if not gid:
        return False
    key = name or plugin_name()
    try:
        from utils.plugin_toggle import set_enabled
        set_enabled(gid, key, bool(on))
        return True
    except Exception:                          # noqa: BLE001
        return False


# --------------------------------------------------------------------------- #
# 配置：声明一次，两端都能出表单
#   · Linux 的 WebUI 认识 commands / settings / messages 三段（就是你文档里那套）
#   · Windows 面板吃 __config_schema__（没有就按当前值推断）
#   → declare_config() 同时产出：JSON 形状（写文件） + __config_schema__（面板表单）
# --------------------------------------------------------------------------- #
_KIND = {"bool": "bool", "number": "number", "int": "number", "float": "number",
         "textarea": "textarea", "text": "text", "password": "password"}


def declare_config(spec: dict, defaults: dict | None = None, *,
                   scope: Any = None) -> dict:
    """声明配置（commands / settings / messages 三段），并生成面板用的 schema。

    写法（简写元组或展开 dict 都行）：
        sdk.declare_config({
            "commands": {"sign1": ("签到", "每日签到")},
            "settings": {"limit": ("每日上限", 3, "number"), "auto": ("自动签到", True, "bool")},
            "messages": {"welcome": ("欢迎语", "你好呀～")},
        })
    """
    schema: dict[str, dict] = {}
    base: dict[str, Any] = {}
    for section in ("commands", "settings", "messages"):
        items = (spec or {}).get(section) or {}
        if not isinstance(items, dict):
            continue
        sec_vals: dict[str, Any] = {}
        for key, desc in items.items():
            label, default, kind, help_text = _parse_desc(key, desc)
            ftype = _KIND.get(str(kind).lower(), "textarea" if section != "settings" else "text")
            if section == "settings" and isinstance(default, bool):
                ftype = "bool"
            elif section == "settings" and isinstance(default, (int, float)):
                ftype = "number"
            schema[key] = {"type": ftype, "label": label or key,
                           "default": default, "group": _GROUP_CN.get(section, section),
                           **({"help": help_text} if help_text else {})}
            sec_vals[key] = default
        base[section] = sec_vals
    if defaults:
        base.update(defaults)
    mod = _mod_of(scope) if scope is not None else sys.modules.get("__main__")
    if mod is not None:
        if getattr(mod, "__config_schema__", None) is None:
            mod.__config_schema__ = schema
        if getattr(mod, "__default_config__", None) is None:
            mod.__default_config__ = base
    return base


_GROUP_CN = {"commands": "指令", "settings": "设置", "messages": "文案"}


def _parse_desc(key: str, desc: Any) -> tuple[str, Any, str, str]:
    """把 ("标签", 默认值, "类型") / {"label":…,"default":…} / 默认值 统一成四元组"""
    if isinstance(desc, dict):
        return (str(desc.get("label") or key), desc.get("default"),
                str(desc.get("type") or "text"), str(desc.get("help") or ""))
    if isinstance(desc, (tuple, list)):
        label = str(desc[0]) if len(desc) > 0 else key
        default = desc[1] if len(desc) > 1 else None
        kind = str(desc[2]) if len(desc) > 2 else ""
        help_text = str(desc[3]) if len(desc) > 3 else ""
        return label, default, kind, help_text
    return str(desc), None, "", ""


def config_path(name: str = "", data_dir: str | Path | None = None) -> Path:
    """配置文件位置（两端各自约定，插件不要硬编码路径）。

    解析顺序：
      1) 显式 data_dir / 已有的 Windows 约定 <实例>/data/plugins/<名>/config.json
      2) 已有的 Linux 约定 <实例>/data/<名>_config.json
      3) 新建时用 Linux 约定（跨端最省事：Linux 的 WebUI 直接认这个文件）
    """
    key = name or plugin_name() or "plugin"
    if data_dir is not None:
        d = Path(data_dir)
        return d / f"{key}_config.json"
    inst = _instance_dir()
    if inst is not None:
        win = inst / "data" / "plugins" / key / "config.json"
        legacy = inst / "data" / f"{key}_config.json"
        if win.exists():
            return win
        if legacy.exists():
            return legacy
        return legacy
    return Path("data") / f"{key}_config.json"


def _instance_dir() -> Path | None:
    """当前账号的实例目录（Linux=CWD=实例目录；Windows=<ASTERCORE_HOME>/accounts/<QQ>）"""
    aid = ""
    try:
        aid = str(_utils("config").get_bot_qq() or "")
    except Exception:                          # noqa: BLE001
        aid = ""
    home = os.environ.get("ASTERCORE_HOME") or ""
    if home and aid:
        p = Path(home) / "accounts" / aid
        if p.is_dir():
            return p
    cwd = Path.cwd()
    if (cwd / "plugins").is_dir() or (cwd / "data").is_dir() or (cwd / "config.json").is_file():
        return cwd
    return None


def load_config(path: str | Path | None = None, *, name: str = "", scope: Any = None) -> dict:
    """读配置（缺项用插件模块的 __default_config__ 兜底；坏文件不炸）"""
    p = Path(path) if path is not None else config_path(name)
    data: dict = {}
    try:
        if p.is_file():
            loaded = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data = loaded
    except Exception as e:                     # noqa: BLE001 配置坏了不该让插件崩溃
        _log(f"读取配置失败 {p}: {e}")
    defaults = getattr(_scope_mod(scope), "__default_config__", None) or {}
    return _merge(defaults, data)


def save_config(data: dict, path: str | Path | None = None, *, name: str = "") -> Path:
    """写配置（自动建目录；原子替换避免写坏）"""
    p = Path(path) if path is not None else config_path(name)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, p)
    return p


def _merge(base: dict, over: dict) -> dict:
    out = dict(base or {})
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def cfg(key: str, default: Any = None, *, name: str = "", path: str | Path | None = None,
        scope: Any = None) -> Any:
    """取值，支持点路径：sdk.cfg("settings.limit", 3)"""
    data = load_config(path, name=name, scope=scope)
    cur: Any = data
    for part in str(key).split("."):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return default if cur is None else cur


def set_cfg(key: str, value: Any, *, name: str = "", path: str | Path | None = None,
            scope: Any = None) -> Path:
    """写单个值（保留其它键）：sdk.set_cfg("settings.limit", 5)"""
    p = Path(path) if path is not None else config_path(name)
    data = load_config(p, name=name, scope=scope)
    cur = data
    parts = str(key).split(".")
    for part in parts[:-1]:
        nxt = cur.get(part)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[part] = nxt
        cur = nxt
    cur[parts[-1]] = value
    return save_config(data, p, name=name)


# --------------------------------------------------------------------------- #
# 规则匹配（自动生成模块级 handle；你自己写了 handle 就不动）
# --------------------------------------------------------------------------- #
class Rule:
    """一条规则：test(text, event) 命中就调 handler；handler 返回 True = 已处理"""

    def __init__(self, name: str, test: Callable[[str, dict], bool], *, priority: int = 0) -> None:
        self.name = name
        self.test = test
        self.priority = priority
        self.handler: Callable | None = None
        self.rx = None

    def __call__(self, fn: Callable) -> Callable:
        # 已经自带 handler 的规则（如 help_rule）**不覆盖**：否则装饰一层薄壳就把回复吞了
        if self.handler is None:
            self.handler = fn
        reg = _RULES.setdefault(_mod_name(fn), [])
        if not any(r is self for r in reg):
            reg.append(self)
        reg.sort(key=lambda r: -r.priority)          # 稳定：同优先级按定义顺序
        _ensure_handle(_mod_of(fn))
        if not getattr(fn, "__doc__", None):
            fn.__doc__ = self.name
        return fn

    def match(self, text: str, event: dict) -> bool:
        try:
            return bool(self.test(text, event))
        except Exception:                            # noqa: BLE001 规则写错不该炸事件链
            return False


_RULES: dict[str, list[Rule]] = {}


def _mod_name(obj: Any) -> str:
    if isinstance(obj, str):
        return obj
    mod = getattr(obj, "__module__", None)
    if isinstance(obj, dict):
        return str(mod or "__main__")
    return str(mod or getattr(obj, "name", "") or "__main__")


def _mod_of(scope: Any):
    if scope is None:
        return sys.modules.get(__name__)
    if isinstance(scope, dict):
        return sys.modules.get(scope.get("__name__", "__main__")) or sys.modules.get("__main__")
    if inspect.ismodule(scope):                 # 直接给了模块对象（help_rule / 测试常用）
        return scope
    mod = sys.modules.get(getattr(scope, "__module__", "") or "")
    return mod or sys.modules.get("__main__")


def _mk(name: str, test, priority: int = 0) -> Rule:
    return Rule(name, test, priority=priority)


def cmd(*names: str, priority: int = 0) -> Rule:
    """整条等于命令，或命令后跟参数（「签到」「签到 3」；大小写不敏感）"""
    low = [n.strip().lower() for n in names if n and n.strip()]

    def _t(text: str, event: dict) -> bool:
        t = text.strip().lower()
        return any(t == w or t.startswith(w + " ") or t.startswith(w + "\n") for w in low)

    return _mk("cmd:" + "/".join(names), _t, priority)


def kw(*words: str, priority: int = 0) -> Rule:
    low = [w.lower() for w in words if w]
    return _mk("kw:" + "/".join(words),
               lambda text, event: any(w in text.lower() for w in low), priority)


def prefix(*ps: str, priority: int = 0) -> Rule:
    keep = [p for p in ps if p]
    return _mk("prefix:" + "/".join(keep),
               lambda text, event: any(text.strip().startswith(p) for p in keep), priority)


def exact(*texts: str, priority: int = 0) -> Rule:
    want = set(t.strip() for t in texts if t)
    return _mk("exact:" + "/".join(want), lambda text, event: text.strip() in want, priority)


def re_(pattern: str, flags: int = 0, priority: int = 0) -> Rule:
    rx = re.compile(pattern, flags)
    r = _mk("re:" + pattern, lambda text, event: rx.search(text) is not None, priority)
    r.rx = rx
    return r


def func(name: str, test, priority: int = 0) -> Rule:
    return _mk(name, test, priority)


def always(priority: int = -100) -> Rule:
    return _mk("always", lambda text, event: True, priority)


def in_group(rule: Rule) -> Rule:
    """限定群事件才匹配"""
    inner = rule.test
    rule.test = lambda text, event: (event or {}).get("message_type") == "group" and inner(text, event)
    return rule


def at_me(rule: Rule) -> Rule:
    """限定消息里 @ 了机器人"""
    inner = rule.test

    def _t(text: str, event: dict) -> bool:
        me = str((event or {}).get("self_id") or "")
        for seg in ((event or {}).get("message") or []):
            if isinstance(seg, dict) and seg.get("type") == "at" \
                    and str((seg.get("data") or {}).get("qq", "")) == me:
                return inner(text, event)
        return False

    rule.test = _t
    return rule


def rule(*rules: Rule, priority: int = 0) -> Callable:
    """把多个规则挂到同一个函数上：@sdk.rule(sdk.cmd("签到"), sdk.kw("qiandao"))"""
    def deco(fn: Callable) -> Callable:
        for r in rules:
            if priority and not r.priority:
                r.priority = priority
            r(fn)
        return fn
    return deco


on = rule


def _ensure_handle(mod) -> None:
    """模块里没有 handle 时，按注册的规则生成一个（有就不动 —— 尊重手写的）"""
    if mod is None or getattr(mod, "handle", None) is not None:
        return
    name = getattr(mod, "__name__", "__main__")
    rules = _RULES.setdefault(name, [])

    def handle(event: dict) -> bool:
        if not isinstance(event, dict):
            return False
        if str(event.get("post_type") or "message") != "message":
            return False
        text = event_text(event)
        for r in list(_RULES.get(name, rules)):
            if not r.match(text, event):
                continue
            out = r.handler(event) if r.handler else None
            if out is True:
                return True
        return False

    mod.handle = handle


def help_rule(*triggers: str) -> Rule:
    """内置「帮助」规则：回一条命令表（触发词默认 帮助 / help / 菜单）"""
    words = [w.lower() for w in (triggers or ("帮助", "help", "菜单"))]

    def _t(text: str, event: dict) -> bool:
        return text.strip().lower() in words

    r = _mk("help:" + "/".join(words), _t, priority=50)

    def _h(event: dict) -> bool:
        reply(event, help_text(scope=sys.modules.get(mod_name)))
        return True

    r.handler = _h
    # 自带 handler + 自己登记：用法就是裸调 `sdk.help_rule()`（别再用 @sdk.rule 包一层）
    mod_name = str(sys._getframe(1).f_globals.get("__name__") or "__main__")
    reg = _RULES.setdefault(mod_name, [])
    if not any(x is r for x in reg):
        reg.append(r)
    reg.sort(key=lambda x: -x.priority)
    _ensure_handle(sys.modules.get(mod_name))
    return r


def help_text(name: str = "", scope: Any = None) -> str:
    """命令表文本（插件名 + 各指令说明；commands 来自配置/默认值）"""
    mod = _scope_mod(scope)
    key = name or plugin_name(scope)
    lines = [f"【{getattr(mod, '__plugin_name_cn__', '') or key}】"
             f"{getattr(mod, '__plugin_desc__', '') or ''}".strip()]
    data = load_config(name=key, scope=scope) if key else {}
    cmds = (data.get("commands") or {}) if isinstance(data, dict) else {}
    if cmds:
        lines.append("指令：")
        for ck, ctext in cmds.items():
            desc = _CMD_HELP.get(str(ck), "")
            lines.append(f"  {ctext} — {desc}" if desc else f"  {ctext}")
    return "\n".join(lines)


_CMD_HELP: dict[str, str] = {}


# --------------------------------------------------------------------------- #
# 元数据 + 可移植性检查
# --------------------------------------------------------------------------- #
def meta(*, name_cn: str = "", name_en: str = "", version: str = "1.0.0",
         desc: str = "", author: str = "") -> Callable:
    """声明插件元信息（写模块级 dunder，宿主直接读它们）

        @sdk.meta(name_cn="示例·签到", name_en="sign_in", version="1.0.0", desc="每日签到")
        @sdk.rule(sdk.cmd("签到"))
        def sign_in(event): ...
    """
    def deco(obj: Any) -> Any:
        mod = _mod_of(obj if not isinstance(obj, dict) else obj)
        if mod is None and isinstance(obj, dict):
            mod = obj
        if mod is not None:
            set_meta(mod, name_cn=name_cn, name_en=name_en, version=version,
                     desc=desc, author=author)
        return obj
    return deco


def set_meta(scope: Any = None, *, name_cn: str = "", name_en: str = "", version: str = "",
             desc: str = "", author: str = "") -> None:
    """直接写元信息（不方便用装饰器时）：sdk.set_meta(globals(), name_cn="…", name_en="…")"""
    mod = scope if isinstance(scope, dict) else _mod_of(scope)
    if mod is None:
        return
    if name_cn:
        mod["__plugin_name_cn__"] = name_cn
    if name_en:
        mod["__plugin_name_en__"] = name_en
    if version:
        mod["__plugin_version__"] = version
    if desc:
        mod["__plugin_desc__"] = desc
    if author:
        mod["__plugin_author__"] = author


def check(scope: Any = None) -> list[str]:
    """可移植性/契约检查（空列表 = 通过）。离线可跑，建议写进插件自测。"""
    mod = scope if isinstance(scope, dict) else _mod_of(scope)
    problems: list[str] = []
    if mod is None:
        return ["拿不到模块对象 —— 用 sdk.check(globals()) 调用"]
    src = ""
    path = mod.get("__file__") if isinstance(mod, dict) else getattr(mod, "__file__", None)
    if path and Path(path).is_file():
        try:
            src = Path(path).read_text(encoding="utf-8")
        except Exception:                       # noqa: BLE001
            src = ""
    if "import astercore" in src or "from astercore" in src:
        problems.append("import 了 astercore.* 内部 —— Linux 版没有这个包，插件会加载失败")
    if re.search(r"class\s+\w+\s*\(\s*\w*Plugin\s*\)", src):
        problems.append("用了类插件（class Xxx(Plugin)）—— Linux 版不支持，跨端请用模块级 handle")
    if re.search(r"\bself\.ctx\b", src):
        problems.append("用了 self.ctx.* 发消息 —— Linux 版没有 ctx，请改用 utils.api.send_message")
    if "data" in src and re.search(r"[\"']data[\"']\s*[,)]|\"data/", src):
        problems.append("硬编码了 data/ 路径 —— 两端数据目录不同，请用 sdk.config_path()/sdk.cfg()")
    if (mod.get("handle") if isinstance(mod, dict) else getattr(mod, "handle", None)) is None:
        problems.append("没有 handle：既没手写，也没有任何 @sdk.rule(...) 生成")
    for field in ("__plugin_name_cn__", "__plugin_name_en__"):
        got = mod.get(field) if isinstance(mod, dict) else getattr(mod, field, None)
        if not got:
            problems.append(f"缺 {field}（用 @sdk.meta(...) 或 sdk.set_meta(globals(), ...)）")
    return problems


# --------------------------------------------------------------------------- #
# 离线自测
# --------------------------------------------------------------------------- #
def make_event(raw_message: str = "签到", *, group_id: Any = 123456, user_id: Any = 10000,
               self_id: Any = 10000, message_type: str | None = None,
               segments: list | None = None) -> dict:
    """造一个事件 dict（字段与《插件开发 SDK》事件表一致）"""
    segs = segments if segments is not None else [{"type": "text", "data": {"text": raw_message}}]
    return {
        "post_type": "message",
        "message_type": message_type or ("group" if group_id else "private"),
        "raw_message": raw_message,
        "user_id": user_id,
        "group_id": group_id,
        "self_id": self_id,
        "message": segs,
        "time": 0,
    }


def dev(scope: Any = None, raw_message: str = "签到", *, group_id: Any = 123456,
        user_id: Any = 10000, verbose: bool = True) -> list:
    """离线跑一次插件：dev(globals(), raw_message="签到") → 返回"发出去的消息"列表"""
    mod = scope if isinstance(scope, dict) else _mod_of(scope)
    if mod is None:
        raise RuntimeError("dev() 需要模块对象：sdk.dev(globals(), ...)")
    _REPLIES.clear()
    handle = mod.get("handle") if isinstance(mod, dict) else getattr(mod, "handle", None)
    if handle is None:
        _ensure_handle(sys.modules.get(mod.get("__name__", "")) or mod)
        handle = mod.get("handle")
    if handle is None:
        raise RuntimeError("没有 handle —— 至少写一条 @sdk.rule(...)")
    event = make_event(raw_message, group_id=group_id, user_id=user_id)
    handled = bool(handle(event))
    if verbose:
        who = mod.get("__plugin_name_cn__") or mod.get("__plugin_name_en__") or "插件"
        print(f"[dev] {who} 收到「{raw_message}」→ {'已处理' if handled else '未处理'}")
        for i, item in enumerate(_REPLIES, 1):
            print(f"[dev] 第 {i} 条发出：{item.get('message', item)}")
        if not _REPLIES:
            print("[dev] （没发出任何消息）")
    return list(_REPLIES)


def _load_module(path: str | Path):
    import importlib.util

    p = Path(path)
    if p.is_dir():
        cand = sorted(x for x in p.glob("*.py")
                      if not x.name.startswith("_") and x.stem not in ("nap_sdk", "new_plugin"))
        if not cand:
            raise SystemExit(f"{path} 下没有可跑的 .py")
        p = cand[0]
    spec = importlib.util.spec_from_file_location(p.stem, p)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[p.stem] = mod
    spec.loader.exec_module(mod)
    return mod


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="nap_sdk", description="栖星 AsterCore · Python 插件 SDK")
    ap.add_argument("--run", metavar="插件.py", help="离线跑一个插件文件/目录")
    ap.add_argument("--text", default="签到", help="喂给插件的事件文本")
    ap.add_argument("--group", default="123456", help="群号（留空 = 私聊）")
    ap.add_argument("--check-only", action="store_true", help="只做契约/可移植性检查")
    args = ap.parse_args(list(argv) if argv is not None else None)
    if not args.run:
        print(f"nap_sdk {SDK_VERSION} · utils={'真的' if _real_utils() else '离线替身'}")
        print("用法：python nap_sdk.py --run 你的插件.py --text 签到")
        return 0
    mod = _load_module(args.run)
    problems = check(vars(mod))
    for p in problems:
        print("检查：-", p)
    if not args.check_only:
        dev(vars(mod), raw_message=args.text, group_id=(args.group or None))
    return 1 if problems else 0


__all__ = [
    "SDK_VERSION", "reply", "send", "http_get", "event_text",
    "meta", "set_meta", "declare_config", "config_path", "load_config", "save_config",
    "cfg", "set_cfg", "enabled", "toggle", "plugin_name",
    "rule", "on", "cmd", "kw", "prefix", "exact", "re_", "func", "always",
    "in_group", "at_me", "help_rule", "help_text",
    "dev", "check", "make_event",
]


if __name__ == "__main__":
    raise SystemExit(main())