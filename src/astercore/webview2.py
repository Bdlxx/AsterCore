# 栖星 AsterCore · WebView2 支持检测（三层）
#
# 为什么需要：桌面端启动时要在**毫秒级**判断本机能不能跑 WebView2，
# 决定界面走「内嵌窗口」还是「浏览器」。检测本身不耗时（注册表 + 系统调用，<10ms），
# 真正的设计重点是**降级时要明确告知用户**（见 nativeui.py）。
#
# 三层：① 系统 build 号 ② Runtime 是否安装 ③ Runtime 版本是否达标
# 任何一层不过就降级，并把**具体原因**带出去给用户看。
#
# 可测性设计：所有 Windows 特有输入（系统版本、注册表）都通过参数注入，
# 于是这套判定逻辑能在 Linux/CI 上完整单测（见 tests/test_webview2.py）。

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass
from typing import Callable

log = logging.getLogger("astercore.webview2")

# WebView2 Runtime 在 EdgeUpdate 下的固定 GUID
WV2_GUID = "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"

# 三个查询位置（规范原文）：
#   · WOW6432Node 是 32 位视角，很多系统上 WebView2 只装在这里
#   · 标准路径对应 64 位 / ARM64 安装
#   · HKCU 对应用户级安装，某些场景只有这一处有记录
REG_PATHS_HKLM = (
    rf"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{WV2_GUID}",
    rf"SOFTWARE\Microsoft\EdgeUpdate\Clients\{WV2_GUID}",
)
REG_PATH_HKCU = rf"SOFTWARE\Microsoft\EdgeUpdate\Clients\{WV2_GUID}"

# Runtime 最低主版本：Win7 SP1 / Server 2012 R2 上的 WebView2 停在 109
MIN_MAJOR_DEFAULT = 92
MIN_MAJOR_WIN7 = 109

# 系统 build 号判定（注意：**不能用 platform.release()**，Python 3.12 之前
# 对 Windows 11 会返回 '10'，见规范 §7.1）
BUILD_WIN11 = 22000
BUILD_WIN10_1803 = 17134
BUILD_WIN10_1507 = 10240
BUILD_WIN81 = 9600
BUILD_WIN8 = 9200
BUILD_WIN7_SP1 = 7601

WV2_DOWNLOAD_URL = "https://developer.microsoft.com/microsoft-edge/webview2/"

# 结果码
OK = "ok"
NOT_WINDOWS = "not_windows"
OS_TOO_OLD = "os_too_old"
RUNTIME_MISSING = "runtime_missing"
VERSION_TOO_OLD = "version_too_old"


@dataclass(slots=True)
class Wv2Status:
    """三层检测结果。ok=True 才允许走 WebView2 模式。"""

    ok: bool
    code: str
    reason: str                 # 给用户看的中文原因（会出现在弹窗里）
    build: int | None = None
    os_label: str = ""
    runtime: str | None = None
    min_major: int = MIN_MAJOR_DEFAULT

    @property
    def needs_install(self) -> bool:
        """是否属于「装一下就能获得更好体验」的情况（规范 §3：不要静默降级）"""
        return self.code in (RUNTIME_MISSING, VERSION_TOO_OLD)


# ---------------------------------------------------------------- 纯判定逻辑

def judge_os(build: int | None) -> tuple[bool, str, str]:
    """第一层：按 Windows build 号判定。返回 (通过, code, 说明)"""
    if build is None:
        return False, NOT_WINDOWS, "当前不是 Windows 系统"
    if build >= BUILD_WIN11:
        return True, OK, "Windows 11"
    if build >= BUILD_WIN10_1803:
        return True, OK, "Windows 10（1803 或更高）"
    if build >= BUILD_WIN10_1507:
        return False, OS_TOO_OLD, "Windows 10 低于 1803，WebView2 兼容性不佳，建议升级系统"
    if build >= BUILD_WIN81:
        return False, OS_TOO_OLD, "Windows 8.1 不支持 WebView2"
    if build >= BUILD_WIN8:
        return False, OS_TOO_OLD, "Windows 8 不支持 WebView2"
    if build >= BUILD_WIN7_SP1:
        # 官方支持，但 WebView2 停在 109，现代前端可能不兼容
        return True, OK, "Windows 7 SP1（WebView2 仅到 109，功能受限）"
    return False, OS_TOO_OLD, f"系统版本过低（build {build}）"


def min_major_for(build: int | None) -> int:
    """第三层的最低主版本：Windows 7 SP1 / Server 2012 R2 是 109，其他系统 92。

    注意 Server 2012 R2 就是 build 9600（Windows 8.1 的 Server SKU），所以
    7601~9600 这一档都按 109 算 —— 反正 9600 在第一层就已经不通过了。
    """
    if build is not None and BUILD_WIN7_SP1 <= build <= BUILD_WIN81:
        return MIN_MAJOR_WIN7
    return MIN_MAJOR_DEFAULT


