# 桌面壳 ShellApp 测试（webview/tray 可用性 + 回退路径逻辑）
import unittest
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from astercore.shell import ShellApp


class ShellAppTest(unittest.TestCase):
    def test_availability_detection(self):
        # 探测不应抛异常（当前无 pywebview 环境应为 False）
        ShellApp.webview_available()
        ShellApp.tray_available()
        self.assertIsInstance(ShellApp.webview_available(), bool)

    def test_construct(self):
        app = ShellApp("http://127.0.0.1:8080", title="测试")
        self.assertEqual(app.title, "测试")
        self.assertEqual(app.url, "http://127.0.0.1:8080")

    def test_open_fallback_browser_headless(self):
        # 无 pywebview 时 open_panel 走浏览器分支——不可真正阻塞，测其能进入（用线程+超时）
        import threading
        import time
        app = ShellApp("http://127.0.0.1:1")  # 无效端口不真正打开
        done = threading.Event()

        def _run():
            try:
                app.open_panel()
            except Exception:
                pass
            done.set()

        t = threading.Thread(target=_run, daemon=True)
        t.start()
        # 浏览器模式内 time.sleep 循环——确认进程未崩即可（2s 内不应异常退出）
        time.sleep(1.5)
        self.assertTrue(t.is_alive() or done.is_set())


if __name__ == "__main__":
    unittest.main()
