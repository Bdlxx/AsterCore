# 桌面壳启动状态测试（runtime.json 持久化 / 向导决策 / 风险确认）
import shutil
import tempfile
import unittest
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from astercore.app import AppState, BACKEND_MODES


class AppStateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_fresh_needs_wizard(self):
        st = AppState(self.tmp)
        st.load()
        self.assertTrue(st.needs_wizard())
        plan = st.startup_plan()
        self.assertTrue(plan["wizard"])

    def test_set_backend_and_persist(self):
        st = AppState(self.tmp)
        st.load()
        ok, _ = st.set_backend("napcat")
        self.assertTrue(ok)
        self.assertFalse(st.needs_wizard())
        # 新实例读取持久化
        st2 = AppState(self.tmp)
        st2.load()
        self.assertEqual(st2.backend_mode, "napcat")
        self.assertFalse(st2.needs_wizard())

    def test_lagrange_requires_ack(self):
        st = AppState(self.tmp)
        st.load()
        ok, err = st.set_backend("lagrange", ack_risk=False)
        self.assertFalse(ok)
        self.assertIn("风险", err)
        ok2, _ = st.set_backend("lagrange", ack_risk=True)
        self.assertTrue(ok2)
        self.assertTrue(st.risk_acknowledged)

    def test_unknown_mode(self):
        st = AppState(self.tmp)
        st.load()
        ok, err = st.set_backend("nosuch")
        self.assertFalse(ok)

    def test_backend_meta(self):
        self.assertIn("napcat", BACKEND_MODES)
        self.assertFalse(BACKEND_MODES["napcat"]["needs_ack"])
        self.assertTrue(BACKEND_MODES["lagrange"]["needs_ack"])


if __name__ == "__main__":
    unittest.main()
