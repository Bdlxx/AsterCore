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

    # 通道决策（手册 §1.2/§3.1）：
    #   有 pywebview 且没被 --no-shell 关掉 → 桌面模式：窗口走 js_api，**HTTP 默认不启动**
    #   否则 → 浏览器模式：必须起 HTTP（那是唯一的界面）
    desktop = ShellApp.webview_available() and not getattr(args, "no_shell", False)
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

    http_url = ""
    if web_cfg["enabled"]:
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
        print("[launch] Web 服务未开启（桌面模式走进程内 RPC，不需要端口）")
        print("[launch] 需要手机/别的电脑访问时，在面板「安全设置」里打开 Web 服务")
    if not started:
        print("[launch] 提示：打开面板 → 账号 → 新建账号 填入你的 QQ 号与 NapCat 地址")

    if desktop:
        return await _run_desktop(args, state, panel, hub, mgr, http_url)

    if not http_url:
        # 既没有桌面壳又没开 Web：起一个兜底 HTTP，否则用户看不到任何界面
        port = _pick_port("127.0.0.1", web_cfg["port"])
        serve_in_background(panel, host="127.0.0.1", port=port)
        http_url = f"http://127.0.0.1:{port}"
        print(f"[launch] 未检测到桌面组件，回退浏览器模式: {http_url}")

    import os as _os
    if not args.no_browser and not _os.environ.get("ASTER_NO_OPEN"):
        import threading
        threading.Timer(0.8, _open_browser, args=(http_url,)).start()

    try:
        while True:
            await asyncio.sleep(3600)
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        await mgr.stop_all()
    return 0


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
