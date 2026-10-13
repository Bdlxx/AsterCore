# -*- coding: utf-8 -*-
# 面板静态页 + Web JSON API 冒烟测试
# 背景: v0.1.2 曾因 index.html 同一 <script> 作用域里同时存在
#   `async function j` 与 `const j` → JS 解析期 SyntaxError → 整个脚本失效,
#   面板只剩静态骨架("加载中…"永不消失)。本测试防止此类问题再次上船。
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INDEX = ROOT / "src" / "astercore" / "web" / "static" / "index.html"
PANEL_JS = ROOT / "src" / "astercore" / "web" / "static" / "panel.js"

_DECL_RE = re.compile(
    r"^\s*(?:(?:async\s+)?function\s+(\w+)|(?:const|let|var)\s+(\w+)\s*=)",
)


def _script_block() -> str:
    s = INDEX.read_text(encoding="utf-8")
    m = re.search(r"<script>(.*?)</script>", s, re.S)
    assert m, "index.html 缺少 <script> 块"
    return m.group(1)


class PanelStaticTest(unittest.TestCase):
    """index.html 内联脚本的静态健全性检查"""

    def test_single_script_block(self):
        s = INDEX.read_text(encoding="utf-8")
        self.assertEqual(len(re.findall(r"<script>", s)), 1,
                         "应只有一个内联 <script>，便于整体语法校验")

    def test_no_duplicate_top_level_decls(self):
        """同一缩进深度下不允许同名声明重复（function j + const j 事故）"""
        depth_owners = {}
        for ln in _script_block().splitlines():
            indent = len(ln) - len(ln.lstrip())
            m = _DECL_RE.match(ln)
            if not m:
                continue
            name = m.group(1) or m.group(2)
            # 仅检查顶层的重复: 函数体内的重复由 node 语法检查覆盖
            if indent == 0:
                depth_owners.setdefault(name, []).append(ln.strip())
        for name, decls in depth_owners.items():
            self.assertLessEqual(len(decls), 1,
                                 f"顶层重复声明 {name}: {decls}")

    def test_panel_js_parses(self):
        """若系统有 node，用 node --check 校验整个脚本可解析(曾因重复声明
        导致整页脚本静默失效，面板永远停在静态“加载中…”)"""
        node = shutil.which("node")
        if not node:
            self.skipTest("node 不可用")
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
            f.write(_script_block())
            path = f.name
        try:
            r = subprocess.run([node, "--check", path],
                               capture_output=True, text=True)
            self.assertEqual(r.returncode, 0,
                             f"面板 JS 语法错误:\n{r.stderr}")
        finally:
            Path(path).unlink(missing_ok=True)

    def test_transport_layer_present(self):
        """传输层必须存在且按运行时选择（施工手册 §1.5）。

        桌面壳用 js_api（进程内 RPC），浏览器用 HTTP —— 同一份前端两边都能跑。
        """
        js = _script_block()
        for needle in ("const Transport", "pywebviewready", "installFetchShim",
                       "window.pywebview.api.rpc", "location.protocol !== 'file:'"):
            self.assertIn(needle, js, f"传输层缺少关键实现: {needle}")
        # 桌面模式靠 fetch 垫片把同源请求转成 js_api（各调用点一行都不用改）
        self.assertIn("window.fetch = async", js,
                      "缺少 fetch 垫片：桌面模式下请求会打到 file:// 上，面板拿不到数据")
        # 启动流程里必须**先定通道、再开始取数据**（beforeStart 是启动前的唯一钩子）
        boot = js[js.index("beforeStart"):]
        self.assertLess(boot.index("Transport.detect()"),
                        boot.index("'/api/auth/status'"),
                        "beforeStart 里必须先 detect 通道再发请求")

    def test_localstorage_is_guarded(self):
        """file:// 下 localStorage 可能被禁用 —— 裸访问会让整段脚本死掉（白屏）"""
        js = _script_block()
        self.assertIn("__ac_probe", js, "应有 localStorage 可用性探测 + 内存兜底")
        # 裸访问只允许出现在 store 内部（探测 1 次 + 返回 1 次）
        self.assertEqual(js.count("localStorage."), 2,
                         "裸 localStorage 访问只应出现在 store 的探测与实现里")
        self.assertIn("store.getItem('ac_token')", js, "业务代码应走带兜底的 store")


