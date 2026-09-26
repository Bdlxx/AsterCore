# 栖星 AsterCore · 桌面壳窗口层
#
# 施工手册 §1.1 宿主形态 / §1.2 js_api 进程内 RPC / §2.1 开窗时机 / §8.1 关窗隐藏到托盘。
#
# 与旧版的区别（这是本次架构改动的核心）：
#   旧版：window 加载 http://127.0.0.1:8080，Flask 永远在后台跑 —— 桌面版被迫占端口，
#         还要处理"浏览器登录态"。
#   新版：window 直接加载**本地面板文件**，所有 API 调用走 js_api → RpcHub →
#         进程内 test_client。**HTTP 服务默认不启动**（想要手机/别的电脑访问再去面板打开）。
#
# pywebview 的三个硬性约束（手册 §1.1 的坑）：
#   1. webview.start() 必须在主线程 —— 所以机器人/Web 服务一律后台线程
#   2. js_api 对象上以 _ 开头的方法不会被暴露 —— JsApi 的公开面只有 rpc()
#   3. 打包时不写 hiddenimports 会报 "No webview platform" —— 见 pack/astercore.spec

from __future__ import annotations

import logging
import threading
import webbrowser
from pathlib import Path
from typing import Any, Callable

log = logging.getLogger("astercore.shell")


class JsApi:
    """暴露给前端 `window.pywebview.api` 的对象。

    只暴露一个公开方法 `rpc`：所有能力都从 RpcHub 走（手册 §1.4 单一 dispatch）。
    pywebview 只暴露**不以 _ 开头**的公开可调用成员，所以内部状态用 _hub 存。
    """

    def __init__(self, hub) -> None:
        self._hub = hub

    def rpc(self, method: str, params: dict | None = None) -> dict:
        return self._hub.dispatch(method, params or {})


