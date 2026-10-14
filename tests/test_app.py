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


class WindowCloseActionTest(unittest.TestCase):
    """点 ✕ 的默认动作：默认每次询问、写进去要能持久化、非法值要拒。

    用户要求：点 ✕ 弹窗里选"关闭窗口 / 退出程序"，勾了"记住选择"就固定下来，
    之后到「设置 → 窗口与关闭行为」改 —— 两处读写的就是这一份值。
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_default_is_ask(self):
        st = AppState(self.tmp)
        st.load()
        self.assertEqual(st.window_config()["close_action"], "ask")

    def test_set_and_persist(self):
        st = AppState(self.tmp)
        st.load()
        for act in ("tray", "quit", "ask"):
            ok, err = st.set_close_action(act)
            self.assertTrue(ok, err)
            st2 = AppState(self.tmp)
            st2.load()
            self.assertEqual(st2.window_config()["close_action"], act, "要落盘到 runtime.json")

    def test_rejects_unknown(self):
        st = AppState(self.tmp)
        st.load()
        ok, err = st.set_close_action("rm -rf")
        self.assertFalse(ok)
        self.assertIn("关闭行为", err)
        self.assertEqual(st.window_config()["close_action"], "ask", "非法值不能改到状态")

    def test_broken_value_in_file_falls_back(self):
        st = AppState(self.tmp)
        st.load()
        st.data["window"] = {"close_action": "????"}
        self.assertEqual(st.window_config()["close_action"], "ask")


if __name__ == "__main__":
    unittest.main()