class GuidePrefillTest(unittest.TestCase):
    """手动建号场景：引导页要能把 ws/令牌带进账号表单（入口在桌面外壳 index.html 里）"""

    def test_index_has_prefill_action(self):
        html = (ROOT / "src" / "astercore" / "web" / "static" / "index.html").read_text(encoding="utf-8")
        self.assertIn("gd-prefill", html)
        self.assertIn("填入账号表单", html)
        for fid in ("ac_ws", "ac_http", "ac_token"):
            self.assertIn(fid, html, "要填进账号表单的这几个字段")
        self.assertIn("store.setItem('prefill'", html, "必须走 store 兜底，不能裸用 localStorage")


class SharedPanelEnvelopeTest(unittest.TestCase):
    """共用外壳（panel.js）与各端信封层的接线。

    背景：Linux 端接口返回扁平 JSON，桌面端返回 {ok,data}。共用外壳若只认扁平层，
    桌面端会静默失效：「运行日志」恒为"暂无日志"、顶栏恒为"已停止/连接失败" ——
    页面不报错，只有肉眼能看出来，所以在这里钉住。
    """

    @staticmethod
    def _func_body(name: str) -> str:
        js = PANEL_JS.read_text(encoding="utf-8")
        i = js.index("function " + name)
        return js[i:js.index("\n}", i)]

    def test_log_viewer_uses_end_adapter(self):
        """日志正文形状两端不同：提供 hooks.fetchLogs 的端必须走它（结构化条目转文本行）"""
        body = self._func_body("loadLogs")
        self.assertIn("P.hooks.fetchLogs", body, "运行日志必须走各端适配钩子")
        self.assertIn("typeof d.data === 'object'", body, "同时要认 {ok,data} 信封")

    def test_header_status_handles_no_account_and_envelope(self):
        body = self._func_body("refreshHeaderStatus")
        self.assertIn("typeof d.data === 'object'", body, "要认 {ok,data} 信封")
        self.assertIn("if (!url)", body,
                      "没有当前账号时 headerStatus() 返回 null，不能直接丢给 api()")


class PanelApiSmokeTest(unittest.TestCase):
    """真实 Flask 面板的 API 冒烟: 首页 200 / 账号列表 JSON / 鉴权状态"""

    @classmethod
    def setUpClass(cls):
        try:
            from astercore.web.server import ManagerWebPanel
        except ImportError:  # flask 未安装
            raise unittest.SkipTest("flask 未安装")
        import astercore.core.manager as mgr_mod

        cls._tmp = tempfile.TemporaryDirectory()
        accounts_dir = Path(cls._tmp.name) / "accounts"
        accounts_dir.mkdir(parents=True)
        # 一个预配置账号(不启动)，scan() 应能列出
        acct = accounts_dir / "acct1"
        acct.mkdir()
        (acct / "backend.json").write_text(
            '{"account_id":"acct1","display_name":"测试号",'
            '"backend_name":"onebot",'
            '"ws_url":"ws://127.0.0.1:3001","http_url":"http://127.0.0.1:3000",'
            '"access_token":""}', encoding="utf-8")
        import asyncio
        _loop = asyncio.new_event_loop()
        cls.mgr = mgr_mod.AccountManager(accounts_dir=accounts_dir)
        cls.panel = ManagerWebPanel(cls.mgr, lambda: _loop)
        cls.client = cls.panel.app.test_client()

    @classmethod
    def tearDownClass(cls):
        try:
            cls._loop.close()
        except Exception:
            pass
        cls._tmp.cleanup()

    def test_index_served(self):
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertIn("栖星 AsterCore", r.get_data(as_text=True))

    def test_accounts_json(self):
        r = self.client.get("/api/accounts")
        self.assertEqual(r.status_code, 200)
        jd = r.get_json()
        self.assertTrue(jd["ok"])
        self.assertIn("acct1", [a["account_id"] for a in jd["data"]])

    def test_auth_status(self):
        r = self.client.get("/api/auth/status")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["data"]["mode"], "none")

    def test_api_404_is_json(self):
        r = self.client.get("/api/nonexistent-xyz")
        self.assertEqual(r.status_code, 404)
        self.assertFalse(r.get_json()["ok"])

    def test_info_reports_version_and_dirs(self):
        from astercore import __version__
        r = self.client.get("/api/info")
        self.assertEqual(r.status_code, 200)
        d = r.get_json()["data"]
        self.assertEqual(d["version"], __version__)
        self.assertIn("plugin_dir", d)
        self.assertIn("accounts_dir", d)
        self.assertFalse(d["frozen"], "测试环境不是打包运行")


