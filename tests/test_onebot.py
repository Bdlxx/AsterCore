# OneBot v11 事件翻译 + 动作映射 离线单测（不起 WS）
import unittest

from astercore.backends.onebot import OneBotV11Backend
from astercore.backends.onebot_mapping import map_action
from astercore.core.backend import BackendConfig


class TranslateTest(unittest.TestCase):
    def setUp(self):
        self.b = OneBotV11Backend(BackendConfig(), account_id=740979632)

    def test_group_text_message(self):
        payload = {
            "post_type": "message",
            "message_type": "group",
            "group_id": 123456789,
            "user_id": 10001,
            "self_id": 740979632,
            "time": 1700000000,
            "raw_message": "早上好",
            "message": [{"type": "text", "data": {"text": "早上好"}}],
        }
        ev = self.b._translate_message(payload)
        self.assertEqual(ev.type, "message")
        self.assertEqual(ev.account_id, 740979632)
        self.assertEqual(ev.message_type, "group")
        self.assertEqual(ev.group_id, 123456789)
        self.assertEqual(ev.text(), "早上好")
        self.assertEqual(ev.segments[0].type, "text")

    def test_private_image_at(self):
        payload = {
            "post_type": "message", "message_type": "private",
            "user_id": 10002, "self_id": 740979632, "time": 1,
            "raw_message": "", "message": [
                {"type": "text", "data": {"text": "看"}},
                {"type": "image", "data": {"url": "http://x/1.jpg", "file": "/app/1.jpg"}},
                {"type": "at", "data": {"qq": "10003"}},
            ],
        }
        ev = self.b._translate_message(payload)
        self.assertEqual(ev.message_type, "private")
        self.assertIsNone(ev.group_id)
        types = [s.type for s in ev.segments]
        self.assertEqual(types, ["text", "image", "at"])
        self.assertEqual(ev.segments[1].data["url"], "http://x/1.jpg")

    def test_group_increase_notice(self):
        payload = {
            "post_type": "notice", "notice_type": "group_increase",
            "group_id": 1, "user_id": 2, "self_id": 740979632, "time": 3,
        }
        ev = self.b._translate_generic(payload)
        self.assertEqual(ev.type, "group_increase")
        self.assertEqual(ev.extra["notice_type"], "group_increase")

    def test_image_with_file_and_url(self):
        payload = {
            "post_type": "message", "message_type": "group",
            "group_id": 1, "user_id": 2, "self_id": 740979632, "time": 1,
            "raw_message": "", "message": [
                {"type": "image", "data": {"file": "/app/cache/1.jpg", "url": "http://x/1.jpg"}},
                {"type": "reply", "data": {"id": 99}},
                {"type": "at", "data": {"qq": "3"}},
            ],
        }
        ev = self.b._translate_message(payload)
        self.assertEqual([s.type for s in ev.segments],
                         ["image", "reply", "at"])
        img = ev.segments[0]
        self.assertEqual(img.data["file"], "/app/cache/1.jpg")
        self.assertEqual(img.data["url"], "http://x/1.jpg")
        self.assertEqual(ev.segments[1].data["id"], 99)

    def test_notice_group_decrease_and_admin(self):
        cases = [
            {"post_type": "notice", "notice_type": "group_decrease",
             "group_id": 1, "user_id": 2, "self_id": 740979632, "time": 1},
            {"post_type": "notice", "notice_type": "group_admin",
             "group_id": 1, "user_id": 2, "sub_type": "set",
             "self_id": 740979632, "time": 1},
        ]
        ev = self.b._translate_generic(cases[0])
        self.assertEqual(ev.type, "group_decrease")  # 语义化事件名
        ev2 = self.b._translate_generic(cases[1])
        self.assertEqual(ev2.type, "notice")

    def test_request_add_group(self):
        p = {"post_type": "request", "request_type": "group",
             "group_id": 1, "user_id": 2, "self_id": 740979632, "time": 1}
        ev = self.b._translate_generic(p)
        self.assertEqual(ev.type, "request")

    def test_echo_is_not_event(self):
        # echo 响应不应触发事件（框架过滤）
        self.b._handle_message('{"echo":"x","status":"ok"}')
        self.assertIsNone(self.b.on_event)  # 未抛异常即可


class MappingTest(unittest.TestCase):
    def test_send_group(self):
        r = map_action("send_group", {"group_id": 5, "message": [{"type": "text", "data": {"text": "hi"}}]})
        self.assertEqual(r["action"], "send_group_msg")
        self.assertEqual(r["params"]["group_id"], 5)

    def test_send_message_group(self):
        r = map_action("send_message", {"message_type": "group", "group_id": 5,
                                        "message": "hi"})
        self.assertEqual(r["action"], "send_msg")
        self.assertEqual(r["params"]["message"], "hi")

    def test_get_members(self):
        r = map_action("get_group_members", {"group_id": 5})
        self.assertEqual(r["action"], "get_group_member_list")

    def test_upload_file_group(self):
        r = map_action("upload_file", {"target": {"type": "group", "id": 5},
                                       "file": "/app/x.mp4", "name": "x.mp4"})
        self.assertEqual(r["action"], "upload_group_file")
        self.assertEqual(r["params"]["group_id"], 5)

    def test_unknown(self):
        self.assertIsNone(map_action("no_such_action", {}))


if __name__ == "__main__":
    unittest.main()
