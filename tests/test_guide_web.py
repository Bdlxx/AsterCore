# -*- coding: utf-8 -*-
"""连接引导（/guide + /api/guide/*）接口测试

不需要真网络：安装走 **file:// 本地 zip**，探测走"连不上的端口"，
验证的是我们这一层的行为（自动端口/token、只改负责的键、状态与错误码）。
"""
import json
import os
import stat
import sys
import tempfile
import time
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from astercore import components as C  # noqa: E402


def _mkzip(path: Path, files: dict[str, bytes], prefix: str = "") -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        for n, d in files.items():
            zf.writestr(prefix + n, d)
    return path


class GuideRoutesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from flask import Flask
        except ImportError:
            raise unittest.SkipTest("flask 未安装")
        from astercore.web.guide import add_guide_routes
        cls._tmp = tempfile.TemporaryDirectory()
        cls.base = Path(cls._tmp.name)
        app = Flask(__name__)
        add_guide_routes(app, base_dir=lambda: cls.base)
        cls.client = app.test_client()

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    # ---------- 页面 ----------
    def test_guide_page_served(self):
        r = self.client.get("/guide")
        self.assertEqual(r.status_code, 200)
        body = r.get_data(as_text=True)
        self.assertIn("连接引导", body)
        self.assertIn("/api/guide/install", body)

    # ---------- 状态 ----------
    def test_status_lists_components(self):
        j = self.client.get("/api/guide/status").get_json()
        self.assertTrue(j["ok"])
        self.assertTrue(j["data"]["supported"])
        self.assertEqual(set(j["data"]["components"]), {"napcat", "lagrange"})
        self.assertEqual(j["data"]["base_dir"], str(self.base))

    def test_unknown_key_is_400(self):
        r = self.client.post("/api/guide/config", json={"key": "nope"})
        self.assertEqual(r.status_code, 400)
        self.assertFalse(r.get_json()["ok"])

    # ---------- 写配置 ----------
    def test_config_autofills_ports_and_token(self):
        j = self.client.post("/api/guide/config", json={"key": "napcat", "qq": "2551736206"}).get_json()
        self.assertTrue(j["ok"], j)
        d = j["data"]
        self.assertTrue(d["access_token"])
        self.assertTrue(d["ws_url"].startswith("ws://127.0.0.1:"))
        p = Path(d["path"])
        self.assertTrue(p.is_file())
        self.assertEqual(p.name, "onebot11_2551736206.json")
        cfg = json.loads(p.read_text(encoding="utf-8"))
        ws = cfg["network"]["websocketServers"][0]
        self.assertEqual(ws["token"], d["access_token"])
        self.assertEqual(str(ws["port"]), d["ws_url"].rsplit(":", 1)[1])

    def test_config_lagrange_writes_appsettings(self):
        j = self.client.post("/api/guide/config", json={"key": "lagrange", "qq": "12345"}).get_json()
        self.assertTrue(j["ok"], j)
        cfg = json.loads(Path(j["data"]["path"]).read_text(encoding="utf-8"))
        fw = [i for i in cfg["Implementations"] if i["Type"] == "ForwardWebSocket"][0]
        self.assertEqual(fw["AccessToken"], j["data"]["access_token"])
        self.assertEqual(cfg["Account"]["Uin"], 12345)

    def test_config_keeps_user_keys(self):
        path = self.base / "components/napcat/config/onebot11_9.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"keep": {"x": 1}}), encoding="utf-8")
        self.client.post("/api/guide/config", json={"key": "napcat", "qq": "9", "ws_port": 3501})
        cfg = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(cfg["keep"], {"x": 1})
        self.assertEqual(cfg["network"]["websocketServers"][0]["port"], 3501)

    # ---------- 安装（file:// 本地包，不碰网络） ----------
    def test_install_from_local_zip(self):
        z = _mkzip(self.base / "lag.zip", {"Lagrange.OneBot.exe": b"MZ"}, prefix="Lagrange/")
        r = self.client.post("/api/guide/install",
                             json={"key": "lagrange", "url": Path(z).as_uri()})
        j = r.get_json()
        self.assertTrue(j["ok"], j)
        self.assertTrue(j["data"]["started"])
        deadline = time.time() + 8
        step = ""
        while time.time() < deadline:
            step = self.client.get("/api/guide/status").get_json()["data"]["jobs"]["lagrange"]["step"]
            if step in ("done", "failed"):
                break
            time.sleep(0.1)
        self.assertEqual(step, "done", f"安装没成功: {step}")
        self.assertTrue(C.find_executable(C.get_component("lagrange"), self.base))

    # ---------- 二维码 / 日志 ----------
    def test_qr_none_then_image(self):
        j = self.client.get("/api/guide/qr?key=lagrange").get_json()
        self.assertEqual(j["data"]["kind"], "none")
        d = self.base / "components/lagrange"
        d.mkdir(parents=True, exist_ok=True)
        (d / "qrcode.png").write_bytes(b"\x89PNG\r\n\x1a\nfake")
        j = self.client.get("/api/guide/qr?key=lagrange").get_json()
        self.assertEqual(j["data"]["kind"], "image")
        raw = self.client.get("/api/guide/qr?key=lagrange&raw=1")
        self.assertEqual(raw.status_code, 200)

    def test_log_tail(self):
        d = self.base / "components/napcat"
        d.mkdir(parents=True, exist_ok=True)
        (d / "run.log").write_text("line1\nline2\n扫码登录中\n", encoding="utf-8")
        j = self.client.get("/api/guide/log?key=napcat&limit=2").get_json()
        self.assertTrue(j["ok"])
        self.assertEqual(j["data"]["lines"], ["line2", "扫码登录中"])

    # ---------- 探测 ----------
    def test_test_connection_requires_url(self):
        r = self.client.post("/api/guide/test", json={})
        self.assertEqual(r.status_code, 400)

    def test_test_connection_reports_failure(self):
        j = self.client.post("/api/guide/test", json={"http_url": "http://127.0.0.1:1"}).get_json()
        self.assertTrue(j["ok"])
        self.assertFalse(j["data"]["ok"])
        self.assertIn("连接失败", j["data"]["msg"])

    def test_discover_shape(self):
        j = self.client.post("/api/guide/discover", json={}).get_json()
        self.assertTrue(j["ok"])
        self.assertIsInstance(j["data"]["found"], list)


