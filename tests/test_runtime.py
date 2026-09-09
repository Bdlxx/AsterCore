# 账号运行时 + 插件启停 测试（null 后端，离线）
import asyncio
import tempfile
import unittest
from pathlib import Path

from astercore.backends.null import NullBackend
from astercore.core.backend import BackendConfig
from astercore.core.models import Event, seg_text
from astercore.core.runtime import AccountRuntime
from astercore import plugins as pkg_plugins


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


class RuntimeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.acct = "9999"
        data_dir = Path(self.tmp.name) / "data"
        plugin_dir = Path(pkg_plugins.__file__).parent  # 内置 demo 插件
        self.rt = AccountRuntime(
            account_id=self.acct,
            data_dir=data_dir,
            backend=NullBackend(BackendConfig(), account_id=self.acct),
            plugin_dir=plugin_dir,
        )
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)

    def tearDown(self):
        self.loop.run_until_complete(self.rt.stop())
        self.loop.close()
        self.tmp.cleanup()

    def test_start_loads_demo(self):
        _run(self.rt.start())
        names = [p["name"] for p in self.rt.list_plugins()]
        self.assertIn("demo_hello", names)

    def test_disable_then_enable(self):
        _run(self.rt.start())
        # 停用后事件不再被 demo 处理
        _run(self.rt.disable_plugin("demo_hello"))
        ev = Event(type="message", account_id=self.acct, platform="test", time=0,
                   user_id=1, group_id=2, self_id=self.acct, message_type="group",
                   raw="你好", segments=[seg_text("你好")])
        result = _run(self.rt.bus.dispatch(ev))
        self.assertEqual(result, "passed")  # 停用后无插件处理
        # 重新启用
        _run(self.rt.enable_plugin("demo_hello"))
        result2 = _run(self.rt.bus.dispatch(ev))
        self.assertEqual(result2, "handled")

    def test_action_via_null(self):
        _run(self.rt.start())
        res = _run(self.rt.action("send_group", {"group_id": 2, "message": [seg_text("x").to_dict()]}))
        self.assertTrue(res.ok)


if __name__ == "__main__":
    unittest.main()
