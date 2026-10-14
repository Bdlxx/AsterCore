# 栖星 AsterCore · 原生插件加载器（ctypes）
# 运行时加载符合 nap_plugin.h 的 DLL/SO（对应 ABI 草案）。
# v0.1：进程内加载（验证 ABI/JSON 交换）；host 子进程隔离在后续版本接入。

from __future__ import annotations

import ctypes
import json
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .core.models import Event
from .core.plugin import HANDLE_HANDLED, HANDLE_NOT_HANDLED, PluginMeta

log = logging.getLogger("astercore.native")

_ABI_VERSION = 1

# ---- 平台动态库格式守卫 ----
# 为什么要有这一层：ctypes.CDLL 把"文件格式不对"直接交给系统加载器，而 Windows
# 加载器对 ELF（Linux .so）**不抛异常，弹模态对话框**：
#     python.exe - 损坏的映像 … libnap_hello.so 没有被指定在 Windows 上运行 (0xc000012f)
# 进程就停在对话框上等人点"确定" —— 测试、面板自检、首启探测会一起卡死。
# 实测：源目录里带着 Linux 编出来的 plugin-sdk/examples/c-sample/libnap_hello.so 时，
# 跑 tests/test_native.py 必弹框。所以加载前先看文件头，格式不符就抛普通异常。
_ELF_MAGIC = b"\x7fELF"
_MACHO_MAGICS = (b"\xfe\xed\xfa\xce", b"\xce\xfa\xed\xfe",
                 b"\xfe\xed\xfa\xcf", b"\xcf\xfa\xed\xfe",
                 b"\xca\xfe\xba\xbe")
_FORMAT_CN = {"pe": "Windows DLL(.dll)", "elf": "Linux 动态库(.so)",
              "macho": "macOS 动态库(.dylib)", "unknown": "未知格式"}


def lib_format(path: str | Path) -> str:
    """按文件头判断动态库格式：'pe' | 'elf' | 'macho' | 'unknown'"""
    try:
        with open(path, "rb") as fh:
            head = fh.read(4)
    except OSError:
        return "unknown"
    if head[:2] == b"MZ":
        return "pe"
    if head == _ELF_MAGIC:
        return "elf"
    if head in _MACHO_MAGICS:
        return "macho"
    return "unknown"


def expected_lib_format() -> str:
    """当前平台能加载的动态库格式"""
    if sys.platform == "win32":
        return "pe"
    if sys.platform == "darwin":
        return "macho"
    return "elf"


def check_platform_lib(path: str | Path) -> None:
    """加载前校验：库不存在 / 格式与当前系统不符 → 抛清晰异常（不弹系统对话框）"""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"原生插件库不存在: {p}")
    kind = lib_format(p)
    want = expected_lib_format()
    if kind != want:
        raise RuntimeError(
            f"{p.name} 是 {_FORMAT_CN.get(kind, kind)}，当前系统（{sys.platform}）"
            f"只能加载 {_FORMAT_CN[want]}；请用本平台编译器重新构建该插件"
            f"（构建命令见 plugin-sdk/examples/c-sample/nap_hello.c 头部注释）")


def _load_library(path: str | Path) -> ctypes.CDLL:
    """ctypes.CDLL，Windows 上顺带把加载器硬错误降级成异常。

    SetErrorMode(SEM_FAILCRITICALERRORS) 只影响"加载器是否弹框"，加载完立刻
    恢复原值，不改变进程其余行为。
    """
    if sys.platform == "win32":
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        old = kernel32.SetErrorMode(0x0001)      # SEM_FAILCRITICALERRORS
        try:
            return ctypes.CDLL(str(path))
        finally:
            kernel32.SetErrorMode(old)
    return ctypes.CDLL(str(path))


def symbol_candidates(name: str) -> list[str]:
    """导出符号的候选名（无修饰优先）。

    为什么要试多个：**易语言**这类编译器的"公开子程序"导出时常带修饰
    （`_nap_plugin_on_event` / `_nap_plugin_on_event@4`）。x64 上本不该有修饰，
    但真机出现过，所以这里按无修饰优先依次试 —— 对合规 DLL 没有任何影响。

    ⚠ 调用约定仍是 **cdecl**（ABI 约定）；带 `@N` 的写法是 32 位 stdcall 的装饰，
    32 位插件 v1 不支持（见 `docs/ABI草案.md` §10.4）。
    """
    return [name, f"_{name}", f"{name}@4", f"_{name}@4"]


def resolve_symbol(lib, name: str):
    """按候选名取导出符号；全都没有就抛 AttributeError（消息里带上试过的名字）"""
    for cand in symbol_candidates(name):
        try:
            return getattr(lib, cand)
        except AttributeError:
            continue
    raise AttributeError(f"缺少导出符号 {name}（试过：{' / '.join(symbol_candidates(name))}）")


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
        check_platform_lib(self.lib_path)
        self._lib = _load_library(self.lib_path)
        # 校验 ABI
        fn = resolve_symbol(self._lib, "nap_plugin_abi_version")
        fn.restype = ctypes.c_int
        ver = fn()
        if ver != _ABI_VERSION:
            raise RuntimeError(f"插件 ABI 版本不匹配: {ver} != {_ABI_VERSION}")
        log.info("原生插件 %s 库已加载（ABI v%d）",
                 self.name(), ver)

    def name(self) -> str:
        self.load()
        fn = resolve_symbol(self._lib, "nap_plugin_name")
        fn.restype = ctypes.c_char_p
        return (fn() or b"").decode()

    def version(self) -> str:
        self.load()
        fn = resolve_symbol(self._lib, "nap_plugin_version")
        fn.restype = ctypes.c_char_p
        return (fn() or b"").decode()

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
        fn = resolve_symbol(self._lib, "nap_plugin_init")
        fn.restype = ctypes.c_int
        cfg = json.dumps(config or {}, ensure_ascii=False).encode("utf-8")
        return fn(cfg, ctypes.byref(api))

    def handle_event(self, event: Event) -> bool:
        """把统一 Event JSON 喂给 nap_plugin_on_event"""
        payload = json.dumps(event.to_dict(), ensure_ascii=False,
                            separators=(",", ":")).encode("utf-8")
        fn = resolve_symbol(self._lib, "nap_plugin_on_event")
        fn.restype = ctypes.c_int
        ret = fn(payload)
        return ret == 1

    def unload(self) -> None:
        if self._lib is not None:
            try:
                resolve_symbol(self._lib, "nap_plugin_unload")()
            except AttributeError:
                log.warning("插件 %s 没有导出 nap_plugin_unload（跳过）", self.lib_path.name)

    def reload(self, config: dict[str, Any]) -> None:
        try:                                      # 可选导出：没有就当作"不支持热更"
            fn = resolve_symbol(self._lib, "nap_plugin_reload")
        except AttributeError:
            return
        cfg = json.dumps(config or {}, ensure_ascii=False).encode("utf-8")
        fn(cfg)