class GuideHostTest(unittest.TestCase):
    """起停组件（用假组件脚本；Windows 上跳过）"""

    @classmethod
    def setUpClass(cls):
        if os.name == "nt":
            raise unittest.SkipTest("假组件是 shebang 脚本，仅 POSIX")
        try:
            from flask import Flask
        except ImportError:
            raise unittest.SkipTest("flask 未安装")
        from astercore.web.guide import add_guide_routes
        cls._tmp = tempfile.TemporaryDirectory()
        cls.base = Path(cls._tmp.name)
        comp_dir = cls.base / "components/lagrange"
        comp_dir.mkdir(parents=True)
        f = comp_dir / "Lagrange.OneBot.exe"
        f.write_text("#!/usr/bin/env python3\nimport time\ntime.sleep(60)\n", encoding="utf-8")
        f.chmod(f.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        app = Flask(__name__)
        add_guide_routes(app, base_dir=lambda: cls.base)
        cls.client = app.test_client()

    @classmethod
    def tearDownClass(cls):
        try:
            C.host_reset(C.get_component("lagrange"), cls.base)
        except Exception:
            pass
        cls._tmp.cleanup()

    def test_host_start_status_stop(self):
        j = self.client.post("/api/guide/host", json={"key": "lagrange", "write_config": True}).get_json()
        self.assertTrue(j["ok"], j)
        st = self.client.get("/api/guide/status").get_json()["data"]["components"]["lagrange"]
        self.assertTrue(st["running"], st)
        self.assertTrue(Path(self.base / "components/lagrange/appsettings.json").is_file())
        j2 = self.client.post("/api/guide/stop", json={"key": "lagrange"}).get_json()
        self.assertTrue(j2["ok"], j2)
        st2 = self.client.get("/api/guide/status").get_json()["data"]["components"]["lagrange"]
        self.assertFalse(st2["running"], st2)

    def test_start_without_component_is_409(self):
        j = self.client.post("/api/guide/host", json={"key": "napcat", "write_config": False})
        self.assertEqual(j.status_code, 409)


if __name__ == "__main__":
    unittest.main()


# --------------------------------------------------------------------------- #
# 运行方式改为「界面里选」（windowed 没有控制台可问）—— 追加在文件末尾，
# unittest discover 照样会收集（`if __name__` 只在直接执行时生效）
# --------------------------------------------------------------------------- #
class GuideModeTest(unittest.TestCase):
    """运行方式：GET 列元数据 / POST 保存（实验后端必须勾风险）"""

    @classmethod
    def setUpClass(cls):
        try:
            from flask import Flask
        except ImportError:
            raise unittest.SkipTest("flask 未安装")
        cls._tmp = tempfile.TemporaryDirectory()

        class FakeState:
            def __init__(self):
                self.backend_mode = ""
                self.ack = None

            def needs_wizard(self):
                return True

            def set_backend(self, mode, ack_risk=False):
                from astercore.app import BACKEND_MODES
                if mode not in BACKEND_MODES:
                    return False, f"未知运行方式: {mode}"
                if BACKEND_MODES[mode]["needs_ack"] and not ack_risk:
                    return False, "该模式为实验性质，需确认已知晓账号风险"
                self.backend_mode, self.ack = mode, ack_risk
                return True, "已保存"

        from astercore.web.guide import add_guide_routes
        cls.state = FakeState()
        app = Flask("guide-mode")
        add_guide_routes(app, base_dir=lambda: Path(cls._tmp.name), state=cls.state)
        cls.client = app.test_client()

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_mode_listed_with_risk_metadata(self):
        j = self.client.get("/api/guide/mode").get_json()
        self.assertTrue(j["ok"], j)
        keys = {m["key"] for m in j["data"]["modes"]}
        self.assertEqual(keys, {"napcat", "lagrange", "null"})
        lag = [m for m in j["data"]["modes"] if m["key"] == "lagrange"][0]
        self.assertTrue(lag["needs_ack"], "实验后端必须带风险确认要求")
        self.assertTrue(j["data"]["can_set"], "传了 state 就应该能在界面里改")

    def test_set_mode_requires_ack_then_saves(self):
        r = self.client.post("/api/guide/mode", json={"mode": "lagrange"})
        self.assertEqual(r.status_code, 400, "没勾风险就不该放行")
        r2 = self.client.post("/api/guide/mode", json={"mode": "lagrange", "ack_risk": True})
        self.assertTrue(r2.get_json()["ok"], r2.get_json())
        self.assertEqual(self.state.backend_mode, "lagrange")
        self.assertTrue(self.state.ack)
        self.assertEqual(self.client.get("/api/guide/mode").get_json()["data"]["mode"],
                         "lagrange")

    def test_unknown_mode_rejected(self):
        r = self.client.post("/api/guide/mode", json={"mode": "not-a-mode", "ack_risk": True})
        self.assertEqual(r.status_code, 400)

    def test_without_state_cannot_set(self):
        from flask import Flask
        from astercore.web.guide import add_guide_routes
        app = Flask("no-state")
        add_guide_routes(app, base_dir=lambda: Path(self._tmp.name))
        c = app.test_client()
        self.assertFalse(c.get("/api/guide/mode").get_json()["data"]["can_set"])
        r = c.post("/api/guide/mode", json={"mode": "napcat"})
        self.assertEqual(r.status_code, 501, "没有 state 时如实说'不支持'，而不是假装成功")


# --------------------------------------------------------------------------- #
# 一键建账号 + 账号级「连接方式」选择器
# --------------------------------------------------------------------------- #
class GuideAccountTest(unittest.TestCase):
    """用假 manager 验行为：建账号要同时把组件配置对齐、切连接方式要能重启"""

    @classmethod
    def setUpClass(cls):
        try:
            from flask import Flask
        except ImportError:
            raise unittest.SkipTest("flask 未安装")
        cls._tmp = tempfile.TemporaryDirectory()
        cls.base = Path(cls._tmp.name)
        # 假装 lagrange 组件已装（一键建账号会检查它）
        d = cls.base / "components" / "lagrange"
        d.mkdir(parents=True)
        (d / "Lagrange.OneBot.exe").write_bytes(b"MZ")

        class FakeManager:
            def __init__(self):
                from astercore.core.manager import AccountConfig
                self.AccountConfig = AccountConfig
                self.cfgs = {}
                self.actions = []
                self.running = set()

            def scan(self):
                return [{"account_id": aid, "display_name": cfg.display_name,
                         "running": aid in self.running} for aid, cfg in self.cfgs.items()]

            def get_config(self, aid):
                return self.cfgs.get(str(aid))

            def save_config(self, cfg):
                self.cfgs[cfg.account_id] = cfg
                return Path("/tmp") / cfg.account_id / "backend.json"

            async def start(self, aid):
                self.actions.append(("start", str(aid)))
                self.running.add(str(aid))

            async def restart(self, aid):
                self.actions.append(("restart", str(aid)))
                self.running.add(str(aid))

        from astercore.web.guide import add_guide_routes
        cls.mgr = FakeManager()
        app = Flask("guide-account")
        add_guide_routes(app, base_dir=lambda: cls.base, manager=cls.mgr)
        cls.client = app.test_client()

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_no_manager_is_501(self):
        from flask import Flask
        from astercore.web.guide import add_guide_routes
        app = Flask("no-mgr")
        add_guide_routes(app, base_dir=lambda: self.base)
        c = app.test_client()
        self.assertEqual(c.get("/api/guide/accounts").status_code, 501)
        self.assertEqual(c.post("/api/guide/account", json={"account_id": "1"}).status_code, 501)

    def test_backends_listed(self):
        j = self.client.get("/api/guide/accounts").get_json()
        self.assertTrue(j["ok"])
        keys = {b["key"] for b in j["data"]["backends"]}
        self.assertEqual(keys, {"onebot", "lagrange", "null"})

    def test_one_click_create_lagrange_account(self):
        """一键建账号：组件配置（端口/token）必须和账号里写的一致"""
        r = self.client.post("/api/guide/account", json={
            "account_id": "2551736206", "display_name": "羽笙", "key": "lagrange"})
        j = r.get_json()
        self.assertTrue(j["ok"], j)
        self.assertEqual(j["data"]["backend_name"], "lagrange")
        self.assertTrue(j["data"]["started"])
        cfg = self.mgr.get_config("2551736206")
        self.assertEqual(cfg.display_name, "羽笙")
        self.assertEqual(cfg.backend_name, "lagrange")
        ws_port = cfg.ws_url.rsplit(":", 1)[1]
        app_cfg = json.loads((self.base / "components/lagrange/appsettings.json")
                             .read_text(encoding="utf-8"))
        fw = [i for i in app_cfg["Implementations"] if i["Type"] == "ForwardWebSocket"][0]
        self.assertEqual(str(fw["Port"]), ws_port, "组件端口与账号 ws_url 必须一致")
        self.assertEqual(fw["AccessToken"], cfg.access_token, "token 也必须一致")
        self.assertIn(("start", "2551736206"), self.mgr.actions)

    def test_create_null_account_without_config(self):
        j = self.client.post("/api/guide/account", json={
            "account_id": "demo1", "backend_name": "null"}).get_json()
        self.assertTrue(j["ok"], j)
        self.assertEqual(self.mgr.get_config("demo1").backend_name, "null")

    def test_create_without_component_is_409(self):
        r = self.client.post("/api/guide/account", json={
            "account_id": "123456789", "key": "napcat"})     # napcat 组件没装
        self.assertEqual(r.status_code, 409)
        self.assertIn("下载并安装", r.get_json()["error"])

    def test_missing_account_id(self):
        self.assertEqual(self.client.post("/api/guide/account", json={}).status_code, 400)

    def test_set_account_backend_switches_and_restarts(self):
        self.client.post("/api/guide/account", json={"account_id": "777", "backend_name": "null"})
        self.mgr.running.add("777")
        self.mgr.actions.clear()
        j = self.client.post("/api/guide/account/777/backend",
                             json={"backend_name": "lagrange"}).get_json()
        self.assertTrue(j["ok"], j)
        self.assertEqual(self.mgr.get_config("777").backend_name, "lagrange")
        self.assertTrue(j["data"]["restarted"], "在跑的账号切方式后应当重启")
        self.assertIn(("restart", "777"), self.mgr.actions)
        ws = self.mgr.get_config("777").ws_url
        self.assertTrue(ws.startswith("ws://127.0.0.1:"), ws)

    def test_set_backend_on_stopped_account_does_not_restart(self):
        self.client.post("/api/guide/account", json={"account_id": "888", "backend_name": "null"})
        self.mgr.running.discard("888")
        self.mgr.actions.clear()
        j = self.client.post("/api/guide/account/888/backend",
                             json={"backend_name": "null"}).get_json()
        self.assertTrue(j["ok"], j)
        self.assertFalse(j["data"]["restarted"])
        self.assertEqual(self.mgr.actions, [])

    def test_set_backend_unknown_account_404(self):
        self.assertEqual(self.client.post("/api/guide/account/nope/backend",
                                          json={"backend_name": "null"}).status_code, 404)

    def test_set_backend_unknown_name_400(self):
        self.client.post("/api/guide/account", json={"account_id": "999", "backend_name": "null"})
        r = self.client.post("/api/guide/account/999/backend", json={"backend_name": "nope"})
        self.assertEqual(r.status_code, 400)

    def test_accounts_list_shape(self):
        j = self.client.get("/api/guide/accounts").get_json()
        rows = j["data"]["accounts"]
        self.assertIsInstance(rows, list)
        if rows:
            a = rows[0]
            for k in ("account_id", "display_name", "backend_name", "running", "component"):
                self.assertIn(k, a)
