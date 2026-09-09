# 原生插件（ctypes）测试 —— 依赖编译产物 libnap_hello.so/.dll，缺失则 skip
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from astercore.native import NativePlugin
from astercore.core.models import Event, seg_text

LIB = (Path(__file__).resolve().parent.parent /
       "plugin-sdk/examples/c-sample")
for name in ("libnap_hello.so", "nap_hello.dll"):
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


if __name__ == "__main__":
    unittest.main()
