# -*- coding: utf-8 -*-
# WebView2 三层检测测试（《WebView2 支持检测说明》）
#
# 可测性：所有 Windows 特有输入（build 号、注册表 pv 值）都是注入参数，
# 所以判定表能在 Linux/CI 上**逐行**验证，不需要真的 Windows 机器。
import asyncio
import socket
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from astercore import nativeui, webview2 as wv2
from astercore.app import AppState, validate_transport
from astercore.launch import _build_parser, _random_free_port, decide_transport


# ---------------------------------------------------------------- 第一层：系统版本

class OsLayerTest(unittest.TestCase):
    """规范 §2 第一层判定表，逐行钉住"""

    CASES = [
        # (build, 通过?, 期望 code, 说明)
        (22631, True, wv2.OK, "Windows 11"),
        (22000, True, wv2.OK, "Windows 11 起始 build"),
        (19045, True, wv2.OK, "Windows 10 22H2"),
        (17134, True, wv2.OK, "Windows 10 1803（恰好达标）"),
        (17133, False, wv2.OS_TOO_OLD, "1803 之前"),
        (10240, False, wv2.OS_TOO_OLD, "Windows 10 1507"),
        (9600, False, wv2.OS_TOO_OLD, "Windows 8.1 不支持"),
        (9200, False, wv2.OS_TOO_OLD, "Windows 8 不支持"),
        (7601, True, wv2.OK, "Windows 7 SP1 通过但受限"),
        (7600, False, wv2.OS_TOO_OLD, "Windows 7 无 SP1"),
        (2600, False, wv2.OS_TOO_OLD, "Windows XP"),
    ]

    def test_build_table(self):
        for build, ok, code, label in self.CASES:
            with self.subTest(build=build, label=label):
                got_ok, got_code, _ = wv2.judge_os(build)
                self.assertEqual(got_ok, ok, f"build {build} 通过性不符（{label}）")
                self.assertEqual(got_code, code, f"build {build} code 不符（{label}）")

    def test_non_windows(self):
        ok, code, reason = wv2.judge_os(None)
        self.assertFalse(ok)
        self.assertEqual(code, wv2.NOT_WINDOWS)
        self.assertIn("不是 Windows", reason)

    def test_min_major_win7_is_109(self):
        """规范 §2 第三层：Win7 SP1 / Server 2012 R2 的 WebView2 停在 109"""
        self.assertEqual(wv2.min_major_for(7601), 109)
        self.assertEqual(wv2.min_major_for(9600), 109)   # 8.1 落在同一档判定里
        self.assertEqual(wv2.min_major_for(17134), 92)
        self.assertEqual(wv2.min_major_for(22631), 92)
        self.assertEqual(wv2.min_major_for(None), 92)


# ---------------------------------------------------------------- 第二/三层：Runtime

