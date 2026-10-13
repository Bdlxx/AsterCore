# -*- coding: utf-8 -*-
"""更新会话 / 启动钩子 / 接口（计划书 §369 第二部分）"""
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from astercore import updater as U  # noqa: E402


def _wait(pred, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.02)
    return False


class BootHookSafetyTest(unittest.TestCase):
    """**源码运行时绝不能动任何目录**（否则开发机上会误删/误回滚）"""

    def test_not_frozen_does_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            (base / U.EXE_NAME).write_bytes(b"MZ")          # 看起来像发布目录
            (base / (base.name + "_old")).mkdir() if False else None
            old = base.parent / (base.name + "_old")
            old.mkdir()
            self.assertEqual(U.try_rollback(base), "", "非打包环境不做回滚")
            self.assertFalse(U.confirm_boot(base), "非打包环境不写标记")
            self.assertFalse((base / U.BOOT_OK).exists())

    def test_apply_now_missing_script_is_false(self):
        self.assertFalse(U.apply_now("/definitely/not/here.bat"))


class SessionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name) / "app"
        self.base.mkdir()
        self._prev = (U.check, U.update_flow)

    def tearDown(self):
        U.check, U.update_flow = self._prev
        self.tmp.cleanup()

    def test_initial_status(self):
        s = U.UpdateSession("0.6.9", self.base).status()
        self.assertEqual(s["state"], "idle")
        self.assertEqual(s["current"], "0.6.9")
        self.assertFalse(s["available"])

    def test_check_up_to_date(self):
        U.check = lambda cur, timeout=20: {"available": False, "current": cur, "error": ""}
        st = U.UpdateSession("0.6.9", self.base).check()
        self.assertFalse(st["available"])
        self.assertIn("已是最新", st["message"])

    def test_check_finds_new_and_start_reaches_done(self):
        U.check = lambda cur, timeout=20: {"available": True, "version": "9.9.9", "error": ""}
        U.update_flow = lambda cur, dl, inst, on_progress=None: (
            on_progress(1, 2) if on_progress else None,
            {"ok": True, "available": True, "version": "9.9.9", "verified": True,
             "sha256": "a" * 64, "size": 10, "staged": str(self.base.parent / "app_new"),
             "script": str(self.base.parent / "apply-update.bat")})[-1]
        sess = U.UpdateSession("0.6.9", self.base)
        self.assertTrue(sess.check()["available"])
        sess.start()
        self.assertTrue(_wait(lambda: sess.status()["state"] == "done"), sess.status())
        s = sess.status()
        self.assertEqual(s["percent"], 100)
        self.assertTrue(s["verified"])
        self.assertIn("重启", s["message"])

    def test_check_error_is_reported(self):
        U.check = lambda cur, timeout=20: {"available": False, "error": "no network"}
        st = U.UpdateSession("0.6.9", self.base).check()
        self.assertEqual(st["state"], "error")
        self.assertIn("no network", st["error"])

    def test_start_failure_reports_error(self):
        U.check = lambda cur, timeout=20: {"available": True, "version": "9.9.9", "error": ""}
        U.update_flow = lambda cur, dl, inst, on_progress=None: {"ok": False, "available": True,
                                                                 "error": "sha256 不匹配"}
        sess = U.UpdateSession("0.6.9", self.base)
        sess.check()
        sess.start()
        self.assertTrue(_wait(lambda: sess.status()["state"] == "error"), sess.status())
        self.assertIn("sha256", sess.status()["error"])


class EndpointTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from flask import Flask
        except ImportError:
            raise unittest.SkipTest("flask 未安装")
        from astercore.web.appapi import add_app_routes

        cls.tmp = tempfile.TemporaryDirectory()
        cls.quit_called = []
        app = Flask("upd")
        # 面板总会传 app_state；这里给个占位，否则 add_app_routes 会整体早退
        add_app_routes(app, None, object(), quit_cb=lambda: cls.quit_called.append(1))
        cls.client = app.test_client()

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_status_endpoint(self):
        r = self.client.get("/api/app/update/status")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.get_json()["ok"])
        self.assertIn("state", r.get_json()["data"])

    def test_apply_without_ready_update_is_409(self):
        r = self.client.post("/api/app/update/apply", json={})
        self.assertEqual(r.status_code, 409, "没备好更新时不能真去替换")

    def test_check_endpoint_tolerates_offline(self):
        import astercore.updater as U2
        prev = U2.check
        U2.check = lambda cur, timeout=20: {"available": False, "current": cur, "error": ""}
        try:
            r = self.client.post("/api/app/update/check", json={})
        finally:
            U2.check = prev
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.get_json()["ok"])


if __name__ == "__main__":
    unittest.main()
