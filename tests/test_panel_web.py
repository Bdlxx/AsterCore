# -*- coding: utf-8 -*-
# 面板静态页 + Web JSON API 冒烟测试
# 背景: v0.1.2 曾因 index.html 同一 <script> 作用域里同时存在
#   `async function j` 与 `const j` → JS 解析期 SyntaxError → 整个脚本失效,
#   面板只剩静态骨架("加载中…"永不消失)。本测试防止此类问题再次上船。
import os
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

    def test_sidebar_has_no_accounts_entry(self):
        """用户要求：侧栏不再列「账号」（和左上角账号切换重复），
        账号切换改走左上角账号卡向右弹出的浮层。"""
        s = INDEX.read_text(encoding="utf-8")
        m = re.search(r"PANEL\.nav = \[(.*?)\];", s, re.S)
        self.assertIsNotNone(m, "index.html 里要有 PANEL.nav 数组")
        self.assertNotIn("id: 'accounts'", m.group(1), "侧栏导航不应再有「账号」项")
        self.assertIn("accountPop:", s, "要有账号浮层 hook（点左上角账号卡弹出）")
        self.assertIn('data-nav="accounts"', s, "浮层里要有进入「管理账号」的入口")


class GuidePrefillTest(unittest.TestCase):
    """手动建号场景：引导页要能把 ws/令牌带进账号表单（入口在桌面外壳 index.html 里）"""

    def test_index_has_prefill_action(self):
        html = (ROOT / "src" / "astercore" / "web" / "static" / "index.html").read_text(encoding="utf-8")
        self.assertIn("gd-prefill", html)
        self.assertIn("填入「添加账号」弹窗", html)
        for fid in ("ac_ws", "ac_http", "ac_token"):
            self.assertIn(fid, html, "要填进账号表单的这几个字段")
        self.assertIn("store.setItem('prefill'", html, "必须走 store 兜底，不能裸用 localStorage")


class RailToggleIconTest(unittest.TestCase):
    """收起/展开侧栏的按钮：就一个，钉在账号卡片右侧，用「面板」图标（用户指定样式）。

    最后一轮用户要求：展开态按钮仍留在账号卡片里、靠右；收起侧边栏后按钮整个收掉，
    展开侧栏只靠点左上角头像。"""

    def setUp(self):
        self.js = PANEL_JS.read_text(encoding="utf-8")
        self.css = (ROOT / "src" / "astercore" / "web" / "static" / "panel.css").read_text(encoding="utf-8")

    def test_panel_icon_defined(self):
        self.assertIn('id=\\"i-panel\\"', self.js, "图标 sprite 里要有 i-panel（圆角方框 + 左侧分隔线）")
        self.assertIn('href=\\"#i-panel\\"', self.js, "收起/展开按钮要用 i-panel 图标")

    def test_no_arrow_glyphs(self):
        for glyph in ("\u25c0", "\u25b6"):
            self.assertNotIn(glyph, self.js, "不该再用三角箭头字符（图标水合会把它换成播放三角）")

    def test_icon_is_sized(self):
        self.assertIn(".rail-toggle .ico{width:18px;height:18px}", self.css,
                      "图标要定尺寸，否则跟 12px 字一样小")

    def test_button_sits_in_account_card(self):
        """展开态：按钮仍在账号卡片里、靠右（用户指定）"""
        self.assertIn("position:absolute;top:21px;right:10px", self.css, "展开态：按钮在账号卡右侧")
        self.assertIn(".sidebar:not(.rail) .logo-identity{padding-right:34px}", self.css,
                      "展开态要给按钮留位，长名字不压按钮")

    def test_toggle_hidden_when_collapsed(self):
        self.assertIn(".sidebar.rail .rail-toggle{display:none}", self.css, "收起侧栏后按钮整个收掉")
        self.assertNotIn('id=\\"railToggleTop\\"', self.js, "顶栏不该再放第二个按钮")

    def test_avatar_expands_when_collapsed(self):
        self.assertIn("sb.classList.contains('rail')) { setRail(false); return; }", self.js,
                      "收起态点左上角头像 = 展开侧栏")
        self.assertIn(".sidebar.rail .sidebar-logo{cursor:pointer}", self.css,
                      "收起态头像要有可点手感")


