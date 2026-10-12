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
