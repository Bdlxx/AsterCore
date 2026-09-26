# 栖星 AsterCore · 启动器（桌面/控制台入口）
# 流程：解析路径 → 首启引导（建目录+播种示例插件）→ 运行方式向导（可跳过）
#       → 启动账号管理器 + Web 面板 → 自动打开浏览器。
# 用法：python -m astercore.launch [--port 8080] [--no-browser] [--yes] [--shell|--no-shell]
#       默认：有 pywebview 就开桌面窗口（进程内 RPC，不占端口）；否则回退浏览器模式。
#       Web 服务（手机/别的电脑访问）默认关闭，用 --web 或在面板「安全设置」里打开。
#       python -m astercore            （零参数 = 本启动器）

from __future__ import annotations

import argparse
import asyncio
import logging
import socket
import sys
from pathlib import Path

from astercore import __version__
from astercore import paths as _paths
from astercore.app import validate_web_cfg
from astercore.shell import ShellApp

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("astercore.launch")

DEFAULT_MODE = "napcat"          # 向导推荐项（最安全：走官方客户端通道）
FALLBACK_PORTS = 10              # 端口被占用时向后尝试的数量


def _console_wizard(state) -> str:
    """首启向导：返回选定的 backend_mode。

    非交互环境（无控制台/输入被重定向/服务方式启动）直接采用推荐模式，
    避免双击 exe 时卡在一个没人回答的提问上。
    """
    from astercore.app import BACKEND_MODES

    interactive = True
    try:
        interactive = bool(sys.stdin and sys.stdin.isatty())
    except Exception:
        interactive = False

    if not interactive:
        ok, msg = state.set_backend(DEFAULT_MODE, ack_risk=True)
        print(f"[wizard] 非交互环境，使用推荐运行方式 {DEFAULT_MODE}（可稍后在面板修改）")
        return DEFAULT_MODE

    print("\n===== 栖星 AsterCore · 首启向导 =====")
    for k, m in BACKEND_MODES.items():
        stars = "★" * m["risk"] + "☆" * (5 - m["risk"])
        mark = "（推荐）" if k == DEFAULT_MODE else ""
        print(f"  {k:10} {m['label']} {mark}  [{stars}]")
        print(f"             {m['desc']}")
    print(f"\n直接回车 = 使用推荐项 {DEFAULT_MODE}")

    while True:
        try:
            raw = input("选择运行方式（napcat / lagrange / null）: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print(f"\n[wizard] 未收到输入，使用推荐项 {DEFAULT_MODE}")
            raw = DEFAULT_MODE

        mode = raw or DEFAULT_MODE
        if mode not in BACKEND_MODES:
            print("  未知选项，请重新输入（或直接回车使用推荐项）")
            continue

        meta = BACKEND_MODES[mode]
        if meta["needs_ack"]:
            try:
                ack = input("⚠️ 该模式非官方协议，存在封号风险（建议小号）。输入 yes 确认: ")
            except (EOFError, KeyboardInterrupt):
                ack = ""
            if ack.strip().lower() != "yes":
                print("  已取消，请重新选择")
                continue
        ok, msg = state.set_backend(mode, ack_risk=True)
        print(("  ✓ " if ok else "  ✗ ") + msg)
        if ok:
            return mode


def _pick_port(host: str, port: int, tries: int = FALLBACK_PORTS) -> int:
    """端口被占用时自动向后找一个可用端口（避免二次启动直接失败）"""
    for i in range(tries):
        p = port + i
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind((host, p))
                return p
            except OSError:
                continue
    return port


async def amain(args: argparse.Namespace) -> int:
    from astercore.app import AppState
    from astercore.bootstrap import bootstrap
    from astercore.core.manager import AccountManager
    from astercore.web.server import ManagerWebPanel, serve_in_background

    base = _paths.app_base_dir()
    data_root = _paths.resolve_data_dir(args.data_dir)
    accounts_dir = _paths.resolve_accounts_dir(args.accounts_dir)
    plugins_dir = Path(args.plugins_dir) if args.plugins_dir else _paths.default_plugins_dir()

    # 首启引导：建目录 + 播种示例插件（用户第一次运行就能看到插件在工作）
    info = bootstrap(base, plugin_dir=plugins_dir)
    seeded = info.get("seeded") or []

    state = AppState(data_root)
    state.load()

    print("=" * 60)
    print(f"  栖星 AsterCore v{__version__}")
    print("=" * 60)
    print(f"  基准目录 : {base}")
    print(f"  数据目录 : {data_root}")
    print(f"  账号目录 : {accounts_dir}")
    print(f"  插件目录 : {plugins_dir}  ← 插件(.py/.pyd/.dll)放这里")
    if seeded:
        print(f"  已播种示例插件: {', '.join(seeded)}")

    # 向导决策（--yes 跳过）
    if args.yes:
        if state.needs_wizard():
            state.set_backend(DEFAULT_MODE, ack_risk=True)
        print(f"[launch] 运行方式: {state.backend_mode}（--yes 跳过向导）")
    elif state.needs_wizard():
        _console_wizard(state)
    else:
        print(f"[launch] 运行方式: {state.backend_mode}（跳过向导，重置请在面板操作）")

    # 账号 + 后端
    import astercore.backends.null    # noqa: F401
    import astercore.backends.onebot  # noqa: F401
    mgr = AccountManager(accounts_dir, plugin_dir=plugins_dir, data_root=data_root)
    started, failed = [], []
    for a in mgr.scan():
        try:
            await mgr.start(a["account_id"])
            started.append(a["account_id"])
        except Exception as e:
            failed.append((a["account_id"], str(e)))
            print(f"[launch] 账号 {a['account_id']} 启动失败: {e}")
    print(f"[launch] 已启动账号: {started or '（无）'}")
    if failed:
        print(f"[launch] 启动失败: {', '.join(a for a, _ in failed)}")

    # 面板：HTTP 与进程内 RPC **共用同一份路由**（手册 §1.4 单一 dispatch）
    loop = asyncio.get_running_loop()
    panel = ManagerWebPanel(mgr, loop_provider=lambda: loop, app_state=state)
    from astercore.rpc import build_hub
    hub = build_hub()
    hub.bind_panel(panel)

    # 通道决策（WebView2 检测说明 §六 + 施工手册 §1.2/§3.1）：
    #   auto   → 三层检测，全过才走内嵌窗口，否则降级 HTTP 并**明确告知原因**
    #   webview/http → 用户手动指定，跳过自动检测
    web_cfg = state.web_config()
    # 命令行显式给 --port/--host = 本次运行要开 Web（**不写回 runtime.json**：
    # 命令行是"这一次"的覆盖，面板里的开关才是持久设置）
    if getattr(args, "host", None):
        web_cfg["host"] = args.host
    if getattr(args, "port", None):
        web_cfg["port"] = args.port
        web_cfg["enabled"] = True
    if getattr(args, "web", False):
        web_cfg["enabled"] = True
    if getattr(args, "no_web", False):
        web_cfg["enabled"] = False

    transport, wv2 = decide_transport(args, state)
    desktop = transport == "webview"
    if wv2 is not None:
        from astercore.webview2 import arch_note
        print(f"[launch] WebView2 检测: {wv2.code} — {wv2.reason}")
        note = arch_note()
        if note:
            print(f"[launch] {note}")

    http_url = ""
    fallback = False          # True = 因为检测不过而本地兜底（≠ 用户主动开 Web）
    if web_cfg["enabled"]:
        # 【主动开 Web】用户配置的 host/port，可对外，强制密码（见 validate_web_cfg）
        err = validate_web_cfg(web_cfg, panel.auth.mode)
        if err:
            print(f"[launch] Web 服务未启动：{err}")
        else:
            bind_host = web_cfg["host"]
            port = _pick_port(bind_host, web_cfg["port"])
            if port != web_cfg["port"]:
                print(f"[launch] 端口 {web_cfg['port']} 被占用，改用 {port}")
            serve_in_background(panel, host=bind_host, port=port)
            # 本机访问地址永远用回环地址 —— 监听 0.0.0.0 时拿它当客户端 URL 是连不上的
            client_host = "127.0.0.1" if bind_host in ("0.0.0.0", "::", "") else bind_host
            http_url = f"http://{client_host}:{port}"
            print(f"[launch] Web 面板已就绪: {http_url}")
    elif desktop:
        print("[launch] Web 服务未开启（内嵌窗口走进程内 RPC，不需要端口）")
        print("[launch] 需要手机/别的电脑访问时，在面板里打开 Web 服务")
    else:
        # 【本地回退】规范 §5.2：这是"没办法的办法"，与主动开 Web 是两件事 ——
        # 只绑回环 + **随机端口**，绝不用用户配置里的那个端口，也不对外。
        port = _random_free_port()
        serve_in_background(panel, host="127.0.0.1", port=port)
        http_url = f"http://127.0.0.1:{port}"
        fallback = True
        print(f"[launch] 本地回退：面板地址 {http_url}")

    if not started:
        print("[launch] 提示：打开面板 → 账号 → 新建账号 填入你的 QQ 号与 NapCat 地址")

    if desktop:
        return await _run_desktop(args, state, panel, hub, mgr, http_url)

    # 降级告知（规范 §5.1）：不能静默打开浏览器。必须说清"为什么 + 地址 + 怎么装"
    #
    # 只在**本地回退**（fallback）时才弹：用户如果本来就主动开了 Web 服务，
    # 浏览器模式就是他要的结果，再去解释一遍是噪音；而且弹窗是阻塞的，
    # 无头环境（CI/e2e）里没人点会一直卡住。
    if fallback and wv2 is not None and not wv2.ok:
        _explain_downgrade(wv2, http_url)

    _start_tray_for_http(hub, http_url, state)

    import os as _os
    if not args.no_browser and not _os.environ.get("ASTER_NO_OPEN"):
        import threading
        threading.Timer(0.8, _open_browser, args=(http_url,)).start()

    # 规范 §5.3：HTTP 模式下关掉浏览器标签 ≠ 退出程序，托盘常驻。
    # 托盘「退出」通过线程安全的 event 唤醒主循环。
    stop = asyncio.Event()
    _bind_quit(loop, stop)
    try:
        await stop.wait()
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        await mgr.stop_all()
    return 0


# ---------------------------------------------------------------- 通道决策与降级体验

_QUIT_HOOKS: list = []


def _bind_quit(loop: asyncio.AbstractEventLoop, stop: asyncio.Event) -> None:
    """把"退出"回调交给托盘菜单（线程 → 事件循环）"""
    def _quit() -> None:
        loop.call_soon_threadsafe(stop.set)

    _QUIT_HOOKS.append(_quit)


def _take_quit() -> "callable | None":
    return _QUIT_HOOKS.pop() if _QUIT_HOOKS else None


def decide_transport(args, state) -> tuple[str, object | None]:
    """决定走内嵌窗口还是浏览器（《WebView2 支持检测说明》§四/§六）。

    返回 (transport, 检测结果)。检测结果只在"真的做了检测"时非空 ——
    这样调用方就能区分「用户自己选了 HTTP」和「检测不过被迫降级」，
    只有后者才需要弹窗解释。
    """
    from astercore.shell import ShellApp
    have_webview = ShellApp.webview_available()

    if getattr(args, "no_shell", False):
        return "http", None                     # 用户明确要浏览器模式
    if getattr(args, "shell", False):
        # 强制内嵌：调试用；缺 pywebview 也不能装作成功
        if not have_webview:
            print("[launch] --shell 要求内嵌窗口，但未安装 pywebview（pip install astercore[desktop]）")
        return ("webview" if have_webview else "http"), None

    mode = state.transport_mode()
    if mode == "http":
        print("[launch] transport=http（配置指定），跳过 WebView2 检测")
        return "http", None
    if mode == "webview":
        print("[launch] transport=webview（配置指定），跳过 WebView2 检测")
        if not have_webview:
            print("[launch] 未安装 pywebview，无法强制内嵌窗口，改为浏览器模式")
        return ("webview" if have_webview else "http"), None

    # auto：没装 pywebview 就不用检测了（开发机/CI 常见）
    if not have_webview:
        return "http", None
    from astercore.webview2 import NOT_WINDOWS, detect
    st = detect()
    if st.code == NOT_WINDOWS:
        # 非 Windows（Linux/macOS）：WebView2 检测不适用，有 pywebview 就用
        return "webview", None
    return ("webview" if st.ok else "http"), st


def _explain_downgrade(status, url: str) -> None:
    """降级必须明确告知（规范 §5.1/§3）：原因 + 地址 + 怎么装 WebView2"""
    from astercore.nativeui import message_box
    from astercore.webview2 import WV2_DOWNLOAD_URL, RUNTIME_MISSING, VERSION_TOO_OLD
    lines = [
        "栖星 AsterCore 本次以【浏览器模式】启动。",
        "",
        f"原因：{status.reason}",
        "",
        f"面板地址（可复制到浏览器打开）：{url}",
        "程序会常驻系统托盘，关闭浏览器标签不会退出。",
    ]
    if status.code in (RUNTIME_MISSING, VERSION_TOO_OLD):
        lines += ["", "想要内嵌窗口的体验，可以安装/更新 WebView2 运行时（免费，微软官方）：",
                  WV2_DOWNLOAD_URL]
        kind = "warning"
    else:
        lines += ["", "你的系统版本不满足 WebView2 的要求，浏览器模式是当前最佳选择。"]
        kind = "info"
    message_box("栖星 AsterCore · 已切换为浏览器模式", "\n".join(lines), kind=kind)


def _start_tray_for_http(hub, url: str, state) -> None:
    """HTTP 模式下的托盘常驻（规范 §5.3）。

    没有托盘能力就什么都不做 —— 不能因为托盘不可用就把程序变成"关不掉"。
    """
    from astercore.shell import ShellApp
    if not ShellApp.tray_available():
        print("[launch] 未安装托盘组件（pystray/pillow），程序需用 Ctrl+C 退出")
        return
    import threading
    title = f"栖星 AsterCore v{__version__}"

    def _run() -> None:
        try:
            import pystray

            def _on_open(icon, item):
                _open_browser(url)

            def _on_quit(icon, item):
                icon.stop()
                cb = _take_quit()
                if cb:
                    cb()

            app = ShellApp("", hub=hub, tray=True, title=title)
            icon = pystray.Icon("astercore", app._make_icon_image(), title,
                                pystray.Menu(
                                    pystray.MenuItem("打开面板", _on_open, default=True),
                                    pystray.MenuItem("退出", _on_quit)))
            icon.run()
        except Exception as e:                       # noqa: BLE001
            print(f"[launch] 托盘不可用: {e}")

    threading.Thread(target=_run, daemon=True, name="astercore-tray").start()
    print("[launch] 已常驻托盘：右键图标可打开面板或退出")


def _random_free_port() -> int:
    """取一个空闲端口（规范 §5.2 本地回退用随机端口，不用用户配置的那个）"""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


async def _run_desktop(args, state, panel, hub, mgr, http_url: str) -> int:
    """桌面模式：窗口 + js_api RPC（阻塞直到退出）。

    手册 §2.1「开窗时机」：先把窗口开出来（WebView2 初始化本身 300-800ms 无法优化），
    机器人启动这类重活丢到 loaded 回调的后台线程里做。
    """
    html = Path(__file__).resolve().parent / "web" / "static" / "index.html"
    holder: dict[str, object] = {}

    def _quit() -> None:
        app = holder.get("shell")
        if app is not None:
            app.quit()

    def _hide() -> None:
        app = holder.get("shell")
        try:
            if app is not None and app._window is not None:
                app._window.hide()
        except Exception:                       # noqa: BLE001
            pass

    hub.register("app.quit", lambda p: (_quit(), {"ok": True})[1])
    hub.register("window.hide", lambda p: (_hide(), {"ok": True})[1])
    hub.register("app.info", lambda p: {
        "version": __version__,
        "desktop": True,
        "web_url": http_url,
        "transport": "jsapi",
    })

    shell = ShellApp(url=http_url, hub=hub, html_path=html,
                     on_quit=None, title=f"栖星 AsterCore v{__version__}",
                     debug=bool(getattr(args, "debug", False)))
    holder["shell"] = shell
    print("[launch] 桌面窗口启动中（关窗口 = 最小化到托盘，退出请用托盘菜单）")
    try:
        # 托盘 + 窗口都会阻塞，放在线程里跑窗口，主线程等它结束
        shell.run(with_tray=not getattr(args, "no_tray", False))
    except KeyboardInterrupt:
        shell.quit()
    finally:
        await mgr.stop_all()
    return 0


def _open_browser(url: str) -> None:
    import webbrowser
    try:
        webbrowser.open(url)
    except Exception:
        pass


def _build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="astercore.launch", description="栖星 AsterCore 启动器")
    ap.add_argument("--data-dir", default=None,
                    help="数据目录（默认：打包运行时为 exe 同级 data/）")
    ap.add_argument("--accounts-dir", default=None,
                    help="账号配置目录（默认：exe 同级 accounts/）")
    ap.add_argument("--plugins-dir", default=None,
                    help="插件目录（默认：exe 同级 plugins/）")
    ap.add_argument("--port", type=int, default=None,
                    help="Web 面板端口（给出即强制开启 Web 服务）")
    ap.add_argument("--host", default=None, help="Web 面板监听地址（默认 127.0.0.1）")
    ap.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    ap.add_argument("--yes", action="store_true", help="跳过首启向导，直接使用推荐运行方式")
    ap.add_argument("--shell", action="store_true",
                    help="强制桌面壳模式（pywebview 内嵌窗口；有 [desktop] 依赖时本来就是默认）")
    ap.add_argument("--no-shell", action="store_true",
                    help="不用桌面窗口，走浏览器模式（首次启动请在防火墙弹窗点「允许」）")
    ap.add_argument("--web", action="store_true", help="强制开启 Web 服务（手机/别的电脑可访问）")
    ap.add_argument("--no-web", action="store_true", help="强制关闭 Web 服务")
    ap.add_argument("--debug", action="store_true", help="桌面窗口开调试（F12 可用）")
    ap.add_argument("--no-tray", action="store_true", help="不创建托盘图标")
    ap.add_argument("--allow-multi", action="store_true",
                    help="允许同时运行多个实例（默认单实例：重复双击会把已有窗口唤到前台）")
    return ap


def main(argv: list[str] | None = None) -> None:
    args = _build_parser().parse_args(argv)

    # 单实例锁（手册 §8.2）：双击两次会开两个实例 → 两个进程抢同一个 QQ 账号，非常难查。
    # --allow-multi 可显式放开（调试多账号时有用）。
    lock = None
    if not getattr(args, "allow_multi", False):
        from astercore.singleinstance import acquire, try_focus_window
        lock = acquire()
        if lock is None:
            title = f"栖星 AsterCore v{__version__}"
            if try_focus_window(title):
                print("[launch] 程序已在运行，已把它的窗口唤到前台")
            else:
                print("[launch] 程序已在运行（同一时间只允许一个实例）")
                print("[launch] 确实需要多开请加 --allow-multi")
            sys.exit(0)
    try:
        sys.exit(asyncio.run(amain(args)))
    except KeyboardInterrupt:
        sys.exit(0)
    finally:
        if lock is not None:
            lock.release()


if __name__ == "__main__":
    main()
