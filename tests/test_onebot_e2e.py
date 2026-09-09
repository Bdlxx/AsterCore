# OneBot 后端动作全映射 端到端断言测试
# 起 fake_onebot(--expect 全动作列表) → backend.action 逐动作调用 → 断言 ok
import asyncio
import json
import subprocess
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from astercore.core.backend import BackendConfig
from astercore.backends.onebot import OneBotV11Backend
from astercore.core.models import seg_text

ROOT = Path(__file__).resolve().parent.parent
FAKE = ROOT / "tools/fake_onebot.py"

# 期望的全部动作名（OneBot API）
ALL_ACTIONS = [
    "send_group_msg", "send_private_msg", "delete_msg",
    "set_group_ban", "set_group_card", "get_group_member_list",
    "get_group_info", "get_stranger_info",
]


class OneBotActionE2ETest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fake = subprocess.Popen(
            [sys.executable, str(FAKE), "--port", "9951", "--no-push"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(1.5)
        cls.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(cls.loop)
        cls.b = OneBotV11Backend(BackendConfig(ws_url="ws://127.0.0.1:9951"))
        cls.loop.run_until_complete(cls.b.start())
        time.sleep(0.5)

    @classmethod
    def tearDownClass(cls):
        try:
            cls.loop.run_until_complete(cls.b.stop())
        finally:
            cls.fake.terminate()
            try:
                cls.fake.wait(timeout=3)
            except Exception:
                cls.fake.kill()
            cls.loop.close()

    def test_each_action_ok(self):
        cases = [
            ("send_group", {"group_id": 1, "message": [seg_text("hi").to_dict()]}),
            ("send_private", {"user_id": 2, "message": [seg_text("hi").to_dict()]}),
            ("recall_message", {"message_id": 123}),
            ("set_group_ban", {"group_id": 1, "user_id": 2, "duration": 60}),
            ("set_group_card", {"group_id": 1, "user_id": 2, "card": "x"}),
            ("get_group_members", {"group_id": 1}),
            ("get_group_info", {"group_id": 1}),
            ("get_stranger_info", {"user_id": 2}),
        ]
        for action, params in cases:
            res = self.loop.run_until_complete(self.b.action(action, params))
            self.assertTrue(res.ok, f"{action} 应成功: {res.error}")
            # fake 回执 data 可能被假数据替换（列表查询类），此时只验证 ok
            if isinstance(res.data, dict):
                self.assertIn(res.data.get("action"), ALL_ACTIONS)

    def test_unknown_action_fails(self):
        res = self.loop.run_until_complete(self.b.action("no_such", {}))
        self.assertFalse(res.ok)


if __name__ == "__main__":
    unittest.main()