if __name__ == "__main__":
    unittest.main()


# --------------------------------------------------------------------------- #
# 运行方式页的两个按钮（保存 / 重跑启动向导）
# 回归：曾经写成裸名 `_session_authed()`（它其实是 self. 的方法）→ 一点就
# 「异常: NameError: name '_session_authed' is not defined」，页面按钮全废。
# --------------------------------------------------------------------------- #
class RuntimeSettingsRouteTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from astercore.web.server import ManagerWebPanel
            from astercore.app import AppState
        except ImportError:
            raise unittest.SkipTest("flask 未安装")
        import astercore.core.manager as mgr_mod

        cls._tmp = tempfile.TemporaryDirectory()
        base = Path(cls._tmp.name)
        accounts_dir = base / "accounts"
        accounts_dir.mkdir(parents=True)
        cls.state = AppState(base)
        cls.state.load()
        cls.state.set_backend("napcat", ack_risk=True)     # 先当作已完成引导
        cls.mgr = mgr_mod.AccountManager(accounts_dir=accounts_dir)
        cls.panel = ManagerWebPanel(cls.mgr, lambda: None, app_state=cls.state)
        cls.client = cls.panel.app.test_client()

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_reset_wizard_does_not_crash(self):
        r = self.client.post("/api/settings/runtime", json={"action": "reset_wizard"})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True)[:300])
        j = r.get_json()
        self.assertTrue(j["ok"], j)
        self.assertTrue(j["data"]["needs_wizard"])

    def test_save_mode_does_not_crash(self):
        r = self.client.post("/api/settings/runtime",
                             json={"backend_mode": "lagrange", "ack_risk": True})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True)[:300])
        self.assertTrue(r.get_json()["ok"])
        self.assertEqual(self.state.backend_mode, "lagrange")

    def test_save_mode_without_ack_is_400(self):
        r = self.client.post("/api/settings/runtime", json={"backend_mode": "lagrange"})
        self.assertEqual(r.status_code, 400)

    def test_missing_mode_is_400(self):
        r = self.client.post("/api/settings/runtime", json={})
        self.assertEqual(r.status_code, 400)


