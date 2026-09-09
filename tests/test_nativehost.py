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
        # auto_restart=False：崩溃后不再拉起，调用抛 HostCrashError
        crash = NativeHostProxy(LIB_PATH, lambda req: {"ok": True},
                                auto_restart=False)
        crash.init({})
        os.kill(crash._proc.pid, signal.SIGKILL)
        # 轮询等读线程感知退出
        deadline = time.time() + 3
        while crash.exit_code is None and time.time() < deadline:
            time.sleep(0.1)
        self.assertIsNotNone(crash.exit_code)
        with self.assertRaises(HostCrashError):
            crash.handle_event({"type": "message", "data": {}})

    def test_auto_restart(self):
        import asyncio
        import os
        import signal

        async def _run():
            seen = []

            async def ac(req):
                seen.append(req)
                return {"ok": True}
            host = NativeHostProxy(LIB_PATH, ac,
                                   loop=asyncio.get_running_loop())
            host.init({})
            ev = Event(type="message", account_id="1", platform="t", time=0,
                       user_id=1, group_id=98765, self_id="1",
                       message_type="group", raw="你好",
                       segments=[seg_text("你好")])
            self.assertTrue(await host.handle_event_async(ev.to_dict()))
            os.kill(host._proc.pid, signal.SIGKILL)
            # 轮询等自动重启完成（退避 0.5s 首次重试）
            deadline = asyncio.get_running_loop().time() + 5
            while host.crash_count < 1 and asyncio.get_running_loop().time() < deadline:
                await asyncio.sleep(0.1)
            self.assertGreaterEqual(host.crash_count, 1)
            self.assertTrue(await host.handle_event_async(ev.to_dict()))
            host.stop()

        asyncio.run(_run())


if __name__ == "__main__":
    unittest.main()
