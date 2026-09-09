# 子进程托管 / 后端别名 测试
import shutil
import tempfile
import time
import unittest
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from astercore.core.process_mgr import ProcSpec, ProcessManager


class ProcessManagerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_start_stop_python(self):
        # 托管一个短命 python 进程（sleep 60）
        spec = ProcSpec(name="t-proc", executable=sys.executable,
                        args=["-c", "import time; time.sleep(60)"],
                        log_file=self.tmp / "p.log")
        mgr = ProcessManager(spec)
        ok, msg = mgr.start()
        self.assertTrue(ok, msg)
        self.assertTrue(mgr.is_alive())
        mgr.stop()
        self.assertFalse(mgr.is_alive())

    def test_missing_executable(self):
        spec = ProcSpec(name="t-none", executable="/nonexistent/xx")
        mgr = ProcessManager(spec)
        ok, msg = mgr.start()
        self.assertFalse(ok)
        self.assertIn("未找到", msg)

    def test_poll_crash(self):
        spec = ProcSpec(name="t-crash", executable=sys.executable,
                        args=["-c", "import time; time.sleep(0.2)"],
                        auto_restart=False)
        mgr = ProcessManager(spec)
        mgr.start()
        time.sleep(0.8)  # 进程自己退出
        rc = mgr.poll_crash()
        self.assertIsNotNone(rc)
        self.assertEqual(mgr.crash_count, 1)


class BackendAliasTest(unittest.TestCase):
    def test_lagrange_alias(self):
        from astercore.backends.onebot import OneBotV11Backend  # noqa: F401
        from astercore.core.backend import BackendConfig, get_registry
        reg = get_registry()
        self.assertIn("lagrange", reg.names())
        self.assertIn("onebot", reg.names())
        b = reg.create("lagrange", BackendConfig())
        self.assertEqual(b.name, "onebot")  # 复用实现


if __name__ == "__main__":
    unittest.main()