class NavIconStyleTest(unittest.TestCase):
    """侧栏导航图标：DeepSeek 网页那种裸线条风格 + 悬停显示名称（用户指定）"""

    def setUp(self):
        self.js = PANEL_JS.read_text(encoding="utf-8")
        self.index = INDEX.read_text(encoding="utf-8")
        self.css = (ROOT / "src" / "astercore" / "web" / "static" / "panel.css").read_text(encoding="utf-8")

    def test_nav_items_have_title(self):
        """悬停显示图标名称：导航项要带 title（收起成竖条时只剩图标，全靠它）"""
        needle = 'title="\' + esc(it.label) + \'"'
        self.assertEqual(self.js.count(needle), 2,
                         "两个分支（有子菜单 / 无子菜单）都要带 title")

    def test_nav_icons_are_bare(self):
        """图标不再坐在色块里（DeepSeek 风格）：悬停/选中由整行高亮 + 图标变色表达"""
        self.assertIn(".nav-item .icon{background:transparent;width:20px;height:20px}", self.css)
        self.assertIn(".nav-item.active .icon{background:transparent;color:var(--color-primary);"
                      "box-shadow:none}", self.css)

    def test_nav_icons_are_distinct(self):
        """每个导航项一个图标（以前「插件配置」和「设置」都是 ⚙️，长得一模一样）"""
        m = re.search(r"PANEL\.nav = \[(.*?)\];", self.index, re.S)
        self.assertIsNotNone(m, "index.html 里要有 PANEL.nav 数组")
        icons = re.findall(r"icon: '([^']+)'", m.group(1))
        self.assertTrue(icons, "导航项要有图标")
        self.assertEqual(len(icons), len(set(icons)), f"导航图标重复: {icons}")

    def test_new_nav_icons_defined(self):
        for sid in ("i-dashboard", "i-filetext"):
            self.assertIn('id=\\"' + sid + '\\"', self.js, f"图标 sprite 里要有 {sid}")

    def test_plugin_and_settings_icons(self):
        """插件用「三方块 + 菱形」，设置用齿轮（用户指定样式）"""
        self.assertIn('id=\\"i-plugin\\"', self.js, "要有 i-plugin 图标")
        self.assertIn("'\\u{1F9E9}': 'plugin'", self.js, "🧩（插件）要指向 i-plugin")
        self.assertIn("rotate(45 17.5 6.5)", self.js, "i-plugin 的菱形是旋转 45° 的方块")
        self.assertIn('circle cx=\\"12\\" cy=\\"12\\" r=\\"3\\"', self.js, "设置图标要有齿轮中心圆")


class TopSeamTest(unittest.TestCase):
    """账号卡 / 侧栏与顶栏的连接处不留分界线（用户要求"连接处不显示接缝"）"""

    def setUp(self):
        self.css = (ROOT / "src" / "astercore" / "web" / "static" / "panel.css").read_text(encoding="utf-8")

    def _rule(self, prefix):
        for ln in self.css.splitlines():
            if ln.startswith(prefix):
                return ln
        return ""

    def test_no_vertical_seam(self):
        rule = self._rule(".sidebar{")
        self.assertTrue(rule, "要有 .sidebar 基础规则")
        self.assertNotIn("border-right", rule, "侧栏不该有右边线（接缝）")
        self.assertNotIn("box-shadow", rule, "侧栏不该用 1px 阴影当分界线")

    def test_no_horizontal_seam(self):
        self.assertNotIn("border-bottom", self._rule(".header{"), "顶栏不该有下边线（接缝）")
        self.assertNotIn("border-bottom", self._rule(".sidebar-logo{"), "账号卡不该有下边线（接缝）")

    def test_junction_is_rounded(self):
        """顶栏/侧栏的接缝做成圆角（用户要求）：内容区左上角圆角，.main 用顶栏底色兜底"""
        self.assertIn("border-top-left-radius:14px", self._rule(".content{"), "内容区左上角要圆角")
        self.assertIn("background:var(--bg-header)", self._rule(".main{"), ".main 要铺顶栏底色当圆角底")


