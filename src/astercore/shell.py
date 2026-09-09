# 栖星 AsterCore · 桌面壳窗口层
# 负责把面板装进桌面窗口：pywebview 内嵌（默认）→ 不可用时回退系统浏览器。
# 托盘（pystray）可选；web 服务始终后台线程运行（进程内 bridge 改造前的过渡）。
# 编译（PyInstaller）后 AsteroCore.exe 直接调 ShellApp.run。

from __future__ import annotations

import logging
import threading
import webbrowser
from typing import Callable

log = logging.getLogger("astercore.shell")


class ShellApp:
    """桌面壳：后台 Web 面板 + 内嵌/浏览器打开 + 可选托盘。"""

    def __init__(self, url: str,
                 on_quit: Callable[[], None] | None = None,
                 title: str = "栖星 AsterCore",
                 tray: bool = True) -> None:
        self.url = url
        self.on_quit = on_quit
        self.title = title
        self.tray = tray
        self._window = None
        self._tray_icon = None
        self._webview_ok = False

    # ---------- 探测 ----------
    @staticmethod
    def webview_available() -> bool:
        try:
            import webview  # noqa: F401
            return True
        except Exception:
            return False

    @staticmethod
    def tray_available() -> bool:
        try:
            import pystray  # noqa: F401
            return True
        except Exception:
            return False

    # ---------- 打开 ----------
    def open_panel(self) -> None:
        """pywebview 内嵌窗口；不可用则系统浏览器打开"""
        if ShellApp.webview_available():
            try:
                import webview
                self._window = webview.create_window(
                    self.title, self.url, width=1120, height=760, min_size=(800, 560))
                webview.start()  # 阻塞直到窗口关闭
                log.info("内嵌窗口已关闭")
                return
            except Exception as e:
                log.warning("pywebview 启动失败，回退浏览器: %s", e)
        log.info("使用系统浏览器打开: %s", self.url)
        threading.Thread(target=lambda: webbrowser.open(self.url),
                         daemon=True).start()
        # 浏览器模式：阻塞等待（Ctrl+C 由外层处理）；这里挂起
        import time
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            pass

    # ---------- 托盘（可选） ----------
    def run_tray(self, open_panel_cb: Callable[[], None] | None = None) -> None:
        if not (self.tray and ShellApp.tray_available()):
            return
        try:
            import pystray
            from PIL import Image, ImageDraw

            def _make_icon_image():
                img = Image.new("RGB", (64, 64), (20, 24, 32))
                d = ImageDraw.Draw(img)
                d.ellipse((14, 14, 50, 50), fill=(110, 168, 254))
                d.polygon([(32, 10), (36, 26), (52, 28), (38, 34),
                           (44, 50), (32, 38), (20, 50), (26, 34),
                           (12, 28), (28, 26)], fill=(255, 255, 255))
                return img

            def _on_open(icon, item):
                cb = open_panel_cb or self.open_panel
                threading.Thread(target=cb, daemon=True).start()

            def _on_quit(icon, item):
                icon.stop()
                if self.on_quit:
                    self.on_quit()

            menu = pystray.Menu(
                pystray.MenuItem("打开面板", _on_open, default=True),
                pystray.MenuItem("退出", _on_quit),
            )
            self._tray_icon = pystray.Icon("astercore", _make_icon_image(),
                                           "栖星 AsterCore", menu)
            self._tray_icon.run()
        except Exception as e:
            log.warning("托盘不可用: %s", e)

    # ---------- 统一入口 ----------
    def run(self, with_tray: bool = True) -> None:
        """阻塞运行：托盘线程 + 面板打开（内嵌阻塞或浏览器挂起）"""
        if with_tray and self.tray and ShellApp.tray_available():
            threading.Thread(target=self.run_tray,
                             args=(self.open_panel,), daemon=True).start()
        self.open_panel()
        if self.on_quit:
            self.on_quit()