class RuntimeLayerTest(unittest.TestCase):

    def test_missing_values(self):
        """pv 不存在或为 0.0.0.0 都算未安装"""
        for v in (None, "", "   ", "0.0.0.0"):
            with self.subTest(pv=v):
                ok, code, _ = wv2.judge_runtime(v)
                self.assertFalse(ok)
                self.assertEqual(code, wv2.RUNTIME_MISSING)

    def test_version_threshold_default_92(self):
        for v, ok in (("92.0.902.78", True), ("91.0.864.59", False),
                      ("120.0.2210.91", True), ("109.0.1518.78", True)):
            with self.subTest(pv=v):
                got, code, _ = wv2.judge_runtime(v, 92)
                self.assertEqual(got, ok, v)
                if not ok:
                    self.assertEqual(code, wv2.VERSION_TOO_OLD)

    def test_version_threshold_win7_109(self):
        """Win7 上 109 是最低也是最高可用；108 判定过低"""
        self.assertTrue(wv2.judge_runtime("109.0.1518.78", 109)[0])
        self.assertFalse(wv2.judge_runtime("108.0.1462.54", 109)[0])
        self.assertEqual(wv2.judge_runtime("108.0.1462.54", 109)[1], wv2.VERSION_TOO_OLD)

    def test_unparsable_version_is_missing_not_crash(self):
        ok, code, _ = wv2.judge_runtime("vNext")
        self.assertFalse(ok)
        self.assertEqual(code, wv2.RUNTIME_MISSING)

    def test_parse_major(self):
        self.assertEqual(wv2.parse_major("120.0.2210.91"), 120)
        self.assertEqual(wv2.parse_major(" 109.0.1518.78 "), 109)
        self.assertIsNone(wv2.parse_major(""))
        self.assertIsNone(wv2.parse_major(None))

    def test_registry_locations_match_spec(self):
        """规范 §2 第二层给的三个位置 + 固定 GUID"""
        self.assertEqual(len(wv2.REG_PATHS_HKLM), 2)
        self.assertTrue(wv2.REG_PATHS_HKLM[0].startswith("SOFTWARE\\WOW6432Node"))
        self.assertTrue(wv2.REG_PATHS_HKLM[1].startswith("SOFTWARE\\Microsoft"))
        self.assertTrue(wv2.REG_PATH_HKCU.startswith("SOFTWARE\\Microsoft"))
        for p in list(wv2.REG_PATHS_HKLM) + [wv2.REG_PATH_HKCU]:
            self.assertIn("F3017226-FE2A-4295-8BDF-00C3A9A7E4C5", p)

    def test_registry_reader_degrades_on_non_windows(self):
        """非 Windows 上读注册表不能抛异常，只能返回 None（随后按未安装降级）"""
        if sys.platform.startswith("win"):
            self.skipTest("本机是 Windows")
        self.assertIsNone(wv2.read_runtime_version())


# ---------------------------------------------------------------- 三层合成：四种结果

class DetectTest(unittest.TestCase):
    """规范 §3：三层检测会得到四种结果"""

    def test_all_pass(self):
        st = wv2.detect(windows_build=19045, runtime_reader=lambda: "120.0.2210.91")
        self.assertTrue(st.ok)
        self.assertEqual(st.code, wv2.OK)
        self.assertEqual(st.runtime, "120.0.2210.91")
        self.assertFalse(st.needs_install)

    def test_os_too_old_short_circuits(self):
        """系统不过就不必查注册表了（第一层直接短路）"""
        called = []
        st = wv2.detect(windows_build=9600,
                        runtime_reader=lambda: called.append(1) or "120.0.0.0")
        self.assertFalse(st.ok)
        self.assertEqual(st.code, wv2.OS_TOO_OLD)
        self.assertEqual(called, [], "系统版本不过时不该继续查注册表")

    def test_runtime_missing_needs_install(self):
        st = wv2.detect(windows_build=19045, runtime_reader=lambda: None)
        self.assertFalse(st.ok)
        self.assertEqual(st.code, wv2.RUNTIME_MISSING)
        self.assertTrue(st.needs_install, "未安装属于「装一下就有更好体验」，不能静默降级")

    def test_version_too_old(self):
        st = wv2.detect(windows_build=19045, runtime_reader=lambda: "88.0.705.81")
        self.assertFalse(st.ok)
        self.assertEqual(st.code, wv2.VERSION_TOO_OLD)
        self.assertEqual(st.min_major, 92)
        self.assertTrue(st.needs_install)

    def test_win7_requires_109(self):
        st = wv2.detect(windows_build=7601, runtime_reader=lambda: "100.0.1185.50")
        self.assertFalse(st.ok)
        self.assertEqual(st.code, wv2.VERSION_TOO_OLD)
        self.assertEqual(st.min_major, 109)
        st2 = wv2.detect(windows_build=7601, runtime_reader=lambda: "109.0.1518.78")
        self.assertTrue(st2.ok)

    def test_reader_exception_degrades_not_crashes(self):
        """探测本身出错也必须能降级 —— 绝不能因为读注册表失败就起不来"""
        def boom():
            raise RuntimeError("注册表被策略挡住")
        st = wv2.detect(windows_build=19045, runtime_reader=boom)
        self.assertFalse(st.ok)
        self.assertEqual(st.code, wv2.RUNTIME_MISSING)

    def test_this_machine_is_not_windows(self):
        if sys.platform.startswith("win"):
            self.skipTest("本机是 Windows")
        st = wv2.detect()
        self.assertFalse(st.ok)
        self.assertEqual(st.code, wv2.NOT_WINDOWS)
        self.assertIsNone(wv2.current_build(), "非 Windows 上 build 必须是 None")