class RailAlignmentTest(unittest.TestCase):
    """展开/收起侧栏时按钮和头像零位移（用户要求：按钮只向右延伸，不许上下跳）"""

    def setUp(self):
        self.css = (ROOT / "src" / "astercore" / "web" / "static" / "panel.css").read_text(encoding="utf-8")
        self.js = PANEL_JS.read_text(encoding="utf-8")
        self.index = INDEX.read_text(encoding="utf-8")

    def _rule(self, prefix):
        for ln in self.css.splitlines():
            if ln.startswith(prefix):
                return ln
        return ""

    def test_group_title_keeps_its_box_in_rail(self):
        """收起态分组标题只换外观、不许 display:none —— 否则它下面的按钮整体上移"""
        self.assertIn(".sidebar.rail .nav-group-title{color:var(--text-secondary)", self.css)
        self.assertNotIn(".sidebar.rail .nav-group-title,", self.css, "分组标题不能被 display:none 掉")

    def test_logo_identity_height_pinned(self):
        """账号卡定高：收起态 .logo-meta 隐藏后卡片不能变矮，否则整列下移"""
        self.assertIn("height:38px", self._rule(".logo-identity{"))
        self.assertNotIn(".sidebar.rail .logo-identity{", self.css, "收起态不能再改账号卡的对齐")

    def test_nav_groups_not_pinned_to_bottom(self):
        """除左下角齿轮外都在顶部：导航组不许再 margin-top:auto 钉底"""
        self.assertNotIn(".nav-group:last-child{margin-top:auto}", self.css)
        self.assertIn(".sidebar-foot{position:relative;margin-top:auto;", self.css, "只有齿轮钉底")

    def test_plugin_submenu_not_auto_opened(self):
        """二级菜单默认收起：默认展开会让「运行日志」在收起/展开时上下跳 45px"""
        self.assertIn("P.hooks.subMenus.config && P.page === 'config'", self.js)

    def test_rail_submenu_expands_in_place(self):
        """收起态：二级菜单就地向下展开（只去左缩进、居中省略），不能 display:none"""
        self.assertNotIn(".sidebar.rail .nav-sub{display:none!important}", self.css)
        self.assertIn(".sidebar.rail .nav-sub{padding:2px 0 8px}", self.css)
        self.assertIn(".sidebar.rail .nav-sub-item{justify-content:center;margin:1px 6px}", self.css)
        self.assertNotIn(".sidebar.rail .nav-sub-item{color:transparent", self.css,
                         "子项不能藏起来（用户要求看得见列表）")

    def test_plugin_rows_use_at_icon(self):
        """插件列表项前面挂 @ 图标（用户指定）；收起态只留图标、行高不变"""
        self.assertIn('<span class="icon" aria-hidden="true">@</span>', self.index)
        self.assertIn('class="nav-sub-item has-icon ', self.index)
        self.assertIn(".nav-sub-item.has-icon{display:flex;align-items:center;gap:8px;height:33px;", self.css,
                      "行高写死 33px，两态同高才不跳")
        self.assertIn(".sidebar.rail .nav-sub-item.has-icon .nav-sub-label,", self.css,
                      "收起态只留 @ 图标")

    def test_rail_click_does_not_expand_sidebar(self):
        """收起态点父项只展开列表，不许把侧栏也拉开（用户要求）"""
        body = self.js.split("function toggleSubMenu(id) {")[1].split(chr(10) + "}")[0]
        self.assertNotIn("setRail", body, "toggleSubMenu 里不该再展开侧栏")
        self.assertIn("P.subOpen[id] = !P.subOpen[id];", body)


