# 栖星 AsterCore · 原生插件加载器（ctypes）
# 运行时加载符合 nap_plugin.h 的 DLL/SO（对应 ABI 草案）。
# v0.1：进程内加载（验证 ABI/JSON 交换）；host 子进程隔离在后续版本接入。

from __future__ import annotations

import ctypes
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .core.models import Event
from .core.plugin import HANDLE_HANDLED, HANDLE_NOT_HANDLED, PluginMeta

log = logging.getLogger("astercore.native")

_ABI_VERSION = 1


class NapApi(ctypes.Structure):
    """对应 nap_plugin.h 的 NapApi（字段顺序必须一致）"""

    _fields_ = [
        ("abi_version", ctypes.c_int),
        ("action", ctypes.CFUNCTYPE(
            ctypes.c_int,
            ctypes.c_char_p,                      # action_json
            ctypes.POINTER(ctypes.c_char_p),      # out_json
            ctypes.POINTER(ctypes.c_int),         # out_len
        )),
        ("log", ctypes.CFUNCTYPE(None, ctypes.c_int, ctypes.c_char_p)),
        ("alloc", ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_ulonglong)),
        ("free", ctypes.CFUNCTYPE(None, ctypes.c_void_p)),
    ]


class _ApiImpl:
    """主程序侧实现 NapApi 回调：插件 action → 这里执行；log → 内核日志。"""

    def __init__(self, action_cb) -> None:
        self._action_cb = action_cb  # fn(action_json: str) -> dict
        self.action = NapApi._fields_[1][1](
            self._py_action)
        self.log = NapApi._fields_[2][1](self._py_log)
        self.alloc = NapApi._fields_[3][1](self._py_alloc)
        self.free = NapApi._fields_[4][1](self._py_free)

    def _py_action(self, action_json: bytes | None, out_json, out_len) -> int:
        try:
            req = json.loads((action_json or b"").decode("utf-8"))
            res = self._action_cb(req)
            # 返回体经 alloc 写入（示例：无返回体场景居多，暂写空）
            if out_json and out_len:
                data = json.dumps(res, ensure_ascii=False).encode("utf-8")
                buf = self._py_alloc(len(data) + 1)
                if buf:
                    ctypes.memmove(buf, data, len(data))
                    ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))[len(data)] = b"\0"
                    out_json[0] = ctypes.cast(buf, ctypes.c_char_p)
                    out_len[0] = len(data)
            return 0
        except Exception as e:
            log.error("原生插件 action 调用失败: %s", e)
            return -1

    def _py_log(self, level: int, msg: bytes | None) -> None:
        lv = {0: "debug", 1: "info", 2: "warn", 3: "error"}.get(level, "info")
        log.log({"debug": 10, "info": 20, "warn": 30, "error": 40}[lv],
                "%s", (msg or b"").decode("utf-8", "replace"))

    def _py_alloc(self, size: int) -> ctypes.c_void_p:
        buf = ctypes.create_string_buffer(size)
        return ctypes.cast(buf, ctypes.c_void_p)

    def _py_free(self, ptr) -> None:
        pass  # create_string_buffer 由 GC 回收（示例级；长期驻留需真实分配器）


class NativePlugin:
    """用 ctypes 加载原生插件库并桥接 Event/action。"""

    def __init__(self, lib_path: str | Path,
                 action_cb) -> None:
        self.lib_path = Path(lib_path)
        self._lib = None
        self._action_cb = action_cb
        self._api_impl: _ApiImpl | None = None

    # ---- 加载 ----
    def load(self) -> None:
        if self._lib is not None:
            return
        self._lib = ctypes.CDLL(str(self.lib_path))
        # 校验 ABI
        self._lib.nap_plugin_abi_version.restype = ctypes.c_int
        ver = self._lib.nap_plugin_abi_version()
        if ver != _ABI_VERSION:
            raise RuntimeError(f"插件 ABI 版本不匹配: {ver} != {_ABI_VERSION}")
        log.info("原生插件 %s 库已加载（ABI v%d）",
                 self.name(), ver)

    def name(self) -> str:
        self.load()
        self._lib.nap_plugin_name.restype = ctypes.c_char_p
        return (self._lib.nap_plugin_name() or b"").decode()

    def version(self) -> str:
        self.load()
        self._lib.nap_plugin_version.restype = ctypes.c_char_p
        return (self._lib.nap_plugin_version() or b"").decode()

    def meta(self) -> PluginMeta:
        return PluginMeta(name=self.name(), name_cn=f"原生·{self.name()}",
                          version=self.version())

    def init(self, config: dict[str, Any]) -> int:
        """调用 nap_plugin_init（注入 NapApi）"""
        self.load()
        self._api_impl = _ApiImpl(self._action_cb)
        api = NapApi(
            abi_version=_ABI_VERSION,
            action=self._api_impl.action,
            log=self._api_impl.log,
            alloc=self._api_impl.alloc,
            free=self._api_impl.free,
        )
        self._lib.nap_plugin_init.restype = ctypes.c_int
        cfg = json.dumps(config or {}, ensure_ascii=False).encode("utf-8")
        return self._lib.nap_plugin_init(cfg, ctypes.byref(api))

    def handle_event(self, event: Event) -> bool:
        """把统一 Event JSON 喂给 nap_plugin_on_event"""
        payload = json.dumps(event.to_dict(), ensure_ascii=False,
                            separators=(",", ":")).encode("utf-8")
        self._lib.nap_plugin_on_event.restype = ctypes.c_int
        ret = self._lib.nap_plugin_on_event(payload)
        return ret == 1

    def unload(self) -> None:
        if self._lib is not None:
            self._lib.nap_plugin_unload()

    def reload(self, config: dict[str, Any]) -> None:
        if not hasattr(self._lib, "nap_plugin_reload"):
            return
        cfg = json.dumps(config or {}, ensure_ascii=False).encode("utf-8")
        self._lib.nap_plugin_reload(cfg)
