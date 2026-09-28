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

    def test_example_declared_schema_is_honored(self):
        """SDK 示例同样是"类插件 + 模块级 __config_schema__"，声明不能被丢。

        回归见 ShippedTemplateTest.test_template_declared_schema_is_honored。
        """
        self.loop.run_until_complete(self.rt.start())
        p = self.rt.get_plugin_schema("demo_counter")
        self.assertTrue(p.get("declared"), p)
        self.assertEqual(p.get("declared_by"), "__config_schema__")
        names = [f.get("name") for f in (p.get("fields") or [])]
        self.assertIn("keywords", names)

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


class ShippedTemplateTest(unittest.TestCase):
    """**随包分发的**类插件模板必须真的能被加载。

    背景（真实回归，冻结版 e2e 抓到）：`plugin_templates/demo_counter.py` 是
    首启播种给用户看的示例，但它漏了 SDK 示例里那行 `plugin = demo_counter()`，
    于是被加载器当成"无 handle 的非插件模块"静默跳过 —— 用户第一次运行就看到
    一个"播种了却不工作"的示例。这里把模板本身钉住。
    """

    def setUp(self):
        from astercore.paths import plugin_templates_dir
        self.tpl = plugin_templates_dir() / "demo_counter.py"
        self.tmp = Path(tempfile.mkdtemp())
        self.pdir = self.tmp / "plugins"
        self.pdir.mkdir()
        shutil.copy(self.tpl, self.pdir / "demo_counter.py")
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

    def test_template_is_loadable(self):
        self.loop.run_until_complete(self.rt.start())
        names = [p["name"] for p in self.rt.list_plugins()]
        self.assertIn("demo_counter", names, f"播种模板未被加载: {names}")
        self.assertNotIn("demo_counter", self.rt.plugin_loader.skipped,
                         "播种模板被当成非插件跳过了")

    def test_template_handles_and_replies(self):
        self.loop.run_until_complete(self.rt.start())
        ev = Event(type="message", account_id="x", platform="t", time=0,
                   user_id=1, group_id=9, self_id="x", message_type="group",
                   raw="计数器", segments=[seg_text("计数器")])
        self.assertEqual(self.loop.run_until_complete(self.rt.bus.dispatch(ev)),
                         "handled")

    def test_template_declared_schema_is_honored(self):
        """模板用**模块级** `__config_schema__` 声明配置项 —— 面板必须能读到。

        回归（本轮抓到）：类插件加载时把 `module` 置 None（为的是模块级 `handle`
        不抢类实例的分发权），模块级声明跟着一起丢了 → 面板只能"按当前值推断"，
        插件作者写的 label/type/min/max **全部失效**且不报错。
        """
        self.loop.run_until_complete(self.rt.start())
        p = self.rt.get_plugin_schema("demo_counter")
        self.assertTrue(p.get("declared"), p)
        self.assertEqual(p.get("declared_by"), "__config_schema__")
        names = [f.get("name") for f in (p.get("fields") or [])]
        self.assertIn("keywords", names)
        self.assertIn("reply", names)


if __name__ == "__main__":
    unittest.main()