# ---------------------------------------------------------------- 原生弹窗

class NativeUiTest(unittest.TestCase):

    def test_message_box_never_raises_off_windows(self):
        if sys.platform.startswith("win"):
            self.skipTest("本机是 Windows")
        self.assertTrue(nativeui.message_box("标题", "内容"))

    def test_ask_yes_no_defaults_false_without_window(self):
        """无窗口环境下必须默认「否」，否则自动化会被误触发"""
        if sys.platform.startswith("win"):
            self.skipTest("本机是 Windows")
        self.assertFalse(nativeui.ask_yes_no("标题", "内容"))

    def test_dialogs_can_be_disabled_for_headless(self):
        with mock.patch.dict("os.environ", {"ASTER_NO_DIALOG": "1"}):
            self.assertTrue(nativeui.dialogs_disabled())
            self.assertTrue(nativeui.message_box("t", "c"))
            self.assertFalse(nativeui.ask_yes_no("t", "c"))


# ---------------------------------------------------------------- 降级告知内容

class DowngradeNoticeTest(unittest.TestCase):
    """规范 §5.1：必须说清「为什么降级 + 面板地址 + 怎么装」"""

    def _capture(self, status, url="http://127.0.0.1:34567"):
        from astercore import launch
        calls = []
        with mock.patch("astercore.nativeui.message_box",
                        lambda t, x, kind="info": calls.append((t, x, kind))):
            launch._explain_downgrade(status, url)
        return calls

    def test_runtime_missing_notice_has_link(self):
        st = wv2.detect(windows_build=19045, runtime_reader=lambda: None)
        calls = self._capture(st)
        self.assertEqual(len(calls), 1, "降级必须弹一次告知框")
        _, text, kind = calls[0]
        self.assertIn("浏览器模式", text)
        self.assertIn("未安装 WebView2", text)
        self.assertIn("http://127.0.0.1:34567", text, "必须给出可复制的面板地址")
        self.assertIn(wv2.WV2_DOWNLOAD_URL, text, "必须给出安装入口")
        self.assertEqual(kind, "warning")

    def test_os_too_old_notice_has_no_install_link(self):
        st = wv2.detect(windows_build=9600, runtime_reader=lambda: "120.0.0.0")
        _, text, kind = self._capture(st)[0]
        self.assertIn("Windows 8.1", text)
        self.assertNotIn(wv2.WV2_DOWNLOAD_URL, text,
                         "系统版本不够时给安装链接是误导")
        self.assertEqual(kind, "info")


# ---------------------------------------------------------------- transport 配置

class TransportConfigTest(unittest.TestCase):

    def test_validate(self):
        for m in ("auto", "webview", "http"):
            self.assertIsNone(validate_transport(m))
        for bad in ("", "win", "WebView", "none"):
            self.assertIsNotNone(validate_transport(bad))

    def test_default_is_auto_and_persists(self):
        with tempfile.TemporaryDirectory() as td:
            st = AppState(Path(td))
            st.load()
            self.assertEqual(st.transport_mode(), "auto")
            ok, err = st.set_transport("http")
            self.assertTrue(ok, err)
            again = AppState(Path(td))
            again.load()
            self.assertEqual(again.transport_mode(), "http", "设置要落盘")

    def test_invalid_value_rejected_and_illegal_file_falls_back(self):
        with tempfile.TemporaryDirectory() as td:
            st = AppState(Path(td))
            st.load()
            ok, err = st.set_transport("bogus")
            self.assertFalse(ok)
            self.assertIn("transport", err)
            self.assertEqual(st.transport_mode(), "auto")
            # 手工把配置写坏 → 读取时必须退回 auto，不能把非法值当命令执行
            st.data["transport"] = {"mode": "whatever"}
            self.assertEqual(st.transport_mode(), "auto")


# ---------------------------------------------------------------- 通道决策