class SettingsPageRouteTest(unittest.TestCase):
    """设置合并进面板后：/ui/page/settings-page 必须能渲染（导航项在 PAGES 里）"""

    @classmethod
    def setUpClass(cls):
        try:
            from astercore.web.server import ManagerWebPanel
            from astercore.app import AppState
            from astercore.core.manager import AccountManager
        except ImportError:
            raise unittest.SkipTest("flask 未安装")
        cls._tmp = tempfile.TemporaryDirectory()
        base = Path(cls._tmp.name)
        (base / "accounts").mkdir(parents=True)
        cls.state = AppState(base)
        cls.state.load()
        cls.mgr = AccountManager(accounts_dir=base / "accounts")
        cls.panel = ManagerWebPanel(cls.mgr, lambda: None, app_state=cls.state)
        cls.client = cls.panel.app.test_client()

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_page_registered_and_rendered(self):
        from astercore.web import ui_pages
        self.assertIn("settings-page", ui_pages.PAGES, "页面要注册进 PAGES，侧栏才会出现")
        r = self.client.get("/ui/page/settings-page")
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True)[:200])
        body = r.get_json()["data"]["html"]
        for marker in ("页面模式与端口", "sp_mode", "sp_port", "34567"):
            self.assertIn(marker, body)

    def test_unknown_page_still_404(self):
        self.assertEqual(self.client.get("/ui/page/nope").status_code, 404)


class GuidePageInPanelTest(unittest.TestCase):
    """引导改成面板内一页（用户要求：建账号时才弹，不在首次启动拦人）"""

    @classmethod
    def setUpClass(cls):
        try:
            from astercore.web.server import ManagerWebPanel
            from astercore.app import AppState
            from astercore.core.manager import AccountManager
        except ImportError:
            raise unittest.SkipTest("flask 未安装")
        cls._tmp = tempfile.TemporaryDirectory()
        base = Path(cls._tmp.name)
        (base / "accounts").mkdir(parents=True)
        cls.state = AppState(base)
        cls.state.load()
        cls.panel = ManagerWebPanel(AccountManager(accounts_dir=base / "accounts"),
                                   lambda: None, app_state=cls.state)
        cls.client = cls.panel.app.test_client()

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_guide_page_in_panel(self):
        from astercore.web import ui_pages
        self.assertIn("guide-page", ui_pages.PAGES)
        r = self.client.get("/ui/page/guide-page")
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True)[:200])
        html = r.get_json()["data"]["html"]
        for marker in ("连接引导", "gd-open", "gd-config", "gd-host",
                       "NapCat", 'data-key="napcat"', "由软件自带",
                       "gd-create", 'id="gdQQ"', "一键建好并启动"):
            self.assertIn(marker, html)

    def test_accounts_page_has_guide_entry(self):
        html = self.client.get("/ui/page/accounts").get_json()["data"]["html"]
        self.assertIn('data-open="guide-page"', html, "新建账号要走引导入口")

    def test_no_startup_gating_in_entry_url(self):
        """首次启动不再强制引导页（用户明确要求）"""
        import inspect

        from astercore import launch
        src = inspect.getsource(launch._entry_url)
        self.assertNotIn("/setup", src)


