# 插件热重载测试：.py 源码修改 → reload_plugins(name) 即时生效
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

SRC = Path(__file__).resolve().parent.parent / "src/astercore/plugins/demo_hello.py"


@unittest.skipUnless(SRC.exists(), "缺少 demo_hello")
class HotReloadTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.pdir = self.tmp / "plugins"
        self.pdir.mkdir()
        shutil.copy(SRC, self.pdir / "demo_hello.py")
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.rt = AccountRuntime(account_id="x", data_dir=self.tmp / "data",
                                 backend=NullBackend(BackendConfig()),
                                 plugin_dir=self.pdir)

    def tearDown(self):
        try:
            self.loop.run_until_complete(self.rt.stop())
        finally:
            self.loop.close()
            shutil.rmtree(self.tmp, ignore_errors=True)

    def _ev(self, raw: str) -> Event:
        return Event(type="message", account_id="x", platform="t", time=0,
                     user_id=1, group_id=1, self_id="x", message_type="group",
                     raw=raw, segments=[seg_text(raw)])

    def test_reload_changes_behavior(self):
        run = self.loop.run_until_complete
        run(self.rt.start())
        self.assertTrue(run(self.rt.bus.dispatch(self._ev("你好"))) == "handled")
        # 改源码关键词 → "数据线"
        src = (self.pdir / "demo_hello.py").read_text()
        src = src.replace('_KEYWORDS = ("你好", "hello", "hi", "在吗")',
                          '_KEYWORDS = ("数据线",)')
        (self.pdir / "demo_hello.py").write_text(src)
        run(self.rt.reload_plugins("demo_hello"))
        self.assertEqual(run(self.rt.bus.dispatch(self._ev("你好"))), "passed")
        self.assertEqual(run(self.rt.bus.dispatch(self._ev("数据线"))), "handled")


if __name__ == "__main__":
    unittest.main()
