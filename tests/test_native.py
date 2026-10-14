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


if __name__ == "__main__":
    unittest.main()
