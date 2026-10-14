# 原生插件（ctypes）测试 —— 依赖编译产物 libnap_hello.so/.dll，缺失则 skip
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from astercore.native import (NativePlugin, check_platform_lib,
                              expected_lib_format, lib_format, resolve_symbol,
                              symbol_candidates)
from astercore.core.models import Event, seg_text

LIB = (Path(__file__).resolve().parent.parent /
       "plugin-sdk/examples/c-sample")
ROOT = Path(__file__).resolve().parent.parent
# 只认本平台的产物名：Windows 是 nap_hello.dll，其余是 libnap_hello.so。
# 不能"找不到就退回另一个"：源目录里躺着 Linux 编出来的 libnap_hello.so 时，
# Windows 的 ctypes.CDLL 不抛异常，而是让系统加载器弹模态框
# （"python.exe - 损坏的映像 … 0xc000012f"），整轮测试卡在对话框上等人点确定。
# 本平台没有产物 → skip，这是正确行为（Windows 上确实没法跑 Linux 的 .so）。
LIB_NAMES = ("nap_hello.dll",) if sys.platform == "win32" else ("libnap_hello.so",)
for name in LIB_NAMES:
    if (LIB / name).exists():
        LIB_PATH = LIB / name
        break
else:
    LIB_PATH = None


@unittest.skipIf(LIB_PATH is None, "未编译 C 示例插件（见 plugin-sdk/examples/c-sample）")
class NativeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.seen = []

        def cb(req):
            cls.seen.append(req)
            return {"ok": True}

        cls.np = NativePlugin(LIB_PATH, cb)
        cls.np.init({})

    @classmethod
    def tearDownClass(cls):
        cls.np.unload()

    def test_meta(self):
        self.assertEqual(self.np.name(), "nap_hello")
        self.assertEqual(self.np.version(), "0.1.0")

    def test_hit_keyword(self):
        ev = Event(type="message", account_id="1", platform="t", time=0,
                   user_id=1, group_id=98765, self_id="1", message_type="group",
                   raw="你好", segments=[seg_text("你好")])
        self.assertTrue(self.np.handle_event(ev))
        self.assertTrue(self.seen)
        req = self.seen[-1]
        self.assertEqual(req["action"], "send_group")

    def test_pass_non_keyword(self):
        ev = Event(type="message", account_id="1", platform="t", time=0,
                   user_id=1, group_id=2, self_id="1", message_type="group",
                   raw="随便聊聊", segments=[seg_text("随便聊聊")])
        self.assertFalse(self.np.handle_event(ev))


class NativeLibPlatformGuardTest(unittest.TestCase):
    """加载前必须按平台校验库格式（否则 Windows 会弹模态框卡死）。

    回归背景：Linux 编的 .so 在 Windows 上走到 ctypes.CDLL 时，系统加载器
    不抛异常而是弹"损坏的映像"对话框（0xc000012f），测试与面板一起卡住。
    所以这里锁两件事：
      · 自动挑产物必须挑到本平台格式（挑错就已经晚了）
      · 格式不符时抛普通 RuntimeError，绝不把控制权交给系统加载器
    """

    def test_discovered_lib_matches_platform(self):
        if LIB_PATH is None:
            self.skipTest("未编译 C 示例插件（见 plugin-sdk/examples/c-sample）")
        self.assertEqual(
            lib_format(LIB_PATH), expected_lib_format(),
            f"挑中的示例产物 {LIB_PATH.name} 与当前平台格式不符，"
            f"ctypes 加载会弹系统对话框而不是报错")

    def test_foreign_format_rejected_before_load(self):
        """另一平台的库 → 清晰异常（不是系统对话框、不是加载器崩溃）"""
        want = expected_lib_format()
        foreign, name = ((b"\x7fELF", "libnap_hello.so") if want == "pe"
                         else (b"MZ", "nap_hello.dll"))
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / name
            p.write_bytes(foreign + b"\x00" * 32)
            with self.assertRaises(RuntimeError) as cm:
                NativePlugin(p, lambda req: {"ok": True}).load()
            self.assertIn("只能加载", str(cm.exception))

    def test_missing_lib_reports_missing(self):
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(FileNotFoundError):
                check_platform_lib(Path(td) / "nap_hello.dll")


class NativeToolingTest(unittest.TestCase):
    """导出校验脚本（`plugin-sdk/tools/check_dll_exports.py`）—— C/C++/Rust 插件都用它。

    这些守卫原先跟着易语言 SDK 的测试放；易语言那一支已移除（只有 32 位工具链），
    但**脚本本身是语言无关的**，所以守卫搬到这里继续看着。
    """

    @classmethod
    def setUpClass(cls):
        import importlib.util
        import re
        cls.checker = ROOT / "plugin-sdk" / "tools" / "check_dll_exports.py"
        spec = importlib.util.spec_from_file_location("check_dll_exports", cls.checker)
        cls.chk = importlib.util.module_from_spec(spec)
        sys.modules["check_dll_exports"] = cls.chk
        spec.loader.exec_module(cls.chk)
        header = (ROOT / "plugin-sdk" / "nap_plugin.h").read_text(encoding="utf-8")
        cls.header_names = set(re.findall(
            r"NAP_EXPORT\s+[\w\s\*]+?\s+(nap_plugin_\w+)\s*\(", header))

    def test_checker_covers_every_header_export(self):
        """校验脚本的必查清单要覆盖头文件里的每个 NAP_EXPORT（漏一个就等于放过）"""
        listed = set(self.chk.REQUIRED) | set(self.chk.OPTIONAL)
        self.assertTrue(self.header_names, "头文件里没解析出导出名？")
        self.assertEqual(self.header_names - listed, set(),
                         "校验脚本没覆盖头文件里的导出")
        self.assertIn("nap_plugin_reload", self.chk.OPTIONAL, "reload 是可选导出")

    def test_candidate_list_matches_host(self):
        """离线/独立运行时用的候选名，必须与宿主 native.symbol_candidates 完全一致"""
        for name in sorted(self.header_names):
            self.assertEqual(self.chk.candidates(name), symbol_candidates(name),
                             f"{name} 的候选名与宿主不一致（会出现在宿主里找不到、这里却通过）")

    def test_describe_reports_decorated_names(self):
        """被修饰的导出名要能认出来，并把"实际导出名"报给用户"""
        class Fake:
            pass

        lib = Fake()
        lib._nap_plugin_abi_version = lambda: 1
        lib.nap_plugin_name = lambda: b"my_plugin"
        lib._nap_plugin_version = lambda: b"0.1.0"
        lib.nap_plugin_init = lambda *a: 0
        lib._nap_plugin_on_event = lambda *a: 0
        lib.nap_plugin_unload = lambda: None
        info = self.chk.describe(lib)
        self.assertEqual(info["missing"], [], f"不该报缺失：{info}")
        self.assertEqual(info["values"]["abi_version"], 1)
        self.assertEqual(info["values"]["name"], "my_plugin")
        self.assertEqual(info["values"]["version"], "0.1.0")
        self.assertEqual(info["exports"]["nap_plugin_on_event"], "_nap_plugin_on_event")

    def test_describe_reports_missing(self):
        class Fake:
            pass

        info = self.chk.describe(Fake())
        self.assertIn("nap_plugin_init", info["missing"])
        self.assertEqual(info["exports"]["nap_plugin_name"], "")


if __name__ == "__main__":
    unittest.main()
