# -*- coding: utf-8 -*-
# 桌面模式 / 进程内 RPC 测试（施工手册 §1.2 §1.4 §2.1 §3.1 §4.1 §8.1 §8.2）
#
# 说明：本机（CI/Linux）没有 pywebview，所以这里**只桩掉 GUI 层**（webview.start 立刻返回），
# 走的是**真实的启动路径**（launch.amain → 真实 AccountManager → 真实 Flask 路由），
# 不 mock 业务代码。这样"桌面模式下到底有没有占端口"这类问题才能被测出来。
import asyncio
import json
import socket
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from astercore.app import AppState, validate_web_cfg
from astercore.core.manager import AccountManager
from astercore.rpc import RpcHub, build_hub, is_trusted, mark_trusted
from astercore.web.server import ManagerWebPanel


# ---------------------------------------------------------------- 工具

class _FakeWindow:
    def __init__(self, kwargs):
        self.kwargs = kwargs
        self.hidden = False
        self.destroyed = False
        self.events = types.SimpleNamespace(closing=[], loaded=[])

    def hide(self):
        self.hidden = True

    def show(self):
        self.hidden = False

    def destroy(self):
        self.destroyed = True


class _FakeWebview:
    """最小 pywebview 替身：记录 create_window 参数，start() 触发回调后立刻返回"""

    def __init__(self, on_start=None):
        self.windows = []
        self.started = 0
        self._on_start = on_start

    def create_window(self, title, url, **kw):
        w = _FakeWindow({"title": title, "url": url, **kw})
        self.windows.append(w)
        return w

    def start(self, **kw):
        self.started += 1
        if self._on_start:
            self._on_start(self)
        # 真实 pywebview 会阻塞到窗口关闭；这里立刻返回，让测试能继续


def _install_fake_webview(on_start=None) -> _FakeWebview:
    fake = _FakeWebview(on_start=on_start)
    mod = types.ModuleType("webview")
    mod.create_window = fake.create_window
    mod.start = fake.start
    sys.modules["webview"] = mod
    return fake


def _port_in_use(host: str, port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.4)
        try:
            s.connect((host, port))
            return True
        except OSError:
            return False


def _panel(tmp: Path, password: str | None = None):
    accounts = tmp / "accounts"
    accounts.mkdir(parents=True, exist_ok=True)
    mgr = AccountManager(accounts, plugin_dir=tmp / "plugins", data_root=tmp / "data")
    panel = ManagerWebPanel(mgr, loop_provider=lambda: None,
                            auth_file=tmp / "web_auth.json")
    if password:
        panel.auth.set_password(password)
        panel.auth.set_mode("password")
    return panel, mgr


# ---------------------------------------------------------------- RPC 中枢

class RpcHubTest(unittest.TestCase):

    def test_unknown_method_returns_error_not_raise(self):
        hub = RpcHub()
        r = hub.dispatch("nope.method", {})
        self.assertFalse(r["ok"])
        self.assertIn("未知方法", r["error"])

    def test_handler_exception_is_converted(self):
        """中枢必须兜住一切：异常直接抛给前端只会变成一个没上下文的 rejected promise"""
        hub = RpcHub()

        def boom(p):
            raise ValueError("炸了")
        hub.register("t.boom", boom)
        r = hub.dispatch("t.boom", {})
        self.assertFalse(r["ok"])
        self.assertIn("ValueError", r["error"])
        self.assertIn("炸了", r["error"])

    def test_register_rejects_underscore_and_semantic_result(self):
        hub = RpcHub()
        with self.assertRaises(ValueError):
            hub.register("_secret", lambda p: {})
        hub.register("t.echo", lambda p: {"v": p.get("v")})
        r = hub.dispatch("t.echo", {"v": 1})
        self.assertEqual(r, {"ok": True, "data": {"v": 1}})

    def test_web_test_port(self):
        """语义方法的返回值统一包成 {ok, data} —— 前端只认一种形状"""
        hub = build_hub()
        free = _free_port()
        r = hub.dispatch("web.test_port", {"port": free})
        self.assertTrue(r["ok"])
        self.assertTrue(r["data"]["available"])
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            s.listen(1)
            busy = s.getsockname()[1]
            r = hub.dispatch("web.test_port", {"port": busy})
            self.assertTrue(r["ok"], "端口被占是正常回答，不是调用失败")
            self.assertFalse(r["data"]["available"], "被监听的端口必须报不可用")
            self.assertIn("reason", r["data"])

    def test_web_test_port_rejects_bad_input(self):
        hub = build_hub()
        for bad in ("abc", 0, 70000, None):
            r = hub.dispatch("web.test_port", {"port": bad})
            self.assertFalse(r["ok"], f"{bad!r} 应被拒绝")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# ---------------------------------------------------------------- 进程内 RPC（js_api 那一侧）