class SpecNamingTest(unittest.TestCase):
    """规格命名对齐：inited / page.mode / page.port（旧键必须仍可读）"""

    def _state(self, files=None):
        from astercore.app import AppState
        import json as _json
        tmp = tempfile.TemporaryDirectory()
        base = Path(tmp.name)
        if files:
            (base / "runtime.json").write_text(_json.dumps(files, ensure_ascii=False), encoding="utf-8")
        st = AppState(base)
        st.load()
        return tmp, st

    def test_new_names_roundtrip(self):
        tmp, st = self._state()
        try:
            self.assertFalse(st.inited)
            st.set_inited(True)
            self.assertTrue(st.inited)
            ok, msg = st.set_page_config("port", 41234)
            self.assertTrue(ok, msg)
            cfg = st.page_config()
            self.assertEqual(cfg["mode"], "port")
            self.assertEqual(cfg["port"], 41234)
            import json as _j2
            raw = _j2.loads((Path(tmp.name) / "runtime.json").read_text(encoding="utf-8"))
            self.assertIn("inited", raw, "要写规格名 inited（顶层）")
            extra = raw.get("extra") or {}
            self.assertEqual(extra["page"]["port"], 41234, "要写规格名 page.port")
            self.assertEqual(extra["page"]["mode"], "port")
            self.assertEqual(extra["web"]["port"], 41234, "旧键也要同步，启动逻辑还读它")
            self.assertTrue(st.inited)
        finally:
            tmp.cleanup()

    def test_old_config_still_readable(self):
        """老配置文件只有 wizard_completed / transport / web.port → 必须照旧能用"""
        # 真实格式：transport/web 存在 extra 里（save() 写的就是 extra）
        tmp, st = self._state({"wizard_completed": True, "wizard_version": 9,
                               "extra": {"transport": {"mode": "http"},
                                         "web": {"enabled": True, "host": "127.0.0.1",
                                                 "port": 45678}}})
        try:
            self.assertTrue(st.inited, "旧名 wizard_completed 要能读成 inited")
            self.assertEqual(st.page_config(), {"mode": "port", "port": 45678})
        finally:
            tmp.cleanup()

    def test_invalid_page_config_rejected(self):
        tmp, st = self._state()
        try:
            self.assertFalse(st.set_page_config("nope")[0])
            self.assertFalse(st.set_page_config("port", 0)[0], "端口 0 非法")
            self.assertFalse(st.set_page_config("port", 70000)[0], "端口越界非法")
            self.assertFalse(st.set_page_config("port", "abc")[0], "非数字非法")
        finally:
            tmp.cleanup()

    def test_window_mode_default(self):
        tmp, st = self._state()
        try:
            self.assertEqual(st.page_config()["mode"], "window", "默认窗口模式")
            pc = st.page_config()["port"]
            self.assertTrue(1 <= pc <= 65535, f"默认端口要在合法范围（实际 {pc}）")
            self.assertEqual(pc, int(st.web_config().get("port") or 34567),
                             "page.port 要与旧键 web.port 保持一致")
        finally:
            tmp.cleanup()


class BotAvatarFallbackTest(unittest.TestCase):
    """左上角账号卡头像：空 src 会显示破图（无账号时最明显）"""

    @classmethod
    def setUpClass(cls):
        cls.js = PANEL_JS.read_text(encoding="utf-8")

    def test_no_empty_avatar_src(self):
        self.assertNotIn('botAvatar\\" src=\\"\\"', self.js, "不允许空 src（会破图）")
        self.assertIn('botAvatar\\" src=\\"favicon.ico', self.js, "无账号时用应用图标")

    def test_has_inline_fallback(self):
        self.assertIn("DEFAULT_AVATAR", self.js)
        self.assertIn("data:image/svg+xml", self.js, "要有不依赖文件的内联兜底")
        self.assertIn("naturalWidth === 0", self.js, "文件不存在要立刻兜底")


class RuntimePageLayoutTest(unittest.TestCase):
    """运行方式页：后端模式是**单选**，右侧按大类分**多张卡片**（用户要求）"""

    @classmethod
    def setUpClass(cls):
        try:
            from astercore.web.server import ManagerWebPanel
            from astercore.app import AppState
            from astercore.core.manager import AccountManager
        except ImportError:
            raise unittest.SkipTest("flask 未安装")
        cls._tmp = tempfile.TemporaryDirectory()
        base = Path(cls._tmp.name)
        (base / "accounts").mkdir(parents=True)
        cls.state = AppState(base)
        cls.state.load()
        cls.panel = ManagerWebPanel(AccountManager(accounts_dir=base / "accounts"),
                                    lambda: None, app_state=cls.state)
        cls.html = cls.panel.app.test_client().get("/ui/page/settings-runtime").get_json()["data"]["html"]

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_uses_radios_not_select(self):
        self.assertGreaterEqual(self.html.count('type="radio"'), 2, "至少两个模式可选")
        self.assertIn('data-act="rt-pick"', self.html)
        self.assertNotIn("<select", self.html, "不能再用下拉框")

    def test_hidden_input_keeps_save_working(self):
        self.assertIn('data-rt="backend_mode"', self.html, "保存仍靠 data-rt 约定")
        self.assertIn('type="hidden"', self.html)

    def test_multiple_cards_by_category(self):
        self.assertGreaterEqual(self.html.count('class="card"'), 3,
                                "按大类分卡片：后端模式 / 当前状态 / 操作")
        for title in ("后端模式", "当前状态", "操作"):
            self.assertIn(title, self.html)