class DecideTransportTest(unittest.TestCase):
    """规范 §六：全部通过才走 WebView2 模式；用户指定时跳过自动检测"""

    def _args(self, *argv):
        return _build_parser().parse_args(list(argv))

    def _state(self, mode="auto"):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        st = AppState(Path(td.name))
        st.load()
        if mode != "auto":
            st.set_transport(mode)
        return st

    def test_transport_http_skips_detection(self):
        with mock.patch("astercore.shell.ShellApp.webview_available",
                        staticmethod(lambda: True)), \
             mock.patch("astercore.webview2.detect") as d:
            mode, st = decide_transport(self._args(), self._state("http"))
        self.assertEqual(mode, "http")
        self.assertIsNone(st)
        d.assert_not_called()

    def test_transport_webview_skips_detection(self):
        with mock.patch("astercore.shell.ShellApp.webview_available",
                        staticmethod(lambda: True)), \
             mock.patch("astercore.webview2.detect") as d:
            mode, st = decide_transport(self._args(), self._state("webview"))
        self.assertEqual(mode, "webview")
        self.assertIsNone(st)
        d.assert_not_called()

    def test_forced_webview_without_pywebview_degrades(self):
        with mock.patch("astercore.shell.ShellApp.webview_available",
                        staticmethod(lambda: False)):
            mode, st = decide_transport(self._args(), self._state("webview"))
        self.assertEqual(mode, "http", "强制内嵌但没装 pywebview 时不能装作成功")
        self.assertIsNone(st)

    def test_no_shell_flag_forces_http(self):
        mode, st = decide_transport(self._args("--no-shell"), self._state())
        self.assertEqual(mode, "http")
        self.assertIsNone(st)

    def test_auto_without_pywebview_is_http(self):
        with mock.patch("astercore.shell.ShellApp.webview_available",
                        staticmethod(lambda: False)):
            mode, st = decide_transport(self._args(), self._state())
        self.assertEqual(mode, "http")
        self.assertIsNone(st, "没装 pywebview 就没必要做 WebView2 检测")

    def test_auto_all_pass_uses_webview(self):
        ok = wv2.detect(windows_build=19045, runtime_reader=lambda: "120.0.2210.91")
        with mock.patch("astercore.shell.ShellApp.webview_available",
                        staticmethod(lambda: True)), \
             mock.patch("astercore.webview2.detect", lambda: ok):
            mode, st = decide_transport(self._args(), self._state())
        self.assertEqual(mode, "webview")
        self.assertTrue(st.ok)

    def test_auto_detection_failure_degrades_and_reports(self):
        """降级时要**把检测结果带出去**，调用方才知道该弹窗解释"""
        bad = wv2.detect(windows_build=19045, runtime_reader=lambda: None)
        with mock.patch("astercore.shell.ShellApp.webview_available",
                        staticmethod(lambda: True)), \
             mock.patch("astercore.webview2.detect", lambda: bad):
            mode, st = decide_transport(self._args(), self._state())
        self.assertEqual(mode, "http")
        self.assertIsNotNone(st)
        self.assertEqual(st.code, wv2.RUNTIME_MISSING)

    @unittest.skipIf(sys.platform.startswith("win"),
                     "本用例验证的是**非 Windows** 分支：Windows 上 WebView2 检测本来就适用，"
                     "detect() 会返回真实状态而不是 None（真机验证时抓到过）")
    def test_non_windows_with_pywebview_uses_webview(self):
        """Linux/macOS：WebView2 检测不适用，有 pywebview 就直接用"""
        with mock.patch("astercore.shell.ShellApp.webview_available",
                        staticmethod(lambda: True)):
            mode, st = decide_transport(self._args(), self._state())
        self.assertEqual(mode, "webview")
        self.assertIsNone(st)


# ---------------------------------------------------------------- 回退端口

class FallbackPortTest(unittest.TestCase):

    def test_random_free_port_is_bindable(self):
        for _ in range(5):
            p = _random_free_port()
            self.assertTrue(1 <= p <= 65535)
            with socket.socket() as s:
                s.bind(("127.0.0.1", p))      # 应该还没人占


if __name__ == "__main__":
    unittest.main()
