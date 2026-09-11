# -*- coding: utf-8 -*-
# Linux 版插件兼容层测试
# 依据《Windows版开发计划书》§4「与现 Linux 多实例架构逻辑一致」、§5.2「保持现有
# SDK 接口不变」：Windows 版必须能直接跑现有 Linux 版插件（handle(event: dict)->bool
# + utils.*），而不是要求插件改写。本测试锁定该契约。
import asyncio
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

from astercore.compat.onebot import (cq_to_segments, event_to_onebot,
                                     normalize_message, segments_to_cq)
from astercore.compat.utils_pkg import install as compat_install
from astercore.core.backend import Backend, BackendConfig, register_backend
from astercore.core.models import ActionResult, Event, seg_at, seg_image, seg_text
from astercore.core.runtime import AccountRuntime

REAL_WORDLIB = Path("/root/mybot2/instances/740979632/plugins/wordlib.py")

# 一个"老写法"插件：同步 handle + utils.* + CQ 码字符串发送
LEGACY_SRC = '''
# 老式插件示例（Linux 版写法）
from utils.api import send_message
from utils.config import get_master_qq, get_bot_qq, get_bot_name
from utils.plugin_toggle import is_enabled, set_enabled
from utils.log import plugin_log


def handle(event: dict) -> bool:
    raw = str(event.get("raw_message", "")).strip()
    if raw == "ping":
        send_message(event, "pong")
        return True
    if raw == "我是谁":
        send_message(event, f"你是 {event.get('user_id')}")
        return True
    if raw == "图片":
        send_message(event, "看图 [CQ:image,file=/app/cache/images/a.png]")
        return True
    if raw == "主人":
        send_message(event, str(get_master_qq()))
        return True
    if raw == "开关":
        send_message(event, str(is_enabled(event.get("group_id"), "legacy_demo")))
        return True
    if raw == "开启":
        set_enabled(event.get("group_id"), "legacy_demo", True)
        return True
    if raw == "转发":
        send_message(event, [{"type": "forward", "data": {"messages": [
            {"type": "node", "data": {"name": "甲", "uin": "1",
             "content": [{"type": "text", "data": {"text": "hi"}}]}}]}}])
        return True
    return False
'''


@register_backend
class _SpyBackend(Backend):
    """记录所有动作的空后端（验证插件→动作链路）"""
    name = "spy"

    def __init__(self, cfg, account_id=None):
        super().__init__(cfg, account_id=account_id)
        self.calls: list[tuple[str, dict]] = []

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    async def check(self) -> ActionResult:
        return ActionResult.success({})

    async def action(self, action: str, params: dict) -> ActionResult:
        self.calls.append((action, params))
        return ActionResult.success({"message_id": 12345, "action": action})