class MergedSettingsPageTest(unittest.TestCase):
    """设置合并页：左侧只一个「设置」入口，右侧三张卡片各带自己的保存按钮"""

    @classmethod
    def setUpClass(cls):
        try:
            from astercore.web.server import ManagerWebPanel
            from astercore.app import AppState
            from astercore.core.manager import AccountManager
        except ImportError:
            raise unittest.SkipTest("flask 未安装")
        cls._tmp = tempfile.TemporaryDirectory()
        base = Path(cls._tmp.name)
        (base / "accounts").mkdir(parents=True)
        cls.state = AppState(base)
        cls.state.load()
        cls.panel = ManagerWebPanel(AccountManager(accounts_dir=base / "accounts"),
                                    lambda: None, app_state=cls.state)
        cls.client = cls.panel.app.test_client()
        cls.html = cls.client.get("/ui/page/settings").get_json()["data"]["html"]
        cls.index = (ROOT / "src" / "astercore" / "web" / "static" / "index.html").read_text(encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_three_cards_in_one_page(self):
        for marker in ("后端模式", "安全设置", "页面与端口"):
            self.assertIn(marker, self.html, f"{marker} 卡片要在设置页里")
        self.assertGreaterEqual(self.html.count('class="card"'), 3)

    def test_each_card_has_its_own_save(self):
        for act in ("save-rt", "save-auth", "save-page"):
            self.assertIn(f'data-act="{act}"', self.html, f"{act} 保存按钮要在")

    def test_sidebar_has_single_settings_entry(self):
        # 只看导航数组（'settings-runtime' 等仍会出现在保存注册表里，那是应该保留的）
        m = re.search(r"PANEL\.nav = \[(.*?)\];", self.index, re.S)
        self.assertIsNotNone(m, "index.html 里要有 PANEL.nav 数组")
        nav = m.group(1)
        self.assertIn("id: 'settings'", nav, "侧栏要有唯一「设置」入口")
        for gone in ("settings-runtime", "settings-auth", "settings-page"):
            self.assertNotIn(gone, nav, f"侧栏导航不应再列出 {gone}")


class AccountsPageLayoutTest(unittest.TestCase):
    """账号页（用户要求）：横向长条列表 + 右上角「添加账号」弹窗，去掉内嵌表单"""

    @classmethod
    def setUpClass(cls):
        try:
            from astercore.web.server import ManagerWebPanel
            from astercore.app import AppState
            from astercore.core.manager import AccountManager
            from astercore.core.manager import AccountConfig
        except ImportError:
            raise unittest.SkipTest("flask 未安装")
        cls._tmp = tempfile.TemporaryDirectory()
        base = Path(cls._tmp.name)
        (base / "accounts").mkdir(parents=True)
        cls.state = AppState(base)
        cls.state.load()
        cls.mgr = AccountManager(accounts_dir=base / "accounts")
        for i, aid in enumerate(("10001", "10002")):
            cls.mgr.save_config(AccountConfig(account_id=aid, display_name="号" + aid,
                                              backend_name="onebot"))
        cls.panel = ManagerWebPanel(cls.mgr, lambda: None, app_state=cls.state)
        cls.html = cls.panel.app.test_client().get("/ui/page/accounts").get_json()["data"]["html"]
        cls.index = (ROOT / "src" / "astercore" / "web" / "static" / "index.html").read_text(encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_rows_are_horizontal_lines(self):
        self.assertEqual(self.html.count('class="acct-line"'), 2, "两个账号 → 两条横向长条")

    def test_add_button_in_card_header(self):
        self.assertIn('data-act="acct-add"', self.html)
        self.assertIn("＋ 添加账号", self.html)

    def test_form_moved_into_dialog(self):
        self.assertIn('id="acctDlg"', self.html, "要弹窗")
        self.assertNotIn("➕ 新建 / 编辑账号", self.html, "页面里不应再有内嵌新建表单卡片")
        # 弹窗里仍要有 data-acct 字段，保存逻辑才能不改
        for f in ("account_id", "display_name", "ws_url", "http_url", "access_token"):
            self.assertIn(f'data-acct="{f}"', self.html)

    def test_index_handles_dialog_actions(self):
        for act in ("acct-add", "acct-edit", "acct-dlg-cancel"):
            self.assertIn(f"'{act}':", self.index, f"{act} 处理器要在")


class GuideOpenDownloadTest(unittest.TestCase):
    """引导页第①步只做引导：点了用浏览器打开官方下载页，不在应用内下载"""

    @classmethod
    def setUpClass(cls):
        try:
            from astercore.web.server import ManagerWebPanel
            from astercore.app import AppState
            from astercore.core.manager import AccountManager
        except ImportError:
            raise unittest.SkipTest("flask 未安装")
        cls._tmp = tempfile.TemporaryDirectory()
        base = Path(cls._tmp.name)
        (base / "accounts").mkdir(parents=True)
        cls.state = AppState(base)
        cls.state.load()
        cls.panel = ManagerWebPanel(AccountManager(accounts_dir=base / "accounts"),
                                    lambda: None, app_state=cls.state)
        cls.client = cls.panel.app.test_client()
        cls.html = cls.client.get("/ui/page/guide-page").get_json()["data"]["html"]

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_step_one_opens_browser(self):
        self.assertIn('data-act="gd-open"', self.html)
        self.assertIn("github.com", self.html, "要露出官方下载页链接")
        self.assertNotIn('data-act="gd-install"', self.html, "第①步不再在应用内下载")

    def test_open_url_endpoint_guards_scheme(self):
        self.assertEqual(self.client.post("/api/app/open-url", json={"url": "file:///etc/passwd"}).status_code, 400)
        self.assertEqual(self.client.post("/api/app/open-url", json={"url": "javascript:alert(1)"}).status_code, 400)


class GuideInstallPathTest(unittest.TestCase):
    """引导页要明确告诉用户组件装到哪个目录（一步不落地讲清）"""

    @classmethod
    def setUpClass(cls):
        try:
            from astercore.web.server import ManagerWebPanel
            from astercore.app import AppState
            from astercore.core.manager import AccountManager
        except ImportError:
            raise unittest.SkipTest("flask 未安装")
        cls._tmp = tempfile.TemporaryDirectory()
        base = Path(cls._tmp.name)
        (base / "accounts").mkdir(parents=True)
        cls.state = AppState(base)
        cls.state.load()
        cls.panel = ManagerWebPanel(AccountManager(accounts_dir=base / "accounts"),
                                    lambda: None, app_state=cls.state)
        cls.html = cls.panel.app.test_client().get("/ui/page/guide-page").get_json()["data"]["html"]

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_shows_component_dir(self):
        self.assertIn("components", self.html)
        self.assertIn("napcat", self.html.lower())
        self.assertIn("第①步要装到哪", self.html)

    def test_step_by_step(self):
        for marker in ("去官网下载", "解压", "NapCatWinBootMain.exe", "② 写 NapCat 配置"):
            self.assertIn(marker, self.html, f"逐步说明要含 {marker}")


class ToastAboveModalTest(unittest.TestCase):
    """提示必须在弹窗之上（弹窗 z-index 10000，提示原来 9999 被盖住）"""

    def test_toast_zindex_higher(self):
        css = (Path(__file__).resolve().parent.parent / "src" / "astercore" /
               "web" / "static" / "panel.css").read_text(encoding="utf-8")
        toast = [l for l in css.splitlines() if l.startswith(".toast{")]
        modal = [l for l in css.splitlines() if l.startswith(".ui-modal{")]
        self.assertTrue(toast and modal)
        tz = int(toast[0].split("z-index:")[1].split(";")[0])
        mz = int(modal[0].split("z-index:")[1].split(";")[0])
        self.assertGreater(tz, mz, "提示层级必须高于弹窗")


class AccountFixedPathRpcTest(unittest.TestCase):
    """桌面 file:// 只认固定路径的 hub 方法 —— 账号四类操作都必须走固定路径"""

    def setUp(self):
        root = Path(__file__).resolve().parent.parent
        self.index = (root / "src" / "astercore" / "web" / "static" / "index.html").read_text(encoding="utf-8")
        self.launch = (root / "src" / "astercore" / "launch.py").read_text(encoding="utf-8")

    def test_index_uses_fixed_paths(self):
        for url in ("/api/app/account-save", "/api/app/account-delete",
                    "/api/app/account-start", "/api/app/account-stop"):
            self.assertIn(url, self.index, f"前端要用固定路径 {url}")
        self.assertNotIn("acctUrl('/start')", self.index, "不要再拼动态路径")
        self.assertNotIn("acctUrl('/stop')", self.index, "不要再拼动态路径")

    def test_launch_registers_them(self):
        for url in ("/api/app/account-save", "/api/app/account-delete",
                    "/api/app/account-start", "/api/app/account-stop"):
            self.assertIn(f'_reg_rpc(hub, "{url}"', self.launch, f"{url} 要注册成 RPC")

    def test_no_local_reg_before_definition(self):
        """踩过两次的坑：函数内 _reg 定义之前的调用 → UnboundLocalError 被 except 吞掉"""
        def_line = self.launch.find("def _reg(path, fn):")   # 不存在也没关系
        if def_line < 0:
            return
        import re as _re
        for m in _re.finditer(r'^\s+_reg\(', self.launch, _re.M):
            self.assertGreater(m.start(), def_line,
                               "函数内 _reg(...) 调用不能出现在它的定义之前（改用模块级 _reg_rpc）")

    def test_dialog_closes_after_save(self):
        self.assertGreaterEqual(self.index.count("acctDlg') ;") + self.index.count("getElementById('acctDlg')"), 2)


class WindowChromeTest(unittest.TestCase):
    """无边框窗口 + 自绘三按钮（左最小化 / 中收起托盘 / 右退出程序）"""

    def setUp(self):
        root = Path(__file__).resolve().parent.parent
        self.index = (root / "src" / "astercore" / "web" / "static" / "index.html").read_text(encoding="utf-8")
        self.launch = (root / "src" / "astercore" / "launch.py").read_text(encoding="utf-8")
        self.shell = (root / "src" / "astercore" / "shell.py").read_text(encoding="utf-8")
        self.css = (root / "src" / "astercore" / "web" / "static" / "panel.css").read_text(encoding="utf-8")

    def test_frameless(self):
        self.assertIn("frameless=True", self.shell)

    def test_buttons_drive_actions(self):
        """按钮必须靠 data-act 走面板的全局委托 —— 只写 id 不挂 data-act 就是死按钮"""
        for bid, act in (("dbWinMin", "win-min"), ("dbWinHide", "win-hide"), ("dbWinQuit", "win-quit")):
            self.assertIn(f'id="{bid}"', self.index)
            self.assertIn(f'data-act="{act}"', self.index, f"{bid} 必须挂 data-act={act}")
            self.assertIn(f"'{act}':", self.index, f"{act} 处理器要在")

    def test_rpc_registered(self):
        self.assertIn('_reg_rpc(hub, "/api/app/window"', self.launch)
        for action in ("minimize", "hide", "quit"):
            self.assertIn(f'action == "{action}"', self.launch)

    def test_drag_region(self):
        self.assertIn("-webkit-app-region:drag", self.css, "无边框窗口必须有拖动区")
        self.assertIn("no-drag", self.css, "按钮要排除拖动，否则点不动")