class AppMenuTest(unittest.TestCase):
    """左下角只留一个「设置」齿轮，点它向右弹出 更新 / 设置 / 引导（用户要求）"""

    def setUp(self):
        self.js = PANEL_JS.read_text(encoding="utf-8")
        self.index = INDEX.read_text(encoding="utf-8")
        self.css = (ROOT / "src" / "astercore" / "web" / "static" / "panel.css").read_text(encoding="utf-8")

    def test_gear_button_in_shell(self):
        self.assertIn('class=\\"app-menu-btn\\" id=\\"appMenuBtn\\"', self.js, "左下角要有齿轮按钮")
        self.assertIn('href=\\"#i-settings\\"', self.js, "齿轮按钮用设置图标")

    def test_card_shows_update_settings_guide(self):
        m = re.search(r"PANEL\.appMenu = \[(.*?)\];", self.index, re.S)
        self.assertIsNotNone(m, "index.html 里要有 PANEL.appMenu")
        body = m.group(1)
        for label in ("更新", "设置", "引导"):
            self.assertIn("label: '%s'" % label, body, f"卡片里要有「{label}」")

    def test_entries_removed_from_sidebar(self):
        nav = re.search(r"PANEL\.nav = \[(.*?)\];", self.index, re.S).group(1)
        for gone in ("about-page", "guide-page"):
            self.assertNotIn(gone, nav, "更新 / 引导 已挪到齿轮卡片里，侧栏不再列")

    def test_card_pops_right(self):
        self.assertIn(".app-menu{position:absolute;left:calc(100% - 4px);", self.css, "卡片从齿轮右侧弹出")

    def test_menu_wiring(self):
        for needle in ("function toggleAppMenu(force)", "function bindAppMenu()", "  bindAppMenu();",
                       "P.toggleAppMenu = toggleAppMenu"):
            self.assertIn(needle, self.js, f"卡片开关要接上：{needle}")


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
        for marker in ("连接引导", "gd-install", "gd-config", "gd-host",
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


class PageTitleTest(unittest.TestCase):
    """网页端标题（用户指定「栖星管理页面」）：固定住，别再被账号名覆盖回去"""

    def test_title_is_fixed(self):
        idx = INDEX.read_text(encoding="utf-8")
        self.assertIn("<title>栖星管理页面</title>", idx)
        # 以前登录后会把标题改成"<账号名> - 管理面板"，等于静默改掉用户要的标题
        self.assertNotIn("document.title =", idx)
        self.assertNotIn("管理面板", idx)


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
    """设置合并页：左侧只一个「设置」入口，右侧三块设置改完即存（没有保存按钮）"""

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
        for marker in ("当前状态", "安全设置", "页面模式与端口"):
            self.assertIn(marker, self.html, f"{marker} 卡片要在设置页里")
        self.assertGreaterEqual(self.html.count('class="card"'), 3)

    def test_no_save_buttons_auto_save(self):
        """用户要求：设置页不保留保存按钮，像正常软件那样改完即存。"""
        for act in ("save-rt", "save-auth", "save-page"):
            self.assertNotIn(f'data-act="{act}"', self.html, f"{act} 保存按钮应已去掉")
        for mid in ("svauthMsg", "svpageMsg"):
            self.assertIn(f'id="{mid}"', self.html, f"{mid} 状态回显要在（自动保存用）")

    def test_login_mode_not_on_settings_page(self):
        """登录模式改到账号弹窗里选（用户要求），设置页不再保留后端模式单选。"""
        self.assertNotIn('data-act="rt-pick"', self.html, "设置页不该再有后端模式单选")
        self.assertIn("登录模式", self.html, "要留一句说明告诉用户去哪选")

    def test_port_blank_by_default(self):
        """端口框默认留空、默认值只做灰色占位（用户要求：此处默认为空）。"""
        m = re.search(r'id="sp_port"[^>]*>', self.html)
        self.assertIsNotNone(m, "设置页要有端口输入框")
        tag = m.group(0)
        self.assertIn('value=""', tag, "没显式设过端口时应留空")
        self.assertIn('placeholder="34567"', tag, "默认端口应作为灰色占位显示")

    def test_auth_guard_needs_password(self):
        """改完即存后，开密码鉴权却没填密码必须被拦（否则会把自己锁在门外）。"""
        from astercore.app import AppState
        from astercore.core.manager import AccountManager
        from astercore.web.server import ManagerWebPanel

        base = Path(self._tmp.name) / "guard"
        (base / "accounts").mkdir(parents=True)
        state = AppState(base)
        state.load()
        panel = ManagerWebPanel(AccountManager(accounts_dir=base / "accounts"),
                                lambda: None, app_state=state)
        cli = panel.app.test_client()
        r = cli.post("/api/settings/auth", json={"mode": "password"})
        self.assertEqual(r.status_code, 400, "没填密码就开密码鉴权应被拦")
        self.assertIn("密码", r.get_json().get("error", ""))

    def test_cards_get_real_config_not_fallbacks(self):
        """合并页必须拿到三个分页的 ctx，而不是各段自己的兜底默认值。

        回归（v0.6.24 实测）：server._page_ctx 只给 'settings-runtime' /
        'settings-auth' / 'settings-page' 喂 ctx，合并页 'settings' 漏了 →
        后端模式显示"当前没有可选项"、鉴权方式退回"不鉴权"、端口退回 34567。
        用户在这页点保存，就把这些默认值**写回配置**（等于静默关掉鉴权）。
        所以这里用非默认值渲染：只要还是默认值就说明 ctx 又漏接了。
        """
        from astercore.app import AppState
        from astercore.core.manager import AccountManager
        from astercore.web.server import ManagerWebPanel

        base = Path(self._tmp.name) / "combo"
        (base / "accounts").mkdir(parents=True)
        state = AppState(base)
        state.load()
        state.set_web_config({"port": 18751})
        state.set_transport("port")
        panel = ManagerWebPanel(AccountManager(accounts_dir=base / "accounts"),
                                lambda: None, app_state=state)
        panel.auth.set_password("pw-for-test")
        panel.auth.set_mode("password")
        cli = panel.app.test_client()
        # 开了鉴权后 API 要先登录（页面片段按设计放行，由前端拿 401 再跳登录）
        self.assertEqual(cli.get("/api/accounts").status_code, 401)
        self.assertTrue(cli.post("/api/login",
                                 json={"password": "pw-for-test"}).get_json().get("ok"),
                        "用刚设的密码应能登录")
        html = cli.get("/ui/page/settings").get_json()["data"]["html"]

        self.assertIn('id="au_mode"', html, "安全设置没渲染（ctx 漏接）")
        self.assertNotIn("（当前没有可选项）", html)
        self.assertIn('value="18751"', html, "端口要取真实配置，不能是兜底 34567")
        self.assertIn('<option value="password" selected', html,
                      "鉴权方式要取真实配置，不能退回 none")

    def test_sidebar_has_single_settings_entry(self):
        # 「设置」入口从侧栏挪到了左下角齿轮弹出的卡片里（用户要求：左下角只留设置按钮）
        m = re.search(r"PANEL\.appMenu = \[(.*?)\];", self.index, re.S)
        self.assertIsNotNone(m, "index.html 里要有 PANEL.appMenu 数组")
        self.assertIn("id: 'settings'", m.group(1), "卡片里要有唯一「设置」入口")
        nav = re.search(r"PANEL\.nav = \[(.*?)\];", self.index, re.S).group(1)
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
                                              backend_name="onebot",
                                              ws_url="ws://127.0.0.1:%d" % (3001 + i),
                                              access_token=("tok" + aid) if i else ""))
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
        for f in ("account_id", "display_name", "backend_name", "ws_url", "http_url", "access_token"):
            self.assertIn(f'data-acct="{f}"', self.html)

    def test_dialog_picks_connection_mode_first(self):
        """用户要求：点「添加账号」先选 NapCat / 内置协议，选完才显示对应填写项。"""
        self.assertIn('id="acctStep1"', self.html, "要有第一步（选连接方式）")
        self.assertIn("PANEL.hooks.acctPick('onebot')", self.html)
        self.assertIn("PANEL.hooks.acctPick('lagrange')", self.html)
        self.assertIn('id="acctFields"', self.html, "第二步（填信息）要先藏起来")
        self.assertIn('id="acctConnFields"', self.html, "NapCat 的连接信息单独一组（内置协议不需要）")
        self.assertIn('id="ac_mode"', self.html, "隐藏字段承接选择结果")
        self.assertIn('data-acct="backend_name"', self.html, "值要写 backend.name")
        self.assertNotIn('<select id="ac_mode"', self.html, "不再是下拉：先选卡再填")
        self.assertIn('src="napcat.png"', self.html, "NapCat 选卡用官方图标")
        # 弹窗里内联 onclick 调的是 PANEL.hooks.acctPick —— 只写标记不接线就等于点了没反应
        self.assertIn("PANEL.hooks.acctPick = ", self.index, "acctPick 必须真的挂上")
        self.assertIn("PANEL.hooks.acctShowMode = ", self.index)

    def test_connection_info_is_not_hand_filled(self):
        """用户要求：NapCat 的连接信息由**软件**写进它的配置（令牌随机生成），不该让人填。

        所以三个框收进「手动填写」里，默认是自动配置。"""
        self.assertIn('id="acctManual"', self.html, "手填那栏要收起来")
        self.assertIn("手动填写连接信息", self.html)
        self.assertNotIn('id="ac_ws" value="ws://', self.html, "不能再预填地址让人以为是必填")
        self.assertNotIn('value="ws://127.0.0.1:3001" data-acct="ws_url"', self.html)
        self.assertIn("令牌随机生成", self.index, "要说明令牌是软件随机生成的")
        self.assertIn("'/api/guide/config'", self.index, "保存时软件自己去写组件配置")

    def test_row_exposes_current_backend(self):
        self.assertIn('data-backend="onebot"', self.html, "编辑按钮要带上当前登录模式")

    def test_edit_button_carries_connection_info(self):
        """编辑弹窗靠 data-* 预填：漏了 ws/token 就会被默认值覆盖
        （用户只想改个显示名，结果连接配置被冲成默认值）。"""
        self.assertIn('data-ws="ws://127.0.0.1:3002"', self.html, "编辑按钮要带真实 ws 地址")
        self.assertIn('data-token="tok10002"', self.html, "编辑按钮要带真实令牌")

    def test_index_handles_dialog_actions(self):
        for act in ("acct-add", "acct-edit", "acct-dlg-cancel"):
            self.assertIn(f"'{act}':", self.index, f"{act} 处理器要在")

    def test_index_saves_backend_name(self):
        """保存时要把弹窗选的登录模式一起发给后端（后端读的是 backend.name）。"""
        self.assertIn("const prep = await prepBackend(f);", self.index,
                      "保存前要过 prepBackend（软件自己写组件配置）")
        self.assertIn("backend: { name: name, ws_url: r.data.ws_url", self.index,
                      "连接模式要写进 backend.name，端口令牌取组件配置的权威值")
        self.assertIn("put('ac_mode', el.getAttribute('data-backend')", self.index,
                      "编辑时要把账号当前的登录模式填回下拉")
        self.assertIn("acctShowMode(PANEL.hooks._acctConn.backend)", self.index,
                      "编辑时直接进第二步（连接方式已知）")
        self.assertIn("offerComponentRestart(prep)", self.index,
                      "改过组件配置要问一句要不要重启（配置只在启动时读一次）")


