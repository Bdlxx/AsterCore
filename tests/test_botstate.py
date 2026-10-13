# -*- coding: utf-8 -*-
"""程序机器人状态机（托盘与主界面共用的那份状态）单测

规格要点（用户给的）：
  - "机器人状态"指**本程序内部核心**，与 NapCat 在线与否无关
  - 启停按钮文案随状态变化；启动中/停止中要禁用；错误态文案回到"启动机器人"
  - 重启：先停后起，期间 停止中 → 启动中 → 运行中
"""
import asyncio
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from astercore import botstate as B  # noqa: E402


class FakeManager:
    def __init__(self, accounts=None, fail_start=False):
        self.accounts = accounts if accounts is not None else [{"account_id": "10001",
                                                               "display_name": "星汐"}]
        self.running = set()
        self.actions = []
        self.fail_start = fail_start
        self.delay = 0.0

    def scan(self):
        return [{"account_id": a["account_id"], "display_name": a["display_name"],
                 "running": a["account_id"] in self.running} for a in self.accounts]

    async def start(self, aid):
        self.actions.append(("start", aid))
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.fail_start:
            raise RuntimeError("模拟启动失败")
        self.running.add(aid)

    async def stop(self, aid):
        self.actions.append(("stop", aid))
        if self.delay:
            await asyncio.sleep(self.delay)
        self.running.discard(aid)


def _wait_state(ctl, want, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        if ctl.state() == want:
            return True
        time.sleep(0.02)
    return False


class BotStateTest(unittest.TestCase):
    def setUp(self):
        self.cur = {"aid": ""}
        self.mgr = FakeManager()

    def _ctl(self, **kw):
        return B.BotController(self.mgr, loop_provider=None,
                               state_get=lambda: self.cur["aid"],
                               state_set=lambda a: self.cur.update(aid=a), **kw)

    # ---------- 基础状态与文案 ----------
    def test_initial_stopped_with_labels(self):
        ctl = self._ctl()
        s = ctl.snapshot()
        self.assertEqual(s["state"], B.STOPPED)
        self.assertEqual(s["state_label"], "已停止")
        self.assertEqual(s["button"], "启动机器人")
        self.assertTrue(s["button_enabled"])
        self.assertFalse(s["restart_enabled"], "非运行中不能重启")
        self.assertEqual(s["nickname"], "星汐")

    def test_no_account_is_stopped_then_error_on_start(self):
        ctl = B.BotController(FakeManager(accounts=[]), state_get=lambda: "",
                              state_set=lambda a: None)
        self.assertEqual(ctl.state(), B.STOPPED)
        self.assertEqual(ctl.snapshot()["nickname"], "（未配置账号）")
        ok, msg = ctl.start()
        self.assertFalse(ok)
        self.assertIn("账号", msg)
        self.assertEqual(ctl.state(), B.ERROR)
        self.assertEqual(ctl.snapshot()["button"], "启动机器人")

    def test_current_account_falls_back_to_first(self):
        mgr = FakeManager(accounts=[{"account_id": "2", "display_name": "B"},
                                    {"account_id": "1", "display_name": "A"}])
        ctl = B.BotController(mgr, state_get=lambda: "missing", state_set=lambda a: None)
        self.assertEqual(ctl.current_account(), "1", "当前账号无效时回退到第一个")

    # ---------- 起停 ----------
    def test_start_then_running(self):
        ctl = self._ctl()
        ok, _ = ctl.start()
        self.assertTrue(ok)
        self.assertTrue(_wait_state(ctl, B.RUNNING), ctl.snapshot())
        s = ctl.snapshot()
        self.assertEqual(s["button"], "停止机器人")
        self.assertTrue(s["restart_enabled"])
        self.assertEqual(self.mgr.actions, [("start", "10001")])

    def test_starting_is_disabled_and_says_starting(self):
        self.mgr.delay = 0.4
        ctl = self._ctl()
        ctl.start()
        time.sleep(0.1)
        s = ctl.snapshot()
        self.assertEqual(s["state"], B.STARTING)
        self.assertEqual(s["button"], "启动中…")
        self.assertFalse(s["button_enabled"], "启动中必须禁用")
        self.assertFalse(s["restart_enabled"])
        ok, msg = ctl.start()
        self.assertFalse(ok)
        self.assertIn("正在处理", msg)
        self.assertTrue(_wait_state(ctl, B.RUNNING))

    def test_stop_then_stopped(self):
        self.mgr.running.add("10001")
        ctl = self._ctl()
        self.assertEqual(ctl.state(), B.RUNNING)
        ctl.stop()
        self.assertTrue(_wait_state(ctl, B.STOPPED))
        self.assertEqual(ctl.snapshot()["button"], "启动机器人")
        self.assertIn(("stop", "10001"), self.mgr.actions)

    def test_start_failure_goes_error(self):
        self.mgr.fail_start = True
        ctl = self._ctl()
        ctl.start()
        self.assertTrue(_wait_state(ctl, B.ERROR))
        s = ctl.snapshot()
        self.assertEqual(s["state_label"], "错误")
        self.assertEqual(s["button"], "启动机器人", "错误态文案回到启动，点了可重试")
        self.assertIn("模拟启动失败", s["error"])

    # ---------- 重启 ----------
    def test_restart_stops_then_starts(self):
        self.mgr.running.add("10001")
        ctl = self._ctl()
        ok, _ = ctl.restart()
        self.assertTrue(ok)
        self.assertTrue(_wait_state(ctl, B.RUNNING))
        self.assertEqual(self.mgr.actions, [("stop", "10001"), ("start", "10001")],
                         "重启必须先停后起")

    def test_restart_only_when_running(self):
        ctl = self._ctl()
        ok, msg = ctl.restart()
        self.assertFalse(ok)
        self.assertIn("运行中", msg)
        self.assertEqual(self.mgr.actions, [])

    def test_restart_transient_sequence(self):
        self.mgr.running.add("10001")
        self.mgr.delay = 0.3
        ctl = self._ctl()
        ctl.restart()
        time.sleep(0.1)
        self.assertEqual(ctl.state(), B.STOPPING)
        self.assertEqual(ctl.snapshot()["button"], "停止中…")
        self.assertFalse(ctl.snapshot()["button_enabled"])
        time.sleep(0.35)
        self.assertEqual(ctl.state(), B.STARTING)
        self.assertTrue(_wait_state(ctl, B.RUNNING))

    # ---------- 同步 ----------
    def test_on_change_callback_fires(self):
        hits = []
        ctl = B.BotController(self.mgr, state_get=lambda: "", state_set=lambda a: None,
                              on_change=lambda: hits.append(1))
        ctl.start()
        self.assertTrue(_wait_state(ctl, B.RUNNING))
        self.assertGreaterEqual(len(hits), 2, "状态变化要通知（托盘据此刷新菜单）")

    def test_set_current_account_notifies(self):
        mgr = FakeManager(accounts=[{"account_id": "1", "display_name": "A"},
                                    {"account_id": "2", "display_name": "B"}])
        cur = {"aid": ""}
        hits = []
        ctl = B.BotController(mgr, state_get=lambda: cur["aid"],
                              state_set=lambda a: cur.update(aid=a),
                              on_change=lambda: hits.append(1))
        ctl.set_current_account("2")
        self.assertEqual(ctl.current_account(), "2")
        self.assertEqual(len(hits), 1)


if __name__ == "__main__":
    unittest.main()