class LegacyCompatTest(unittest.TestCase):

    def setUp(self):
        compat_install()
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.instance = base / "accounts" / "10001"
        (self.instance / "plugins").mkdir(parents=True)
        (self.instance / "data").mkdir(parents=True)
        (self.instance / "config.json").write_text(json.dumps({
            "BOT_NAME": "测试星", "BOT_QQ": 10001, "MASTER_QQ": [2840771765],
            "NAPCAT_HTTP": "http://127.0.0.1:3000", "ACCESS_TOKEN": "tok",
        }, ensure_ascii=False), encoding="utf-8")
        (self.instance / "plugins" / "legacy_demo.py").write_text(
            LEGACY_SRC, encoding="utf-8")
        self.data_dir = base / "data" / "10001"

    def tearDown(self):
        self.tmp.cleanup()

    def _runtime(self) -> AccountRuntime:
        rt = AccountRuntime(
            account_id=10001,
            data_dir=self.data_dir,
            backend=_SpyBackend(BackendConfig()),
            plugin_dir=None,
            instance_dir=self.instance,
        )
        setattr(rt, "display_name", "测试星")
        return rt

    async def _run(self, raw: str, group: bool = True):
        rt = self._runtime()
        await rt.start()
        names = [p["name"] for p in rt.list_plugins()]
        self.assertIn("legacy_demo", names, f"老插件未被加载: {names}")
        lp = rt.plugin_loader.loaded["legacy_demo"]
        self.assertEqual(lp.kind, "legacy", "应识别为 Linux 版老插件")
        ev = Event(type="message", account_id=10001, platform="test", time=0,
                   user_id=555, group_id=987654321 if group else None,
                   self_id=10001,
                   message_type="group" if group else "private",
                   raw=raw, segments=[seg_text(raw)])
        result = await rt.bus.dispatch(ev)
        calls = list(rt.backend.calls)
        await rt.stop()
        return result, calls

    # ---------- 加载与分发 ----------
    def test_legacy_plugin_loads_and_handles(self):
        result, calls = asyncio.run(self._run("ping"))
        self.assertEqual(result, "handled")
        self.assertTrue(calls, "老插件应通过 utils.api 发出动作")
        action, params = calls[0]
        self.assertEqual(action, "send_group")
        self.assertEqual(str(params["group_id"]), "987654321")
        self.assertEqual(params["message"][0]["data"]["text"], "pong")

    def test_unmatched_passes_through(self):
        result, calls = asyncio.run(self._run("无关内容"))
        self.assertEqual(result, "passed")
        self.assertFalse(calls)

    def test_private_message_target(self):
        result, calls = asyncio.run(self._run("我是谁", group=False))
        self.assertEqual(result, "handled")
        self.assertEqual(calls[0][0], "send_private")

    def test_cq_image_becomes_segment(self):
        _, calls = asyncio.run(self._run("图片"))
        msg = calls[0][1]["message"]
        types = [s["type"] for s in msg]
        self.assertIn("image", types, f"CQ 图片应被解析成消息段: {msg}")
        img = next(s for s in msg if s["type"] == "image")
        self.assertEqual(img["data"]["file"], "/app/cache/images/a.png")

    def test_forward_message_uses_forward_action(self):
        _, calls = asyncio.run(self._run("转发"))
        self.assertEqual(calls[0][0], "send_group_forward",
                         "合并转发必须走 *_forward_msg 动作")

    # ---------- 配置与数据 ----------
    def test_legacy_config_read_from_instance(self):
        _, calls = asyncio.run(self._run("主人"))
        text = calls[0][1]["message"][0]["data"]["text"]
        self.assertIn("2840771765", text, "utils.config 应读到账号实例的 config.json")

    def test_plugin_toggle_defaults_off_and_persists(self):
        _, calls = asyncio.run(self._run("开关"))
        self.assertEqual(calls[0][1]["message"][0]["data"]["text"], "False",
                         "分群开关默认应为关闭（与 Linux 版一致）")
        asyncio.run(self._run("开启"))
        f = self.instance / "data" / "plugin_toggle.json"
        self.assertTrue(f.exists(), "开关应写入账号实例的 data/plugin_toggle.json")
        data = json.loads(f.read_text(encoding="utf-8"))
        self.assertIs(data["987654321"]["legacy_demo"], True)

    def test_legacy_data_dir_is_instance_data(self):
        """老插件按 插件目录/../data 取数据 → 必须落在账号实例目录里"""
        rt = self._runtime()
        self.assertEqual(rt.plugin_loader.plugins_dir, self.data_dir / "plugins")
        self.assertIn(self.instance / "plugins", rt.plugin_loader.extra_dirs)


class ConvertTest(unittest.TestCase):
    """消息/事件双向转换（老插件契约的保真度）"""

    def test_cq_roundtrip(self):
        cq = "你好[CQ:at,qq=123]看图[CQ:image,file=x.png]"
        segs = cq_to_segments(cq)
        self.assertEqual([s.type for s in segs], ["text", "at", "text", "image"])
        self.assertEqual(segs[1].data["qq"], "123")
        self.assertEqual(segments_to_cq(segs), cq)

    def test_cq_escape(self):
        segs = cq_to_segments("[CQ:text,text=a&amp;b]")
        self.assertEqual(segs[0].data["text"], "a&b")

    def test_normalize_string_list_and_segments(self):
        self.assertEqual(len(normalize_message("hi")), 1)
        self.assertEqual(normalize_message([{"type": "text", "data": {"text": "x"}}])[0].type, "text")
        self.assertEqual(normalize_message([seg_text("y")])[0].data["text"], "y")

    def test_event_to_onebot_prefers_original_payload(self):
        """后端保留的原始报文必须优先（sender/message_id 等字段老插件要用）"""
        original = {"post_type": "message", "message_type": "group",
                    "group_id": 1, "user_id": 2, "raw_message": "raw",
                    "sender": {"nickname": "甲", "card": "甲卡", "role": "admin"},
                    "message_id": 999, "sub_type": "normal"}
        ev = Event(type="message", account_id=10, platform="t", time=0,
                   user_id=2, group_id=1, self_id=10, message_type="group",
                   raw="raw", segments=[seg_text("raw")], extra={"_onebot": original})
        d = event_to_onebot(ev)
        self.assertEqual(d["message_id"], 999)
        self.assertEqual(d["sender"]["card"], "甲卡")
        self.assertEqual(d["account_id"], 10)

    def test_event_to_onebot_rebuilds_when_no_payload(self):
        ev = Event(type="message", account_id=10, platform="t", time=5,
                   user_id=2, group_id=1, self_id=10, message_type="group",
                   raw="", segments=[seg_text("hi"), seg_at(7), seg_image("f.png")])
        d = event_to_onebot(ev)
        self.assertEqual(d["raw_message"], "hi[CQ:at,qq=7][CQ:image,file=f.png]")
        self.assertEqual(len(d["message"]), 3)
        self.assertIn("sender", d)


