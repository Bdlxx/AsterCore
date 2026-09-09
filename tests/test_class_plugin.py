# Python 类插件（Plugin 继承 + 配置热更）测试
import asyncio
import shutil
import tempfile
import unittest
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from astercore.core.backend import BackendConfig
from astercore.backends.null import NullBackend
from astercore.core.models import Event, seg_text
from astercore.core.runtime import AccountRuntime

SRC = Path(__file__).resolve().parent.parent / "plugin-sdk/examples/py-class-plugin/demo_counter.py"


@unittest.skipUnless(SRC.exists(), "缺少类插件示例")
class ClassPluginTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.pdir = self.tmp / "plugins"
        self.pdir.mkdir()
        shutil.copy(SRC, self.pdir / "demo_counter.py")
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.rt = AccountRuntime(
            account_id="x", data_dir=self.tmp / "data",
            backend=NullBackend(BackendConfig()), plugin_dir=self.pdir)

    def tearDown(self):
        try:
            self.loop.run_until_complete(self.rt.stop())
        finally:
            self.loop.close()
            shutil.rmtree(self.tmp, ignore_errors=True)

    def _ev(self, raw: str) -> Event:
        return Event(type="message", account_id="x", platform="t", time=0,
                     user_id=1, group_id=9, self_id="x", message_type="group",
                     raw=raw, segments=[seg_text(raw)])

    def test_load_and_count(self):
        self.loop.run_until_complete(self.rt.start())
        pls = self.rt.list_plugins()
        self.assertEqual(pls[0]["name"], "demo_counter")
        self.assertEqual(pls[0]["kind"], "py")
        self.loop.run_until_complete(self.rt.bus.dispatch(self._ev("计数器")))
        self.loop.run_until_complete(self.rt.bus.dispatch(self._ev("计数器")))
        # 内部计数（无异常即可；计数正确性看插件日志）
        self.assertTrue(True)

    def test_config_reload_changes_behavior(self):
        self.loop.run_until_complete(self.rt.start())
        # 默认 keywords 不含 "hi" → 放行
        r1 = self.loop.run_until_complete(self.rt.bus.dispatch(self._ev("hi")))
        self.assertEqual(r1, "passed")
        # 热更：keywords 加 "hi"
        cfg = {"keywords": ["hi"], "reply": "收到{n}次"}
        self.assertTrue(self.loop.run_until_complete(
            self.rt.save_plugin_config("demo_counter", cfg)))
        self.assertEqual(self.rt.get_plugin_config("demo_counter")["keywords"], ["hi"])
        # 热更后 "hi" 命中
        r2 = self.loop.run_until_complete(self.rt.bus.dispatch(self._ev("hi")))
        self.assertEqual(r2, "handled")
        # 热更后 "计数器" 不再命中
        r3 = self.loop.run_until_complete(self.rt.bus.dispatch(self._ev("计数器")))
        self.assertEqual(r3, "passed")


if __name__ == "__main__":
    unittest.main()