class GuideOneClickDownloadTest(unittest.TestCase):
    """用户要求：引导第①步改成**在应用内直接下载并解压** NapCat 官方 Windows 版"""

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
        cls.index = INDEX.read_text(encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_step_one_downloads_in_app(self):
        self.assertIn('data-act="gd-install"', self.html)
        self.assertIn('data-key="napcat"', self.html)
        self.assertNotIn('data-act="gd-open"', self.html, "第①步不再跳浏览器下载页")
        self.assertNotIn("github.com", self.html, "不再让用户自己去官网下")

    def test_downloads_official_windows_shell(self):
        from astercore import components as C
        urls = C.get_component("napcat").download_urls
        self.assertTrue(any("NapCat.Shell" in u for u in urls), urls)

    def test_topbar_progress_slot_and_texts(self):
        """顶栏要能显示「下载中 x%」与「解压中」（用户要求）"""
        self.assertIn("el.id = 'topJob'", self.index)
        self.assertIn("'解压中…'", self.index)
        self.assertIn("'下载中 ' + pct + '%'", self.index)
        self.assertIn("header-right", self.index, "插进顶栏右侧那一排")

    def test_done_dialog_goes_to_accounts(self):
        """下载完成弹窗 → 进用户管理页建连接"""
        self.assertIn("title: '下载完成'", self.index)
        self.assertIn("PANEL.goPage('accounts')", self.index)
        self.assertIn("openAcctDialog", self.index)

    def test_open_url_endpoint_guards_scheme(self):
        self.assertEqual(self.client.post("/api/app/open-url", json={"url": "file:///etc/passwd"}).status_code, 400)
        self.assertEqual(self.client.post("/api/app/open-url", json={"url": "javascript:alert(1)"}).status_code, 400)


class GuideInstallPathTest(unittest.TestCase):
    """引导页要讲清组件装到哪个目录（用户要求：简化成三步引导，别堆长清单）"""

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
        self.assertIn('id="gdDir"', self.html, "组件目录要带 id（第①步提醒用它）")

    def test_step_by_step(self):
        for marker in ("一键下载并解压", "解压", "② 写 NapCat 配置", "③ 启动 NapCat 并取二维码"):
            self.assertIn(marker, self.html, f"逐步说明要含 {marker}")
        self.assertNotIn("NapCatWinBootMain.exe", self.html, "冗长的逐步清单已按用户要求删掉")


class DashboardOverviewTest(unittest.TestCase):
    """用户要求：概览不重复顶栏已有的信息；齿轮卡片要压住底部操作栏"""

    @classmethod
    def setUpClass(cls):
        try:
            from astercore.web.server import ManagerWebPanel
            from astercore.app import AppState
            from astercore.core.manager import AccountConfig, AccountManager
        except ImportError:
            raise unittest.SkipTest("flask 未安装")
        cls._tmp = tempfile.TemporaryDirectory()
        base = Path(cls._tmp.name)
        (base / "accounts").mkdir(parents=True)
        cls.state = AppState(base)
        cls.state.load()
        mgr = AccountManager(accounts_dir=base / "accounts")
        mgr.save_config(AccountConfig(account_id="10001", display_name="依星",
                                      backend_name="onebot",
                                      ws_url="ws://127.0.0.1:3001"))
        cls.panel = ManagerWebPanel(mgr, lambda: None, app_state=cls.state)
        client = cls.panel.app.test_client()
        cls.page = client.get("/ui/page/dashboard?aid=10001").get_json()["data"]["html"]
        cls.status = client.get("/ui/status?aid=10001").get_json()["data"]
        cls.css = (ROOT / "src" / "astercore" / "web" / "static" / "panel.css").read_text(encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_no_cards_duplicating_the_topbar(self):
        """账号状态与版本顶栏/左侧身份区都有了 —— 概览别再摊一遍（用户指定去掉）"""
        self.assertNotIn('<div class="label">账号状态</div>', self.page)
        self.assertNotIn('<div class="label">版本</div>', self.page)
        self.assertNotIn("dashBotVal", self.page)
        self.assertIn("后端连接", self.page, "顶栏没有的信息要留下")
        self.assertIn("已加载插件", self.page)

    def test_conn_card_is_polled(self):
        """留下的卡片要跟着 /ui/status 走，否则等于静态摆设"""
        self.assertIn('id="dashConnVal"', self.page)
        self.assertIn('id="dashConnDesc"', self.page)
        self.assertIn("dashConnVal", self.status["text"])
        # 页面上已经没有的元素，接口里也别再喂（那是"接口看着在工作"的假象）
        self.assertNotIn("dashBotVal", self.status["text"])
        self.assertNotIn("botStatusArea", self.status.get("html", {}))
        self.assertNotIn("dashBotDesc", self.status.get("html", {}))

    def test_sidebar_stacks_above_action_bar(self):
        """齿轮弹出的卡片/账号浮层向右探出侧栏：侧栏层级必须高于底部操作栏"""
        def zidx(sel):
            m = re.search(re.escape(sel) + r"\{[^}]*z-index:(\d+)", self.css)
            return int(m.group(1)) if m else None

        self.assertIsNotNone(zidx(".sidebar"), "侧栏要有显式 z-index")
        self.assertGreater(zidx(".sidebar"), zidx(".action-bar"))


class NapCatEntryTest(unittest.TestCase):
    """用户要求：侧栏「概览」下面常驻一个 NapCat 管理入口，用 NapCat 官方图标"""

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
        cls.page = cls.client.get("/ui/page/napcat").get_json()["data"]["html"]
        cls.index = INDEX.read_text(encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_nav_entry_sits_below_dashboard(self):
        nav = re.search(r"PANEL\.nav = \[(.*?)\];", self.index, re.S).group(1)
        self.assertIn("id: 'napcat'", nav, "侧栏要有 NapCat 入口")
        self.assertLess(nav.index("'dashboard'"), nav.index("'napcat'"),
                        "要排在「概览」下面")
        self.assertIn('src="napcat.png"', nav, "图标用 NapCat 官方图标")
        self.assertIn("label: 'NapCat'", nav)

    def test_official_icon_asset_and_route(self):
        icon = ROOT / "src" / "astercore" / "web" / "static" / "napcat.png"
        self.assertTrue(icon.is_file(), "官方图标要随包分发（static 目录整目录进包）")
        self.assertGreater(icon.stat().st_size, 512)
        r = self.client.get("/napcat.png")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.headers["Content-Type"].startswith("image/png"))
        self.assertEqual(r.get_data()[:8], b"\x89PNG\r\n\x1a\n")

    def test_page_registered_with_actions(self):
        from astercore.web import ui_pages as ui
        self.assertIn("napcat", ui.PAGES)
        self.assertIn("NapCat 管理", self.page)
        for act in ("gd-install", "gd-host", "gd-stop", "gd-log", "gd-refresh"):
            self.assertIn(f'data-act="{act}"', self.page, f"{act} 按钮要在")
        # 首屏状态必须有真数据：喂不进 ctx 时会静默退回全"—"（看着像"没装"，
        # 其实只是接线断了）—— 所以这里钉住组件目录真的渲染出来了。
        self.assertIn(f"components{os.sep}napcat", self.page, "组件目录要来自 components 模块")
        self.assertNotIn("<td>组件目录</td><td style=\"word-break:break-all\">—</td>", self.page)


class DesktopGuideJobRpcTest(unittest.TestCase):
    """桌面 file:// 模式的安装进度必须和 HTTP 同一份（以前 RPC 自己裸起线程 → 顶栏永远没进度）"""

    def test_launch_delegates_to_guide_module(self):
        src = (ROOT / "src" / "astercore" / "launch.py").read_text(encoding="utf-8")
        self.assertIn('api.get("install")', src, "RPC 安装要复用 guide 的任务化实现")
        self.assertIn("_guide.start_install", src, "兜底也走同一份")
        self.assertIn("_guide.jobs_snapshot", src, "状态要带 jobs")


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


class AccountHttpRoutesTest(unittest.TestCase):
    """浏览器（HTTP）模式下 /api/app/account-* 必须也有路由。

    回归（v0.6.24 实测）：这四条以前只在 launch.py 注册成 RPC，桌面 file:// 端能用；
    可面板也能用浏览器打开，那时只有 HTTP → 点账号弹窗「保存」直接 404「接口不存在」，
    用户看到的"点击处理有问题"就是这个。
    """

    @classmethod
    def setUpClass(cls):
        try:
            from astercore.web.server import ManagerWebPanel
            from astercore.app import AppState
            from astercore.core.manager import AccountManager
        except ImportError:
            raise unittest.SkipTest("flask 未安装")
        import asyncio
        import threading
        cls._tmp = tempfile.TemporaryDirectory()
        base = Path(cls._tmp.name)
        (base / "accounts").mkdir(parents=True)
        cls.state = AppState(base)
        cls.state.load()
        cls.mgr = AccountManager(accounts_dir=base / "accounts")
        # 真事件循环跑在后台线程：删除/启停路由里的 run_coro_sync 需要它
        cls.loop = asyncio.new_event_loop()
        cls.thread = threading.Thread(target=cls.loop.run_forever, daemon=True)
        cls.thread.start()
        cls.panel = ManagerWebPanel(cls.mgr, loop_provider=lambda: cls.loop,
                                    app_state=cls.state)
        cls.client = cls.panel.app.test_client()

    @classmethod
    def tearDownClass(cls):
        try:
            cls.loop.call_soon_threadsafe(cls.loop.stop)
            cls.thread.join(timeout=5)
        except Exception:
            pass
        cls._tmp.cleanup()

    def test_routes_registered(self):
        for url in ("/api/app/account-save", "/api/app/account-delete",
                    "/api/app/account-start", "/api/app/account-stop"):
            r = self.client.post(url, json={})
            self.assertNotEqual(r.status_code, 404, f"{url} 在浏览器模式下没注册（404）")

    def test_save_then_delete_roundtrip(self):
        r = self.client.post("/api/app/account-save", json={
            "account_id": "20001", "display_name": "测试号",
            "backend": {"name": "lagrange", "ws_url": "ws://127.0.0.1:3001",
                        "http_url": "http://127.0.0.1:3000", "access_token": ""},
        })
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.get_json().get("ok"))
        cfg = self.mgr.get_config("20001")
        self.assertIsNotNone(cfg, "保存后账号要真的落盘")
        self.assertEqual(cfg.backend_name, "lagrange", "登录模式要按弹窗选的值存")
        d = self.client.post("/api/app/account-delete", json={"account_id": "20001"})
        self.assertEqual(d.status_code, 200)
        self.assertIsNone(self.mgr.get_config("20001"))


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
        parts = self.index.split("function installWindowChrome()", 1)
        self.assertEqual(len(parts), 2, "要先有 installWindowChrome")
        body = parts[1]
        for bid, act in (("winMin", "win-min"), ("winHide", "win-hide"), ("winQuit", "win-quit")):
            self.assertIn(f'id="{bid}"', body, f"{bid} 要真的注入到顶栏里")
            self.assertIn(f'data-act="{act}"', body, f"{bid} 必须挂 data-act={act}")
            self.assertIn(f"'{act}':", self.index, f"{act} 处理器要在")

    def test_rpc_registered(self):
        self.assertIn('_reg_rpc(hub, "/api/app/window"', self.launch)
        for action in ("minimize", "hide", "quit"):
            self.assertIn(f'action == "{action}"', self.launch)

    def test_drag_region(self):
        self.assertIn("-webkit-app-region:drag", self.css, "无边框窗口必须有拖动区")
        self.assertIn("no-drag", self.css, "按钮要排除拖动，否则点不动")

    def test_buttons_are_injected_into_the_shared_header(self):
        """三按钮要真的被注入到共用顶栏右侧。

        踩过的坑：原来那条 #desktopBar 的标记在 panel.js 之后解析，panel.js 里
        `getElementById` 拿到 null 直接 return → 顶栏恒 display:none，三按钮和拖动区
        一起失效（v0.6.0 起一直没显示过，测试只查"标记里有 data-act"所以没抓住）。"""
        self.assertIn("function installWindowChrome()", self.index, "要有注入函数")
        self.assertIn("installWindowChrome();", self.index, "光定义不调用 = 还是没按钮")
        self.assertIn("location.protocol !== 'file:'", self.index, "只在桌面壳（file://）注入")
        self.assertIn("right.appendChild(box)", self.index, "要塞进顶栏右侧")
        self.assertIn("box.id = 'winCtl'", self.index)
        for act in ("win-min", "win-hide", "win-quit"):
            self.assertIn(f'data-act="{act}"', self.index, f"注入的按钮要带 {act}")
        self.assertIn("pywebview-drag-region", self.index,
                      "拖动交给 pywebview 认的类名（WebView2 不看 -webkit-app-region）")


class WindowMaximizeTest(unittest.TestCase):
    """无边框窗口没有系统最大化按钮 → 双击顶栏拖动区切换最大化/还原"""

    def setUp(self):
        root = Path(__file__).resolve().parent.parent
        self.index = (root / "src" / "astercore" / "web" / "static" / "index.html").read_text(encoding="utf-8")
        self.launch = (root / "src" / "astercore" / "launch.py").read_text(encoding="utf-8")
        self.shell = (root / "src" / "astercore" / "shell.py").read_text(encoding="utf-8")

    def test_dblclick_wired(self):
        self.assertIn("addEventListener('dblclick'", self.index, "顶栏要绑双击")
        self.assertIn("action: 'maximize'", self.index, "双击要调 maximize")
        self.assertIn("tagName === 'BUTTON'", self.index, "点按钮不能被当成双击标题栏")

    def test_backend_supports_maximize(self):
        self.assertIn("def toggle_maximize", self.shell)
        self.assertIn('action == "maximize"', self.launch)


class TextSelectTest(unittest.TestCase):
    """面板文字可选中（token/路径要能复制），但顶栏拖动区必须保持不可选"""

    def setUp(self):
        root = Path(__file__).resolve().parent.parent
        self.shell = (root / "src" / "astercore" / "shell.py").read_text(encoding="utf-8")
        self.css = (root / "src" / "astercore" / "web" / "static" / "panel.css").read_text(encoding="utf-8")

    def test_shell_enables_text_select(self):
        self.assertIn("text_select=True", self.shell, "pywebview 要开 text_select")

    def test_css_allows_text_but_keeps_drag_area_unselectable(self):
        self.assertIn("-webkit-user-select:text", self.css, "正文/输入框要可选中")
        self.assertIn("#desktopBar,#desktopBar *{-webkit-user-select:none", self.css,
                      "顶栏是拖动区，必须保持不可选")