def parse_major(version: str | None) -> int | None:
    """取版本号第一段数字；无法解析返回 None"""
    if not version:
        return None
    head = str(version).strip().split(".")[0]
    try:
        return int(head)
    except ValueError:
        return None


def judge_runtime(version: str | None, min_major: int = MIN_MAJOR_DEFAULT
                  ) -> tuple[bool, str, str]:
    """第二/三层：Runtime 是否存在、版本是否达标。

    `pv` 为 0.0.0.0 或不存在 = 未安装（规范 §2 第二层）。
    """
    v = (version or "").strip()
    if not v or v == "0.0.0.0":
        return False, RUNTIME_MISSING, "未安装 WebView2 运行时"
    major = parse_major(v)
    if major is None:
        return False, RUNTIME_MISSING, f"WebView2 版本号无法识别（{v}）"
    if major < min_major:
        return False, VERSION_TOO_OLD, f"WebView2 版本过低（{v}，需要 {min_major} 或更高）"
    return True, OK, f"WebView2 {v}"


def detect(windows_build: int | None = None,
           runtime_reader: Callable[[], str | None] | None = None) -> Wv2Status:
    """三层检测总入口。

    windows_build: None 表示按本机探测（非 Windows 会得到 None）
    runtime_reader: 返回注册表里的 pv 值（None = 未安装）
    """
    if windows_build is None:
        windows_build = current_build()
    ok_os, code, os_label = judge_os(windows_build)
    if not ok_os:
        return Wv2Status(ok=False, code=code, reason=os_label,
                         build=windows_build, os_label=os_label)
    min_major = min_major_for(windows_build)
    reader = runtime_reader or read_runtime_version
    try:
        runtime = reader()
    except Exception as e:                       # noqa: BLE001 — 探测失败不能拖垮启动
        log.warning("读取 WebView2 注册表失败: %s", e)
        runtime = None
    ok_rt, code_rt, rt_label = judge_runtime(runtime, min_major)
    if not ok_rt:
        return Wv2Status(ok=False, code=code_rt, reason=rt_label,
                         build=windows_build, os_label=os_label,
                         runtime=runtime, min_major=min_major)
    return Wv2Status(ok=True, code=OK, reason=f"{os_label} · {rt_label}",
                     build=windows_build, os_label=os_label,
                     runtime=runtime, min_major=min_major)


# ---------------------------------------------------------------- Windows 探测

def current_build() -> int | None:
    """本机 Windows build 号（非 Windows 返回 None）。

    **必须用 sys.getwindowsversion().build**：platform.release() 在 Python 3.12
    之前对 Windows 11 返回 '10'（规范 §7.1）。
    """
    try:
        v = sys.getwindowsversion()             # 非 Windows 上不存在此属性
    except AttributeError:
        return None
    return int(getattr(v, "build", 0) or 0) or None


def _winreg():
    try:
        import winreg                          # type: ignore[import-not-found]
        return winreg
    except ImportError:
        return None


def read_runtime_version() -> str | None:
    """读 WebView2 的 pv 值；三个位置依次尝试（规范 §2 第二层）。

    每个位置再分别用「默认视图 / 64 位视图 / 32 位视图」试一遍 —— 在 64 位进程里
    读 WOW6432Node 与在 32 位进程里读标准路径，视图标志不同，多试一次最稳。
    """
    winreg = _winreg()
    if winreg is None:
        return None
    views = [0]
    for name in ("KEY_WOW64_64KEY", "KEY_WOW64_32KEY"):
        flag = getattr(winreg, name, 0)
        if flag:
            views.append(flag)

    locations = [(winreg.HKEY_LOCAL_MACHINE, p) for p in REG_PATHS_HKLM]
    locations.append((winreg.HKEY_CURRENT_USER, REG_PATH_HKCU))

    for hive, path in locations:
        for view in views:
            try:
                with winreg.OpenKey(hive, path, 0,
                                    winreg.KEY_READ | view) as k:
                    pv, _ = winreg.QueryValueEx(k, "pv")
            except OSError:
                continue
            pv = str(pv or "").strip()
            if pv and pv != "0.0.0.0":
                return pv
            # pv=0.0.0.0 说明这一处只是占位（未安装），继续找下一处
    return None


def arch_note() -> str:
    """架构说明（规范 §7.2）：ARM64 需要额外注册表路径，当前未覆盖。

    绝大多数用户是 x86/x64，所以这里只做提示，不阻断。
    """
    try:
        import platform
        m = platform.machine().lower()
    except Exception:                            # noqa: BLE001
        return ""
    if m in ("arm64", "aarch64"):
        return "当前为 ARM64 设备，WebView2 检测可能不完整（如需可手动指定 transport=webview）"
    return ""


def summary() -> str:
    """一行摘要，给日志/面板显示"""
    st = detect()
    return (f"{st.code} | {st.reason} | build={st.build} runtime={st.runtime} "
            f"min={st.min_major}")