class InlineRpcTest(unittest.TestCase):
    """手册 §1.4 验收：同一 RPC 在 js_api 和 HTTP 下返回一致；§4.1：js_api 不鉴权"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_inline_call_matches_http(self):
        panel, _ = _panel(self.base)
        hub = build_hub()
        hub.bind_panel(panel)
        via_rpc = hub.dispatch("/api/accounts")
        with panel.app.test_client() as c:
            via_http = json.loads(c.get("/api/accounts").get_data(as_text=True))
        self.assertTrue(via_rpc["ok"])
        self.assertEqual(via_rpc.get("data"), via_http.get("data"),
                         "两条通道必须返回同一份数据（单一 dispatch 的意义）")

    def test_inline_bypasses_password_but_http_does_not(self):
        """桌面窗口里不该要用户输密码；浏览器/外网访问必须输"""
        panel, _ = _panel(self.base, password="s3cret")
        hub = build_hub()
        hub.bind_panel(panel)

        self.assertTrue(hub.dispatch("/api/accounts")["ok"],
                        "进程内 RPC 是可信调用，不该被鉴权挡住")

        with panel.app.test_client() as c:
            r = c.get("/api/accounts")
            self.assertEqual(r.status_code, 401)
            body = json.loads(r.get_data(as_text=True))
            self.assertFalse(body["ok"])
            # 输对密码后 HTTP 才通
            c.post("/api/login", json={"password": "s3cret"})
            self.assertTrue(json.loads(c.get("/api/accounts").get_data(as_text=True))["ok"])

    def test_trusted_flag_does_not_leak(self):
        panel, _ = _panel(self.base)
        hub = build_hub()
        hub.bind_panel(panel)
        self.assertFalse(is_trusted(), "初始不该是可信状态")
        hub.dispatch("/api/accounts")
        self.assertFalse(is_trusted(), "调用结束后必须清掉可信标记（否则后续请求全放行）")

    def test_inline_keeps_http_status_semantics(self):
        panel, _ = _panel(self.base)
        hub = build_hub()
        hub.bind_panel(panel)
        r = hub.dispatch("/api/does-not-exist")
        self.assertFalse(r.get("ok"), "404 之类的失败必须如实反映成 ok=false")
        self.assertEqual(r.get("status"), 404)


# ---------------------------------------------------------------- Web 配置规则

class WebConfigRuleTest(unittest.TestCase):
    """手册 §3.1/§3.2/§4.3：默认关、默认只听回环、对外必须设密码"""

    def test_defaults_are_safe(self):
        with tempfile.TemporaryDirectory() as td:
            st = AppState(Path(td))
            st.load()
            cfg = st.web_config()
            self.assertFalse(cfg["enabled"], "Web 服务默认必须关闭（netstat 应无监听）")
            self.assertEqual(cfg["host"], "127.0.0.1")

    def test_external_host_requires_password(self):
        err = validate_web_cfg({"enabled": True, "host": "0.0.0.0", "port": 18750},
                               auth_mode="none")
        self.assertIsNotNone(err)
        self.assertIn("密码", err)
        self.assertIsNone(validate_web_cfg({"enabled": True, "host": "0.0.0.0",
                                            "port": 18750}, auth_mode="password"))
        # 关着的时候不校验（不碍着用户先存配置）
        self.assertIsNone(validate_web_cfg({"enabled": False, "host": "0.0.0.0",
                                            "port": 18750}, auth_mode="none"))

    def test_set_web_config_rejects_and_does_not_persist(self):
        with tempfile.TemporaryDirectory() as td:
            st = AppState(Path(td))
            st.load()
            ok, err, cfg = st.set_web_config({"enabled": True, "host": "0.0.0.0"},
                                             auth_mode="none")
            self.assertFalse(ok)
            self.assertIn("密码", err)
            self.assertFalse(st.web_config()["enabled"], "校验失败不得写盘")
            ok, err, cfg = st.set_web_config({"enabled": True, "host": "0.0.0.0",
                                              "port": 18750}, auth_mode="password")
            self.assertTrue(ok, err)
            reloaded = AppState(Path(td))
            reloaded.load()
            self.assertTrue(reloaded.web_config()["enabled"], "成功时应当落盘")

    def test_save_is_atomic(self):
        """手册 §5.2：写入过程中挂掉也不能损坏原配置"""
        with tempfile.TemporaryDirectory() as td:
            st = AppState(Path(td))
            st.load()
            st.set_backend("null", ack_risk=True)
            target = Path(td) / "runtime.json"
            self.assertTrue(target.exists())
            self.assertFalse(target.with_suffix(".json.tmp").exists(),
                             "原子写入不该留下 .tmp")
            data = json.loads(target.read_text(encoding="utf-8"))
            self.assertEqual(data["backend_mode"], "null")


# ---------------------------------------------------------------- 桌面壳行为

class ShellBehaviourTest(unittest.TestCase):
    """手册 §8.1 关窗隐藏到托盘 / §2.1 开窗时机 / §1.1 js_api 暴露"""

    def test_closing_hides_to_tray(self):
        from astercore.shell import ShellApp
        app = ShellApp("", hub=build_hub(), tray=True)
        app._window = _FakeWindow({})
        with mock.patch.object(ShellApp, "tray_available", staticmethod(lambda: True)):
            self.assertFalse(app._on_closing(), "有托盘时应阻止关闭")
            self.assertTrue(app._window.hidden, "应把窗口隐藏起来")

    def test_closing_exits_when_quitting(self):
        from astercore.shell import ShellApp
        app = ShellApp("", hub=build_hub(), tray=True)
        app._window = _FakeWindow({})
        app._quitting = True
        self.assertTrue(app._on_closing(), "点了托盘退出就必须真的关掉")

    def test_closing_without_tray_lets_close(self):
        """没有托盘还拦截关闭 = 用户关不掉又找不到图标"""
        from astercore.shell import ShellApp
        app = ShellApp("", hub=build_hub(), tray=True)
        app._window = _FakeWindow({})
        with mock.patch.object(ShellApp, "tray_available", staticmethod(lambda: False)):
            self.assertTrue(app._on_closing())

    def test_js_api_exposes_only_rpc(self):
        from astercore.shell import JsApi
        api = JsApi(build_hub())
        public = [n for n in dir(api) if not n.startswith("_") and callable(getattr(api, n))]
        self.assertEqual(public, ["rpc"],
                         "pywebview 只暴露不以 _ 开始的公开方法，内部状态不能是公开可调用成员")
        self.assertTrue(api.rpc("web.test_port", {"port": _free_port()})["data"]["available"])

    def test_target_prefers_local_file(self):
        from astercore.shell import ShellApp
        with tempfile.TemporaryDirectory() as td:
            html = Path(td) / "index.html"
            html.write_text("<html></html>", encoding="utf-8")
            app = ShellApp("http://127.0.0.1:18750", hub=build_hub(), html_path=html)
            self.assertTrue(app.target().startswith("file:"),
                            "有本地面板文件时应直接加载文件（file://），不依赖端口")

    def test_ready_callback_runs_once_off_thread(self):
        from astercore.shell import ShellApp
        calls = []
        app = ShellApp("", hub=build_hub(), on_ready=lambda: calls.append(1))
        app._on_loaded()
        app._on_loaded()          # loaded 可能触发多次，重活只能跑一次
        import time
        for _ in range(50):
            if calls:
                break
            time.sleep(0.02)
        self.assertEqual(calls, [1])


# ---------------------------------------------------------------- 单实例锁

class SingleInstanceTest(unittest.TestCase):

    def test_second_acquire_fails_and_release_frees(self):
        from astercore.singleinstance import acquire
        import uuid
        name = f"astercore-test-{uuid.uuid4().hex}.lock"
        first = acquire(name)
        self.assertIsNotNone(first)
        try:
            self.assertIsNone(acquire(name), "同一把锁第二次必须拿不到（否则会开两个实例）")
        finally:
            first.release()
        again = acquire(name)
        self.assertIsNotNone(again, "释放后应能重新拿到")
        again.release()


# ---------------------------------------------------------------- 真实启动路径（桌面模式）

class DesktopLaunchTest(unittest.TestCase):
    """走**真实** launch.amain：只桩掉 pywebview，业务与路由都是真的"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.observed: dict = {}

    def tearDown(self):
        self.tmp.cleanup()
        sys.modules.pop("webview", None)

    def _run(self, extra_argv=None):
        from astercore.launch import _build_parser, amain

        def on_start(fake):
            # 这一刻"程序正在运行" —— 检查有没有多余的监听端口
            self.observed["listener"] = _port_in_use("127.0.0.1", 18750)
            self.observed["window"] = fake.windows[0] if fake.windows else None

        _install_fake_webview(on_start=on_start)
        argv = ["--yes", "--no-tray", "--data-dir", str(self.base / "data"),
                "--accounts-dir", str(self.base / "accounts"),
                "--plugins-dir", str(self.base / "plugins")]
        args = _build_parser().parse_args(argv + (extra_argv or []))
        with mock.patch("astercore.singleinstance.acquire",
                        lambda *a, **k: None):        # 锁由专门的测试覆盖
            rc = asyncio.run(amain(args))
        return rc

    def test_desktop_mode_js_api_no_listener(self):
        rc = self._run()
        self.assertEqual(rc, 0)
        win = self.observed.get("window")
        self.assertIsNotNone(win, "桌面模式必须开出窗口")
        self.assertIsNotNone(win.kwargs.get("js_api"),
                             "窗口必须挂 js_api —— 否则前端只能走 HTTP")
        self.assertTrue(str(win.kwargs.get("url", "")).startswith("file:"),
                        f"窗口应加载本地面板文件，实际: {win.kwargs.get('url')}")
        self.assertEqual(win.kwargs.get("confirm_close"), False)
        self.assertFalse(self.observed.get("listener"),
                         "桌面模式默认不得启动 Web 服务（手册 §3.1：netstat 应无监听）")

    def test_explicit_port_still_serves_http(self):
        """显式给 --port 时必须真的起 HTTP（tools/e2e_frozen.py 依赖这个行为）"""
        port = _free_port()
        self._run(["--port", str(port), "--no-browser"])
        poll = 0
        while poll < 40 and not _port_in_use("127.0.0.1", port):
            import time
            time.sleep(0.05)
            poll += 1
        self.assertTrue(_port_in_use("127.0.0.1", port),
                        "给了 --port 就应该监听它（否则打包验收会失败）")


if __name__ == "__main__":
    unittest.main()
