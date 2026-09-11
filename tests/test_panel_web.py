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