class ShellApp:
    """桌面壳：本地窗口 + js_api RPC + 关窗进托盘 + 可选托盘菜单。

    hub 为空时退化为旧行为（打开 URL / 系统浏览器），保证 --shell 在没有
    pywebview 的环境（比如 CI）里不会炸。
    """

    def __init__(self, url: str = "", on_quit: Callable[[], None] | None = None,
                 title: str = "栖星 AsterCore", tray: bool = True,
                 hub=None, html_path: str | Path | None = None,
                 on_ready: Callable[[], None] | None = None,
                 debug: bool = False,
                 width: int = 1120, height: int = 760) -> None:
        self.url = url
        self.on_quit = on_quit
        self.title = title
        self.tray = tray
        self.hub = hub
        self.html_path = Path(html_path) if html_path else None
        self.on_ready = on_ready
        self.debug = debug
        self.width = width
        self.height = height
        self._window = None
        self._tray_icon = None
        self._quitting = False
        self._ready_started = False

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
            import PIL  # noqa: F401
            return True
        except Exception:
            return False

    # ---------- 窗口目标 ----------
    def target(self) -> str:
        """窗口加载什么。

        有 hub + 本地面板文件 → 直接加载文件（file://），**不需要任何端口**；
        否则退回 URL（旧行为）。
        """
        if self.hub is not None and self.html_path and self.html_path.is_file():
            return self.html_path.resolve().as_uri()
        return self.url

    # ---------- 事件 ----------
    def _on_closing(self) -> bool:
        """点 X：隐藏到托盘而不是退出（手册 §8.1）。

        返回 False = 阻止默认关闭。真正的退出只走托盘「退出」或 app.quit。
        没有托盘可用时不拦截 —— 否则用户会陷入"关不掉又找不到托盘图标"。
        """
        if self._quitting:
            return True
        if not (self.tray and ShellApp.tray_available()):
            return True
        try:
            win = self._window
            if win is not None and hasattr(win, "hide"):
                win.hide()
                log.info("窗口已隐藏到托盘（退出请用托盘菜单）")
                return False
        except Exception as e:                    # noqa: BLE001 — 隐藏失败就让它正常关
            log.warning("隐藏窗口失败，按正常关闭处理: %s", e)
        return True

    def _on_loaded(self) -> None:
        """窗口已显示 → 再跑重活（手册 §2.1：别在开窗前干重活）。

        机器人启动 / Web 服务启动都在这里丢后台线程；窗口本身 0 秒级出现。
        """
        if self._ready_started or self.on_ready is None:
            return
        self._ready_started = True
        threading.Thread(target=self._run_ready, daemon=True).start()

    def _run_ready(self) -> None:
        try:
            self.on_ready()
        except Exception as e:                    # noqa: BLE001
            log.warning("启动后置任务失败: %s", e)

    # ---------- 打开 ----------
    def open_panel(self) -> None:
        """pywebview 内嵌窗口；不可用则系统浏览器打开（阻塞）"""
        if ShellApp.webview_available():
            try:
                import webview
                js_api = JsApi(self.hub) if self.hub is not None else None
                self._window = webview.create_window(
                    self.title, self.target(), js_api=js_api,
                    width=self.width, height=self.height, min_size=(900, 600),
                    confirm_close=False,     # 自己拦截关闭（手册 §1.1）
                )
                try:
                    self._window.events.closing += self._on_closing
                    self._window.events.loaded += self._on_loaded
                except Exception as e:            # noqa: BLE001 — 老版本 pywebview 没这些事件
                    log.warning("窗口事件挂载失败（功能降级）: %s", e)
                webview.start(debug=self.debug)   # 阻塞直到窗口关闭（必须主线程）
                log.info("内嵌窗口已关闭")
                return
            except Exception as e:
                log.warning("pywebview 启动失败，回退浏览器: %s", e)
        if not self.url:
            log.error("无 pywebview 且没有可打开的 URL —— 桌面壳无法工作")
            return
        log.info("使用系统浏览器打开: %s", self.url)
        threading.Thread(target=lambda: webbrowser.open(self.url),
                         daemon=True).start()
        # 浏览器模式：阻塞等待（Ctrl+C 由外层处理）
        import time
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            pass

    # ---------- 托盘 ----------
    def _make_icon_image(self):
        from PIL import Image, ImageDraw
        img = Image.new("RGB", (64, 64), (20, 24, 32))
        d = ImageDraw.Draw(img)
        d.ellipse((14, 14, 50, 50), fill=(110, 168, 254))
        d.polygon([(32, 10), (36, 26), (52, 28), (38, 34),
                   (44, 50), (32, 38), (20, 50), (26, 34),
                   (12, 28), (28, 26)], fill=(255, 255, 255))
        return img

    def quit(self) -> None:
        """真正退出：销毁窗口 → webview.start() 返回 → 外层收尾"""
        self._quitting = True
        try:
            if self._tray_icon is not None:
                self._tray_icon.stop()
        except Exception:                          # noqa: BLE001
            pass
        try:
            if self._window is not None:
                self._window.destroy()
        except Exception as e:                     # noqa: BLE001
            log.warning("销毁窗口失败: %s", e)

    def _show_window(self) -> None:
        try:
            if self._window is not None:
                self._window.show()
        except Exception as e:                     # noqa: BLE001
            log.debug("显示窗口失败: %s", e)

    def run_tray(self, open_panel_cb: Callable[[], None] | None = None) -> None:
        if not (self.tray and ShellApp.tray_available()):
            return
        try:
            import pystray

            def _on_open(icon, item):
                self._show_window()

            def _on_quit(icon, item):
                self.quit()

            menu = pystray.Menu(
                pystray.MenuItem("打开面板", _on_open, default=True),
                pystray.MenuItem("退出", _on_quit),
            )
            self._tray_icon = pystray.Icon("astercore", self._make_icon_image(),
                                           self.title, menu)
            self._tray_icon.run()
        except Exception as e:                     # noqa: BLE001
            log.warning("托盘不可用: %s", e)

    # ---------- 统一入口 ----------
    def run(self, with_tray: bool = True) -> None:
        """阻塞运行：托盘线程 + 面板窗口"""
        if with_tray and self.tray and ShellApp.tray_available():
            threading.Thread(target=self.run_tray, daemon=True).start()
        self.open_panel()
        if self.on_quit:
            self.on_quit()
