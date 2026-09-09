# AccountManager 测试（null 后端，离线）
import asyncio
import tempfile
import unittest
from pathlib import Path

from astercore.core.manager import AccountConfig, AccountManager
from astercore import plugins as pkg


def _run(coro, loop=None):
    if loop is None:
        return asyncio.get_event_loop().run_until_complete(coro)
    return asyncio.run_coroutine_threadsafe(coro, loop).result(timeout=10)


class ManagerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.acct_dir = Path(self.tmp.name) / "accounts"
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.mgr = AccountManager(self.acct_dir,
                                  plugin_dir=Path(pkg.__file__).parent,
                                  data_root=Path(self.tmp.name) / "data")

    def tearDown(self):
        try:
            _run(self.mgr.stop_all())
        finally:
            self.loop.close()
            self.tmp.cleanup()

    def test_save_and_scan(self):
        cfg = AccountConfig(account_id="10001", display_name="依星",
                            backend_name="null")
        self.mgr.save_config(cfg)
        accts = self.mgr.scan()
        self.assertEqual(len(accts), 1)
        self.assertEqual(accts[0]["display_name"], "依星")
        self.assertFalse(accts[0]["running"])

    def test_start_stop_null(self):
        self.mgr.save_config(AccountConfig("20001", backend_name="null"))
        rt = _run(self.mgr.start("20001"))
        self.assertTrue(rt.backend.running)
        self.assertEqual(len(rt.list_plugins()), 1)  # demo 插件
        accts = self.mgr.scan()
        self.assertTrue(accts[0]["running"])
        _run(self.mgr.stop("20001"))
        self.assertFalse(_run(self.mgr.start("20001")).backend.running is False)

    def test_missing_account(self):
        with self.assertRaises(KeyError):
            _run(self.mgr.start("nope"))


if __name__ == "__main__":
    unittest.main()
