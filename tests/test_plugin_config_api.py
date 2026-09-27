# -*- coding: utf-8 -*-
# 插件配置 API 测试：schema 下发 + 保存校验（走真实 Flask 路由与真实 AccountRuntime）
#
# 重点验证三件事：
#   1. 声明了 __config_schema__ 的插件 → 面板拿到 declared=true 的字段表
#   2. 保存时**服务端**校验（不能只靠前端）：越界/必填 → 400 + 精确到字段的错误
#   3. 没声明的插件 → 按当前值推断字段；保存不改写类型、不丢未声明的键
import asyncio
import json
import tempfile
import threading
import unittest
from pathlib import Path

from astercore.backends.null import NullBackend
from astercore.core.backend import BackendConfig
from astercore.core.manager import AccountManager
from astercore.core.runtime import AccountRuntime
from astercore.web.server import ManagerWebPanel

# 声明了 schema 的插件
DECLARED_SRC = '''
__meta__ = {"name": "declared_demo", "name_cn": "声明示例", "version": "1.0.0"}

__config_schema__ = {
    "keywords": {"type": "list", "item_type": "text", "label": "关键词",
                 "min_items": 1},
    "reply": {"type": "text", "label": "回复", "maxlength": 10},
    "ratio": {"type": "number", "label": "概率", "min": 0, "max": 100,
              "step": 1, "unit": "%"},
    "mode": {"type": "select", "label": "模式", "options": ["a", "b"]},
    "on": {"type": "bool", "label": "开关"},
    "api_key": {"type": "password", "label": "密钥"},
    "targets": {"type": "group_select", "label": "目标群"},
}


def handle(event: dict) -> bool:
    return False
'''

# 没声明 schema 的插件（要靠推断）
PLAIN_SRC = '''
__meta__ = {"name": "plain_demo", "name_cn": "无声明示例", "version": "1.0.0"}


def handle(event: dict) -> bool:
    return False
'''


class PluginConfigApiTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        base = Path(cls.tmp.name)
        cls.accounts = base / "accounts"
        cls.pdir = base / "plugins"
        cls.pdir.mkdir(parents=True)
        (cls.pdir / "declared_demo.py").write_text(DECLARED_SRC, encoding="utf-8")
        (cls.pdir / "plain_demo.py").write_text(PLAIN_SRC, encoding="utf-8")
        cls.data_root = base / "data"

        # 真事件循环跑在后台线程：面板路由里的 run_coro_sync 需要它
        cls.loop = asyncio.new_event_loop()
        cls.thread = threading.Thread(target=cls.loop.run_forever, daemon=True)
        cls.thread.start()

        cls.acct = "10001"
        inst = cls.accounts / cls.acct
        (inst / "plugins").mkdir(parents=True)
        (inst / "data").mkdir(parents=True)
        cls.rt = AccountRuntime(
            account_id=cls.acct, data_dir=cls.data_root / cls.acct,
            backend=NullBackend(BackendConfig()), plugin_dir=cls.pdir,
            instance_dir=inst)
        asyncio.run_coroutine_threadsafe(cls.rt.start(), cls.loop).result(10)

        cls.mgr = AccountManager(cls.accounts, plugin_dir=cls.pdir,
                                 data_root=cls.data_root)
        cls.mgr.runtimes[cls.acct] = cls.rt
        cls.panel = ManagerWebPanel(cls.mgr, loop_provider=lambda: cls.loop,
                                    auth_file=base / "web_auth.json")
        cls.client = cls.panel.app.test_client()

    @classmethod
    def tearDownClass(cls):
        try:
            asyncio.run_coroutine_threadsafe(cls.rt.stop(), cls.loop).result(10)
        except Exception:
            pass
        cls.loop.call_soon_threadsafe(cls.loop.stop)
        cls.thread.join(timeout=5)
        cls.tmp.cleanup()

    # ---------- 工具 ----------
    def _get(self, path):
        r = self.client.get(path)
        return r.status_code, json.loads(r.get_data(as_text=True))

    def _post(self, path, body):
        r = self.client.post(path, json=body)
        return r.status_code, json.loads(r.get_data(as_text=True))

    # ---------- schema 下发 ----------
    def test_schema_endpoint_declared(self):
        code, body = self._get(f"/api/accounts/{self.acct}/plugins/declared_demo/schema")
        self.assertEqual(code, 200)
        d = body["data"]
        self.assertTrue(d["declared"], "插件声明了 __config_schema__，应标为 declared")
        self.assertEqual(d["declared_by"], "__config_schema__")
        by_name = {f["name"]: f for f in d["fields"]}
        self.assertEqual(set(by_name), {"keywords", "reply", "ratio", "mode",
                                        "on", "api_key", "targets"})
        # 每种类型的额外信息都要原样带到前端（前端只做映射，不做猜）
        self.assertEqual(by_name["keywords"]["type"], "list")
        self.assertEqual(by_name["keywords"]["item_type"], "text")
        self.assertEqual(by_name["keywords"]["min_items"], 1)
        self.assertEqual(by_name["reply"]["maxlength"], 10)
        self.assertEqual(by_name["ratio"]["unit"], "%")
        self.assertEqual(by_name["mode"]["options"],
                         [{"value": "a", "label": "a"}, {"value": "b", "label": "b"}])
        self.assertTrue(by_name["api_key"]["secret"])
        self.assertTrue(by_name["targets"]["multiple"])
        # 声明顺序要保住（表单阅读顺序）
        self.assertEqual([f["name"] for f in d["fields"]][0], "keywords")

    def test_schema_endpoint_inferred_for_undeclared(self):
        code, body = self._get(f"/api/accounts/{self.acct}/plugins/plain_demo/schema")
        self.assertEqual(code, 200)
        self.assertFalse(body["data"]["declared"])
        self.assertEqual(body["data"]["fields"], [], "没有配置也没有声明 → 空表单")

    def test_schema_unknown_account(self):
        code, body = self._get("/api/accounts/nope/plugins/declared_demo/schema")
        self.assertEqual(code, 404)
        self.assertFalse(body["ok"])

    # ---------- 保存校验（服务端说了算） ----------
    def test_save_rejects_invalid_and_reports_field(self):
        path = f"/api/accounts/{self.acct}/plugins/declared_demo/config"
        _, before = self._get(path)               # 快照：校验失败后必须一字不变
        code, body = self._post(path,
            {"keywords": [], "ratio": 999, "on": "true", "mode": "c",
             "api_key": "k", "targets": ["1"], "reply": "ok"})
        self.assertEqual(code, 400)
        self.assertFalse(body["ok"])
        errs = body["field_errors"]
        self.assertIn("keywords", errs, "min_items=1 应被拦住")
        self.assertIn("ratio", errs, "max=100 应被拦住")
        _, after = self._get(path)
        self.assertEqual(after["data"], before["data"], "校验失败不得落盘")

    def test_group_select_multiple_stored_as_list(self):
        """真缺陷回归：多选群号曾被 str() 成 "['123']" 存进配置"""
        path = f"/api/accounts/{self.acct}/plugins/declared_demo/config"
        code, body = self._post(path,
            {"keywords": ["a"], "reply": "r", "ratio": 1, "mode": "a",
             "on": True, "api_key": "k", "targets": ["315471269"]})
        self.assertEqual(code, 200, body)
        self.assertEqual(body["data"]["config"]["targets"], ["315471269"])
        self.assertIsInstance(body["data"]["config"]["targets"], list)

    def test_save_coerces_types_and_persists(self):
        code, body = self._post(
            f"/api/accounts/{self.acct}/plugins/declared_demo/config",
            {"keywords": ["签到", 123], "reply": "hi", "ratio": "80",
             "mode": "a", "on": "是", "api_key": "secret", "targets": ["315471269"]})
        self.assertEqual(code, 200, body)
        self.assertTrue(body["data"]["validated"])
        cfg = body["data"]["config"]
        self.assertEqual(cfg["keywords"], ["签到", "123"], "list 元素应转成字符串")
        self.assertEqual(cfg["ratio"], 80)
        self.assertIs(cfg["on"], True, "字符串 '是' 应转成真 bool")
        code2, got = self._get(f"/api/accounts/{self.acct}/plugins/declared_demo/config")
        self.assertEqual(got["data"]["ratio"], 80)

    def test_length_limit_enforced_server_side(self):
        code, body = self._post(
            f"/api/accounts/{self.acct}/plugins/declared_demo/config",
            {"keywords": ["a"], "reply": "x" * 50, "ratio": 1,
             "api_key": "k", "targets": [], "mode": "a", "on": True})
        self.assertEqual(code, 400)
        self.assertIn("reply", body["field_errors"])

    def test_raw_save_bypasses_validation(self):
        """JSON 页签是给高级用户的逃生口：显式 raw=1 时不做校验/转换"""
        code, body = self._post(
            f"/api/accounts/{self.acct}/plugins/declared_demo/config?raw=1",
            {"ratio": 999, "关键词自定义": ["x"]})
        self.assertEqual(code, 200)
        self.assertFalse(body["data"]["validated"])
        _, got = self._get(f"/api/accounts/{self.acct}/plugins/declared_demo/config")
        self.assertEqual(got["data"]["ratio"], 999)
        self.assertEqual(got["data"]["关键词自定义"], ["x"], "raw 模式不该丢任何键")

    # ---------- 未声明插件：不越权改写 ----------
    def test_undeclared_save_preserves_types_and_extra_keys(self):
        code, body = self._post(
            f"/api/accounts/{self.acct}/plugins/plain_demo/config",
            {"n": "不是数字也行", "deep": {"a": [1, 2]}})
        self.assertEqual(code, 200, body)
        self.assertFalse(body["data"]["validated"],
                         "推断的 schema 不能当法律用来改类型")
        _, got = self._get(f"/api/accounts/{self.acct}/plugins/plain_demo/config")
        self.assertEqual(got["data"]["n"], "不是数字也行")
        self.assertEqual(got["data"]["deep"], {"a": [1, 2]})

    def test_undeclared_schema_infers_after_having_values(self):
        """先有配置再打开面板 → 应能推断出字段（不是空表单）"""
        self._post(f"/api/accounts/{self.acct}/plugins/plain_demo/config",
                   {"enabled": True, "api_key": "x", "items": ["a"]})
        code, body = self._get(f"/api/accounts/{self.acct}/plugins/plain_demo/schema")
        d = body["data"]
        self.assertFalse(d["declared"])
        types = {f["name"]: f["type"] for f in d["fields"]}
        self.assertEqual(types["enabled"], "bool")
        self.assertEqual(types["api_key"], "password", "名字像密钥的推断成密码框")
        self.assertEqual(types["items"], "list")

    def test_declared_plugin_keeps_undeclared_keys_on_form_save(self):
        """表单只提交声明过的字段时，用户手写在 JSON 里的额外键不能丢"""
        self._post(f"/api/accounts/{self.acct}/plugins/declared_demo/config?raw=1",
                   {"keywords": ["a"], "我的私有键": 42})
        code, body = self._post(
            f"/api/accounts/{self.acct}/plugins/declared_demo/config",
            {"keywords": ["b"], "reply": "r", "ratio": 1, "mode": "a",
             "on": False, "api_key": "k", "targets": []})
        self.assertEqual(code, 200, body)
        self.assertEqual(body["data"]["config"]["我的私有键"], 42)


if __name__ == "__main__":
    unittest.main()
