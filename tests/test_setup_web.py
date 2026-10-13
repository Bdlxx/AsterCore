# -*- coding: utf-8 -*-
"""首启引导页 /setup 与设置页 /settings（规格 §三）

用**真实的 AppState + Auth**（临时目录）跑，这样持久化与校验都是真的：
  三选一（napcat/internal/later）、鉴权方式与设置/修改密码、页面模式与端口校验。
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


class SetupPageTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from flask import Flask
        except ImportError:
            raise unittest.SkipTest("flask 未安装")
        from astercore.app import AppState
        from astercore.web.auth import AuthConfig
        from astercore.web.setup import add_setup_routes

        cls._tmp = tempfile.TemporaryDirectory()
        base = Path(cls._tmp.name)
        cls.state = AppState(base)
        cls.state.load()
        cls.auth = AuthConfig(base / "auth.json")
        cls.auth.load()
        app = Flask("setup")
        add_setup_routes(app, cls.state, cls.auth)
        cls.client = app.test_client()

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def _reset(self):
        """每个用例回到"未完成引导、免密"的初始态"""
        self.state.wizard_completed = False
        self.state.wizard_version = 0
        self.state.data.pop("setup_mode", None)
        self.state.save()
        self.auth.set_mode("none")
        self.auth.data["password_hash"] = ""
        self.auth.save()

    # ---------- 页面 ----------
    def test_pages_served(self):
        s = self.client.get("/setup")
        self.assertEqual(s.status_code, 200)
        self.assertIn("首次设置", s.get_data(as_text=True))
        self.assertIn("settings-page", s.get_data(as_text=True),
                      "引导页左下角要有设置入口（指向面板设置页）")
        # 设置已合并进面板 → /settings 只做跳转（兼容旧链接）
        t = self.client.get("/settings")
        self.assertEqual(t.status_code, 302)
        self.assertIn("page=settings-page", t.headers.get("Location", ""))

    def test_without_state_no_routes(self):
        from flask import Flask
        from astercore.web.setup import add_setup_routes
        app = Flask("nostate")
        add_setup_routes(app, None)
        self.assertEqual(app.test_client().get("/setup").status_code, 404)

    # ---------- 状态 ----------
    def test_state_lists_three_modes(self):
        self._reset()
        j = self.client.get("/api/setup/state").get_json()
        self.assertTrue(j["ok"])
        self.assertFalse(j["data"]["inited"])
        keys = [m["key"] for m in j["data"]["modes"]]
        self.assertEqual(keys, ["napcat", "internal", "later"])
        self.assertEqual(j["data"]["auth_mode"], "none")
        self.assertEqual(j["data"]["port"], 34567, "默认端口按规格是 34567")

    # ---------- 三选一 ----------
    def test_choose_napcat_marks_inited(self):
        self._reset()
        j = self.client.post("/api/setup/mode", json={"mode": "napcat"}).get_json()
        self.assertTrue(j["ok"], j)
        self.assertTrue(j["data"]["inited"])
        self.assertEqual(self.state.backend_mode, "napcat")
        self.assertEqual(self.state.data["setup_mode"], "napcat")

    def test_internal_requires_risk_ack(self):
        self._reset()
        r = self.client.post("/api/setup/mode", json={"mode": "internal"})
        self.assertEqual(r.status_code, 400, "内部协议必须勾风险")
        self.assertIn("风险", r.get_json()["error"])
        j = self.client.post("/api/setup/mode",
                             json={"mode": "internal", "ack_risk": True}).get_json()
        self.assertTrue(j["ok"], j)
        self.assertEqual(self.state.backend_mode, "lagrange", "internal 映射到内置协议后端")

    def test_later_keeps_backend_untouched(self):
        self._reset()
        before = self.state.backend_mode
        j = self.client.post("/api/setup/mode", json={"mode": "later"}).get_json()
        self.assertTrue(j["ok"], j)
        self.assertEqual(self.state.data["setup_mode"], "later")
        self.assertTrue(self.state.wizard_completed, "选稍后也要标记引导完成")
        self.assertEqual(self.state.backend_mode, before, "不改运行方式")

    def test_unknown_mode_rejected(self):
        self._reset()
        self.assertEqual(self.client.post("/api/setup/mode", json={"mode": "??"}).status_code, 400)

    # ---------- 安全设置 ----------
    def test_set_password_flow(self):
        self._reset()
        r = self.client.post("/api/setup/auth",
                             json={"action": "set_mode", "mode": "password",
                                   "password": "abcd1234", "password2": "abcd9999"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("不一致", r.get_json()["error"])
        r = self.client.post("/api/setup/auth",
                             json={"action": "set_mode", "mode": "password",
                                   "password": "abc", "password2": "abc"})
        self.assertEqual(r.status_code, 400, "太短要拦")
        j = self.client.post("/api/setup/auth",
                             json={"action": "set_mode", "mode": "password",
                                   "password": "abcd1234", "password2": "abcd1234"}).get_json()
        self.assertTrue(j["ok"], j)
        self.assertEqual(self.auth.mode, "password")
        self.assertTrue(self.auth.password_ok("abcd1234"))
        self.assertFalse(self.auth.password_ok("wrong"))

    def test_change_password_checks_old(self):
        self._reset()
        self.client.post("/api/setup/auth",
                         json={"action": "set_mode", "mode": "password",
                               "password": "old12345", "password2": "old12345"})
        r = self.client.post("/api/setup/auth",
                             json={"action": "change_password", "old_password": "nope",
                                   "new_password": "new12345", "new_password2": "new12345"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("原密码", r.get_json()["error"])
        r = self.client.post("/api/setup/auth",
                             json={"action": "change_password", "old_password": "old12345",
                                   "new_password": "new12345", "new_password2": "new99999"})
        self.assertEqual(r.status_code, 400)
        j = self.client.post("/api/setup/auth",
                             json={"action": "change_password", "old_password": "old12345",
                                   "new_password": "new12345", "new_password2": "new12345"}).get_json()
        self.assertTrue(j["ok"], j)
        self.assertTrue(self.auth.password_ok("new12345"))

    def test_switch_back_to_none(self):
        self._reset()
        self.client.post("/api/setup/auth", json={"action": "set_mode", "mode": "password",
                                                  "password": "abcd1234", "password2": "abcd1234"})
        j = self.client.post("/api/setup/auth",
                             json={"action": "set_mode", "mode": "none"}).get_json()
        self.assertTrue(j["ok"], j)
        self.assertEqual(self.auth.mode, "none")

    def test_unknown_auth_action(self):
        self._reset()
        self.assertEqual(self.client.post("/api/setup/auth",
                                          json={"action": "nope"}).status_code, 400)

    # ---------- 页面模式与端口 ----------
    def test_window_mode(self):
        self._reset()
        j = self.client.post("/api/setup/page", json={"page_mode": "window"}).get_json()
        self.assertTrue(j["ok"], j)
        self.assertEqual(self.state.transport_mode(), "webview")

    def test_port_mode_defaults_to_34567(self):
        self._reset()
        j = self.client.post("/api/setup/page", json={"page_mode": "port"}).get_json()
        self.assertTrue(j["ok"], j)
        self.assertEqual(self.state.transport_mode(), "http")
        self.assertEqual(j["data"]["port"], 34567)
        self.assertTrue(self.state.web_config()["enabled"])
        self.assertTrue(j["data"]["restart_needed"])

    def test_port_mode_custom_and_invalid(self):
        self._reset()
        j = self.client.post("/api/setup/page",
                             json={"page_mode": "port", "port": 41234}).get_json()
        self.assertEqual(j["data"]["port"], 41234)
        for bad in ("abc", 0, 70000, -1):
            r = self.client.post("/api/setup/page", json={"page_mode": "port", "port": bad})
            self.assertEqual(r.status_code, 400, f"非法端口 {bad!r} 必须拦住")

    def test_bad_page_mode(self):
        self.assertEqual(self.client.post("/api/setup/page",
                                          json={"page_mode": "??"}).status_code, 400)


if __name__ == "__main__":
    unittest.main()
