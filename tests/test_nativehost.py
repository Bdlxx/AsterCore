# plugin-host 子进程隔离测试（需要编译好的 C 示例）
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from astercore.core.models import Event, seg_text
from astercore.nativehost import HostCrashError, NativeHostProxy

LIB = Path(__file__).resolve().parent.parent / "plugin-sdk/examples/c-sample"
for name in ("libnap_hello.so", "nap_hello.dll"):
    if (LIB / name).exists():
        LIB_PATH = LIB / name
        break
else:
    LIB_PATH = None


@unittest.skipIf(LIB_PATH is None, "未编译 C 示例插件")
class NativeHostTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.seen = []
        cls.host = NativeHostProxy(
            LIB_PATH, lambda req: (cls.seen.append(req), {"ok": True})[1])
        cls.host.init({})

    @classmethod
    def tearDownClass(cls):
        cls.host.stop()

    def test_event_roundtrip_hit(self):
        ev = Event(type="message", account_id="1", platform="t", time=0,
                   user_id=1, group_id=98765, self_id="1", message_type="group",
                   raw="你好", segments=[seg_text("你好")])
        self.assertTrue(self.host.handle_event(ev.to_dict()))
        self.assertTrue(self.seen)
        self.assertEqual(self.seen[-1]["action"], "send_group")

    def test_event_pass(self):
        ev = Event(type="message", account_id="1", platform="t", time=0,
                   user_id=1, group_id=2, self_id="1", message_type="group",
                   raw="随便", segments=[seg_text("随便")])
        self.assertFalse(self.host.handle_event(ev.to_dict()))

    def test_crash_isolation(self):
        import os
        import signal
        import time
        crash = NativeHostProxy(LIB_PATH, lambda req: {"ok": True})
        crash.init({})
        os.kill(crash._proc.pid, signal.SIGKILL)
        time.sleep(0.8)
        self.assertIsNotNone(crash.exit_code)
        with self.assertRaises(HostCrashError):
            crash.handle_event({"type": "message", "data": {}})


if __name__ == "__main__":
    unittest.main()