@unittest.skipUnless(REAL_WORDLIB.exists(), "未找到线上 wordlib.py")
class RealWordlibTest(unittest.TestCase):
    """用线上真实插件（41KB）验证：不改一行代码就能被 Windows 版加载"""

    def test_real_wordlib_loads(self):
        import shutil
        compat_install()
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            instance = base / "accounts" / "740979632"
            (instance / "plugins").mkdir(parents=True)
            (instance / "data").mkdir(parents=True)
            shutil.copy2(REAL_WORDLIB, instance / "plugins" / "wordlib.py")
            (instance / "config.json").write_text(json.dumps({
                "BOT_NAME": "依星", "BOT_QQ": 740979632, "MASTER_QQ": [2840771765],
            }, ensure_ascii=False), encoding="utf-8")

            async def _go():
                rt = AccountRuntime(
                    account_id=740979632,
                    data_dir=base / "data" / "740979632",
                    backend=_SpyBackend(BackendConfig()),
                    instance_dir=instance,
                )
                await rt.start()
                names = [p["name"] for p in rt.list_plugins()]
                lp = rt.plugin_loader.loaded.get("wordlib")
                await rt.stop()
                return names, lp

            names, lp = asyncio.run(_go())
            self.assertIn("wordlib", names, f"线上词库插件未能加载: {names}")
            self.assertEqual(lp.kind, "legacy")
            self.assertIn("词库", lp.meta.name_cn)


if __name__ == "__main__":
    unittest.main()


class LegacyPathMapTest(unittest.TestCase):
    """老插件硬编码的容器路径 → 本机 NapCat 目录（Windows 原生 NapCat 必需）"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.instance = base / "accounts" / "10001"
        (self.instance / "plugins").mkdir(parents=True)
        (self.instance / "data").mkdir(parents=True)
        self.rt = AccountRuntime(
            account_id=10001, data_dir=base / "data" / "10001",
            backend=_SpyBackend(BackendConfig()), instance_dir=self.instance)

    def tearDown(self):
        self.tmp.cleanup()

    def _cfg(self, extra: dict):
        (self.instance / "config.json").write_text(
            json.dumps(extra, ensure_ascii=False), encoding="utf-8")

    def test_identity_by_default_on_linux(self):
        """Linux/Docker 下容器路径本来就是对的 → 默认不映射（保持线上行为）"""
        from astercore.compat import paths as cpaths
        self._cfg({"BOT_QQ": 10001})
        self.assertEqual(cpaths.path_map(self.rt), [])
        self.assertEqual(cpaths.map_path("/app/cache/images/a.png", self.rt),
                         "/app/cache/images/a.png")

    def test_explicit_mapping_applies(self):
        from astercore.compat import paths as cpaths
        self._cfg({"BOT_QQ": 10001, "NAPCAT_CACHE_PREFIX": "/app/cache/images",
                   "NAPCAT_CACHE_HOST": "/tmp/napcat-cache"})
        mapped = cpaths.map_path("/app/cache/images/sub/a.png", self.rt)
        self.assertTrue(mapped.startswith("/tmp/napcat-cache"), mapped)
        self.assertIn("sub", mapped)
        self.assertNotIn("/app/", mapped)

    def test_segments_mapping_only_touches_file(self):
        from astercore.compat import paths as cpaths
        self._cfg({"BOT_QQ": 10001, "NAPCAT_CACHE_PREFIX": "/app/cache/images",
                   "NAPCAT_CACHE_HOST": "/tmp/napcat-cache"})
        segs = [{"type": "text", "data": {"text": "/app/cache/images/keep"}},
                {"type": "image", "data": {"file": "/app/cache/images/a.png"}}]
        out = cpaths.map_segments(segs, self.rt)
        self.assertEqual(out[0]["data"]["text"], "/app/cache/images/keep")
        self.assertTrue(out[1]["data"]["file"].startswith("/tmp/napcat-cache"))

    def test_other_paths_untouched(self):
        from astercore.compat import paths as cpaths
        self._cfg({"BOT_QQ": 10001, "NAPCAT_CACHE_PREFIX": "/app/cache/images",
                   "NAPCAT_CACHE_HOST": "/tmp/napcat-cache"})
        self.assertEqual(cpaths.map_path("http://x/y.png", self.rt), "http://x/y.png")
        self.assertEqual(cpaths.map_path("file:///app/cache/images/a.png", self.rt),
                         "file:///app/cache/images/a.png")


class LegacyEnvTest(unittest.TestCase):
    """老插件在 Windows 上跑起来所需的环境准备"""

    def test_prepare_env_sets_encoding(self):
        os.environ.pop("PYTHONIOENCODING", None)
        os.environ.pop("PYTHONUTF8", None)
        from astercore.bootstrap import prepare_legacy_env
        with tempfile.TemporaryDirectory() as td:
            prepare_legacy_env(Path(td))
        self.assertEqual(os.environ.get("PYTHONIOENCODING"), "utf-8")
        self.assertEqual(os.environ.get("PYTHONUTF8"), "1")

    def test_cache_dir_created(self):
        from astercore.bootstrap import prepare_legacy_env
        from astercore.compat.paths import default_cache_host
        with tempfile.TemporaryDirectory() as td:
            info = prepare_legacy_env(Path(td))
        self.assertTrue(info.get("cache_dir"))
        self.assertTrue(default_cache_host().exists())
