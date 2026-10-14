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
import re
import socket
import sys
from pathlib import Path

def _reg_rpc(hub, path, fn):
    """注册桌面 RPC：**拆信封** + 返回值统一包成 {ok,data}/{ok,error}，与 HTTP 形状一致。

    两个踩过的坑都收在这里，避免再犯：
    1) 前端 Transport 调 `rpc(url, {method, body})` —— 所以进到处理函数的**不是**业务参数，
       而是 `{"method": "...", "body": {...}}`。不拆包的话 `p.get("account_id")` 永远是 None
       （用户报的"save-account 失败：ValueError: 缺少 account_id"就是这个）。
    2) 函数内 def 的先后顺序：统一用模块级函数，避免 UnboundLocalError 被 except 吞掉。
    """
    def _h(p=None):
        payload = p
        if isinstance(p, dict) and ("method" in p or "body" in p):
            payload = p.get("body")
            if payload is None:                  # GET：没有 body，就把非信封字段当参数
                payload = {k: v for k, v in p.items() if k not in ("method", "body")}
        try:
            return {"ok": True, "data": fn(payload)}
        except Exception as e:                  # noqa: BLE001
            return {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}
    hub.register(path, _h)


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

    # 向导决策：窗口模式（无控制台）不问 cmd —— 运行方式挪到界面（面板 → 连接引导）
    if args.yes:
        if state.needs_wizard():
            state.set_backend(DEFAULT_MODE, ack_risk=True)
        print(f"[launch] 运行方式: {state.backend_mode}（--yes 跳过向导）")
    elif state.needs_wizard():
        if _interactive_stdin():
            _console_wizard(state)
        else:
            state.set_backend(DEFAULT_MODE, ack_risk=True)
            print(f"[launch] 无交互终端（窗口模式）→ 先取推荐运行方式 {DEFAULT_MODE}；"
                  f"要改请在面板『连接引导』页里选（不再在 cmd 里问）")
    else:
        print(f"[launch] 运行方式: {state.backend_mode}（跳过向导，重置请在面板操作）")

    # 账号 + 后端
    import astercore.backends.null      # noqa: F401
    import astercore.backends.onebot    # noqa: F401
    import astercore.backends.lagrange  # noqa: F401  内置协议直登（自托管子进程）
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
    from astercore.botstate import BotController
    # 托盘 / 主界面 / 接口共用同一个状态机（规格 §四：状态实时同步）
    controller = BotController(mgr, loop_provider=lambda: loop,
                               state_get=lambda: state.current_account,
                               state_set=state.set_current_account)
    # 更新器两阶段：①启动最早期先看上次更新是否没起来 → 回滚；②面板建好后确认本次启动
    try:
        from astercore import updater as _upd
        from astercore.paths import app_base_dir as _abd
        if _upd.try_rollback(_abd()):
            print("[launch] 上次更新未能正常启动，已自动回滚到旧版本")
        try:                                    # 老的图片缓存目录一次性迁移
            from astercore.compat.paths import migrate_legacy_cache
            _mig = migrate_legacy_cache(_abd())
            if _mig.get("migrated"):
                print(f"[launch] 已迁移 {_mig['migrated']} 个历史图片到 NapCat 缓存目录")
        except Exception:                       # noqa: BLE001
            log.debug("缓存目录迁移检查失败", exc_info=True)
    except Exception:                       # noqa: BLE001 更新器不能拖垮启动
        log.exception("更新回滚检查失败")

    panel = ManagerWebPanel(mgr, loop_provider=lambda: loop, app_state=state,
                            bot_controller=controller)

    try:
        from astercore import updater as _upd2
        from astercore.paths import app_base_dir as _abd2
        if _upd2.confirm_boot(_abd2()):
            print("[launch] 本次启动已确认（清理旧版本目录）")
    except Exception:                       # noqa: BLE001
        log.exception("更新确认失败")
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
        return await _run_desktop(args, state, panel, hub, mgr, http_url, controller)

    # 降级告知（规范 §5.1）：不能静默打开浏览器。必须说清"为什么 + 地址 + 怎么装"
    #
    # 只在**本地回退**（fallback）时才弹：用户如果本来就主动开了 Web 服务，
    # 浏览器模式就是他要的结果，再去解释一遍是噪音；而且弹窗是阻塞的，
    # 无头环境（CI/e2e）里没人点会一直卡住。
    if fallback and wv2 is not None and not wv2.ok:
        _explain_downgrade(wv2, http_url)

    _start_tray_for_http(hub, http_url, state, controller)

    import os as _os
    if not args.no_browser and not _os.environ.get("ASTER_NO_OPEN"):
        import threading
        threading.Timer(0.8, _open_browser, args=(_entry_url(http_url, state),)).start()
    print(f"[launch] 连接引导页: {http_url}/guide（装组件/写配置/扫码都在那一页走完）")

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


def _start_tray_for_http(hub, url: str, state, controller=None) -> None:
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

            from astercore.shell import build_tray_menu
            import webbrowser
            app = ShellApp("", hub=hub, tray=True, title=title)
            # 规格 §二 的同一份菜单：HTTP 模式下"显示面板"= 打开浏览器
            menu = build_tray_menu(controller,
                                   toggle_cb=lambda: webbrowser.open(url),
                                   quit_cb=app.quit)
            icon = pystray.Icon("astercore", app._make_icon_image(), title, menu)
            if controller is not None:
                controller.on_change = lambda: icon.update_menu()
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


async def _run_desktop(args, state, panel, hub, mgr, http_url: str,
                     controller=None) -> int:
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

    # 桌面（file://）模式下，面板的 Transport 会把 fetch('/api/...') 转成 hub 方法调用；
    # 桌面专属接口因此必须在这里注册一份（HTTP 那份只用于浏览器/端口模式）
    try:
        from astercore.web.setup import DEFAULT_PORT as _SP_DEFAULT

        def _snap(p=None):
            return controller.snapshot() if controller is not None else {"accounts": []}

        def _accounts(p=None):
            rows = []
            for a in _snap().get("accounts", []):
                name = str(a.get("display_name") or a.get("account_id") or "")
                rows.append({"account_id": str(a.get("account_id")), "display_name": name,
                             "short": name[:1] or "?", "running": bool(a.get("running")),
                             "current": bool(a.get("current"))})
            return {"accounts": rows}

        def _act(fn):
            def _h(p=None):
                ok, msg = fn()
                return {"ok": bool(ok), "msg": str(msg), "state": _snap()}
            return _h

        _reg_rpc(hub, "/api/app/state", lambda p=None: _snap())
        _reg_rpc(hub, "/api/app/accounts", lambda p=None: _accounts())
        if controller is not None:
            _reg_rpc(hub, "/api/app/start", _act(controller.start))
            _reg_rpc(hub, "/api/app/stop", _act(controller.stop))
            _reg_rpc(hub, "/api/app/restart", _act(controller.restart))

        def _current(p=None):
            aid = str((p or {}).get("aid") or "")
            if controller is not None and aid:
                controller.set_current_account(aid)
            return _snap()
        _reg_rpc(hub, "/api/app/account-current", _current)

        def _setup_state(p=None):
            port = _SP_DEFAULT
            try:
                port = int((state.web_config() or {}).get("port") or _SP_DEFAULT)
            except (TypeError, ValueError):
                pass
            auth_mode = getattr(getattr(panel, "auth", None), "mode", "none")
            return {"inited": (not state.needs_wizard()) and bool(state.data.get("setup_mode")),
                    "mode": state.data.get("setup_mode") or "",
                    "port": port,
                    "page_mode": "window" if state.transport_mode() in ("webview", "auto") else "port",
                    "auth_mode": auth_mode}

        def _setup_page(p=None):
            p = p or {}
            mode = str(p.get("page_mode") or "window")
            ok, msg = state.set_transport("webview" if mode == "window" else "http")
            if not ok:
                return {"ok": False, "error": msg}
            if mode == "port":
                raw = p.get("port")
                port = _SP_DEFAULT if raw in (None, "") else raw
                try:
                    port = int(port)
                except (TypeError, ValueError):
                    return {"ok": False, "error": "端口必须是数字"}
                if not (1 <= port <= 65535):
                    return {"ok": False, "error": "端口范围 1-65535"}
                am = getattr(getattr(panel, "auth", None), "mode", "none")
                ok2, msg2, cfg = state.set_web_config(
                    {"enabled": True, "host": "127.0.0.1", "port": port}, auth_mode=am)
                if not ok2:
                    return {"ok": False, "error": msg2}
                return {"ok": True, "page_mode": mode, "port": cfg["port"], "restart_needed": True}
            return {"ok": True, "page_mode": mode, "restart_needed": True}

        _reg_rpc(hub, "/api/setup/state", _setup_state)
        _reg_rpc(hub, "/api/setup/page", _setup_page)

        def _setup_window(p=None):
            """点 ✕ 的行为（GET=读、带 close_action=写）；返回值与 HTTP 那份同形"""
            p = p or {}
            if "close_action" in p:
                ok, msg = state.set_close_action(p.get("close_action"))
                if not ok:
                    raise ValueError(msg)        # _reg_rpc 会包成 {ok:false,error}
            return state.window_config()

        _reg_rpc(hub, "/api/setup/window", _setup_window)
    except Exception:                       # noqa: BLE001 注册失败不能拖垮启动
        logging.getLogger("astercore.launch").exception("桌面 RPC 接口注册失败")

    # 面板内「连接引导」页用到的端点：file:// 模式下 fetch 会被 Transport 转成 hub 方法
    try:
        from astercore import components as _C
        from astercore.paths import app_base_dir as _base
        from astercore.web import guide as _guide

        def _g_status(p=None):
            """与 HTTP 的 /api/guide/status 同形：顶栏靠 jobs 显示"下载中 x% / 解压中"""
            api = getattr(panel, "guide_api", None) or {}
            fn = api.get("status")
            if fn is not None:
                return fn()
            base = _base()
            return {"supported": True, "base_dir": str(base),
                    "components": {k: _C.host_status(c, base) for k, c in _C.COMPONENTS.items()},
                    "jobs": _guide.jobs_snapshot()}

        def _g_install(p=None):
            """走 guide 那份任务化安装（有进度、能区分下载/解压），不再自己裸起线程"""
            api = getattr(panel, "guide_api", None) or {}
            fn = api.get("install")
            if fn is not None:
                return fn(p)
            p = p or {}
            return _guide.start_install(str(p.get("key") or ""), _base(),
                                        url=p.get("url"), zip_path=p.get("zip"))

        def _g_config(p=None):
            p = p or {}
            comp = _C.get_component(str(p.get("key") or ""))
            base = _base()
            ws = int(p.get("ws_port") or _C.free_port(3001))
            hp = int(p.get("http_port") or _C.free_port(3000, avoid=[ws]))
            token = str(p.get("token") or _C.random_token())
            qq = str(p.get("qq") or "default")
            if comp.key == "napcat":
                path = _C.write_napcat_config(base, qq, ws, hp, token)
            else:
                path = _C.write_lagrange_config(base, ws, token, http_port=hp,
                                                uin=int(qq) if qq.isdigit() else None)
            return {"path": str(path), "ws_url": "ws://127.0.0.1:%d" % ws,
                    "http_url": "http://127.0.0.1:%d" % hp, "access_token": token, "qq": qq}

        def _g_host(p=None):
            comp = _C.get_component(str((p or {}).get("key") or "lagrange"))
            base = _base()
            ok, msg = _C.host_start(comp, base)
            return {"ok": ok, "msg": msg, "status": _C.host_status(comp, base),
                    "qr": _C.qr_code(base, comp.key)}

        def _g_stop(p=None):
            comp = _C.get_component(str((p or {}).get("key") or "lagrange"))
            base = _base()
            ok, msg = _C.host_stop(comp, base)
            return {"ok": ok, "msg": msg}

        def _g_qr(p=None):
            key = str((p or {}).get("key") or "lagrange")
            return _C.qr_code(_base(), key)

        def _g_log(p=None):
            key = str((p or {}).get("key") or "lagrange")
            return {"lines": _C.tail_log(_base(), key, limit=int((p or {}).get("limit") or 40))}

        def _g_discover(p=None):
            return {"found": _C.discover_local(_base())}

        def _guide_create_account(p=None):
            """一键建账号：复用 HTTP 路由那份实现（guide_api），避免写第二份逻辑"""
            api = getattr(panel, "guide_api", None) or {}
            fn = api.get("create_account")
            if fn is None:
                return {"ok": False, "error": "当前环境不支持一键建账号"}
            ok, payload = fn(p or {})
            if ok:
                return payload
            raise RuntimeError(str(payload))        # _reg 会包成 {ok:false,error}

        _reg_rpc(hub, "/api/guide/status", _g_status)
        _reg_rpc(hub, "/api/guide/install", _g_install)
        _reg_rpc(hub, "/api/guide/config", _g_config)
        _reg_rpc(hub, "/api/guide/host", _g_host)
        _reg_rpc(hub, "/api/guide/stop", _g_stop)
        _reg_rpc(hub, "/api/guide/qr", _g_qr)
        _reg_rpc(hub, "/api/guide/log", _g_log)
        _reg_rpc(hub, "/api/guide/discover", _g_discover)
        _reg_rpc(hub, "/api/guide/account", _guide_create_account)
    except Exception:                       # noqa: BLE001
        logging.getLogger("astercore.launch").exception("引导 RPC 接口注册失败")

    try:
        from astercore.web.appapi import _session as _app_session

        _reg_rpc(hub, "/api/app/update/status", lambda p=None: _app_session().status())
        _reg_rpc(hub, "/api/app/update/check", lambda p=None: _app_session().check())
        _reg_rpc(hub, "/api/app/update/start", lambda p=None: _app_session().start())

        def _update_apply(p=None):
            from astercore import updater as _u2
            stt = _app_session().status()
            scr = stt.get("script") or ""
            if stt.get("state") != "done" or not scr:
                return {"applied": False, "error": "还没有备好更新（先检查并下载）"}
            if not _u2.apply_now(scr):
                return {"applied": False, "error": "交接脚本不存在或无法执行"}
            _quit()                            # 关掉本进程，脚本替换目录后重新拉起
            return {"applied": True, "hint": "程序将退出并应用更新"}

        _reg_rpc(hub, "/api/app/update/apply", _update_apply)
    except Exception:                       # noqa: BLE001
        logging.getLogger("astercore.launch").exception("更新 RPC 注册失败")

    def _reg(path, fn):
        """注册桌面 RPC：返回值统一包成 {ok,data}/{ok,error}，与 HTTP 那份形状一致，
        这样共用面板的 fetch 代码在两种模式下都能读 r.ok / r.data。"""
        def _h(p=None):
            try:
                return {"ok": True, "data": fn(p)}
            except Exception as e:                  # noqa: BLE001
                return {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}
        hub.register(path, _h)

    def _open_url(p=None):
        """引导页第①步"去官网下载"：用系统浏览器打开链接（只允许 http/https）"""
        import re as _re
        import webbrowser as _wb
        url = str((p or {}).get("url") or "").strip()
        if not _re.match(r"^https?://", url):
            raise ValueError("只允许 http/https 链接")
        return {"opened": bool(_wb.open(url, new=2)), "url": url}

    _reg_rpc(hub, "/api/app/open-url", _open_url)
    def _window_ctl(p=None):
        """自绘标题栏三按钮：左上=最小化 / 中=收起托盘 / 右=退出程序"""
        action = str((p or {}).get("action") or "").strip()
        sh = holder.get("shell")
        if action == "minimize":
            return {"done": bool(sh and sh.minimize())}
        if action == "maximize":
            return {"done": bool(sh and sh.toggle_maximize())}
        if action == "hide":
            if sh is not None:
                sh._hide_window()
            return {"done": True}
        if action == "quit":
            if sh is not None:
                sh.quit()
            return {"done": True}
        raise ValueError(f"未知窗口操作: {action or '(空)'}")

    _reg_rpc(hub, "/api/app/window", _window_ctl)
    # 账号操作（**固定路径**，RPC 只认固定名）：新建/保存/删除/启停
    try:
        from astercore.core.manager import AccountConfig as _ACct2

        def _acct_save(p=None):
            d = p or {}
            aid = str(d.get("account_id") or "").strip()
            if not aid:
                raise ValueError("缺少 account_id")
            b = d.get("backend") or {}
            old = mgr.get_config(aid)
            mgr.save_config(_ACct2(
                account_id=aid,
                display_name=str(d.get("display_name") or (old.display_name if old else aid)),
                backend_name=str(b.get("name") or (old.backend_name if old else "onebot")),
                ws_url=str(b.get("ws_url") or (old.ws_url if old else "ws://127.0.0.1:3001")),
                http_url=str(b.get("http_url") or (old.http_url if old else "http://127.0.0.1:3000")),
                access_token=str(b.get("access_token") if "access_token" in b
                                 else (old.access_token if old else "")),
            ))
            return {"account_id": aid, "saved": True, "created": old is None}

        def _acct_del(p=None):
            aid = str((p or {}).get("account_id") or "").strip()
            if not aid:
                raise ValueError("缺少 account_id")
            fn = getattr(mgr, "delete", None) or getattr(mgr, "remove", None)
            if fn is None:
                raise RuntimeError("当前管理器不支持删除账号")
            fn(aid)
            return {"account_id": aid, "deleted": True}

        def _acct_act(action):
            def _h(p=None):
                aid = str((p or {}).get("account_id") or "").strip()
                if not aid:
                    raise ValueError("缺少 account_id")
                import asyncio as _aio
                loop = asyncio.get_event_loop()
                coro = getattr(mgr, action)(aid)
                if loop.is_running():
                    fut = _aio.run_coroutine_threadsafe(coro, loop)
                    fut.result(timeout=120)
                else:
                    loop.run_until_complete(coro)
                return {"account_id": aid, action: True}
            return _h

        _reg_rpc(hub, "/api/app/account-save", _acct_save)
        _reg_rpc(hub, "/api/app/account-delete", _acct_del)
        _reg_rpc(hub, "/api/app/account-start", _acct_act("start"))
        _reg_rpc(hub, "/api/app/account-stop", _acct_act("stop"))
    except Exception:                       # noqa: BLE001
        logging.getLogger("astercore.launch").exception("账号操作 RPC 注册失败")

    # 账号 CRUD：桌面 file:// 模式下 fetch('/api/accounts') 会被转成 hub 方法，
    # 不注册就走不通（用户报的"失败，账号未配置"就是这个）——固定路径逐个注册，
    # 动态路径（/api/accounts/<id>/...）用前缀兜底分发。
    try:
        from astercore.core.manager import AccountConfig as _ACct

        def _acct_rows():
            rows = []
            for r in (mgr.scan() if mgr is not None else []):
                rows.append({"account_id": str(r.get("account_id")),
                             "display_name": r.get("display_name") or str(r.get("account_id")),
                             "backend_name": r.get("backend_name") or "",
                             "running": bool(r.get("running"))})
            return rows

        def _acct_create(p=None):
            d = p or {}
            aid = str(d.get("account_id") or "").strip()
            if not aid:
                raise ValueError("缺少 account_id")
            b = d.get("backend") or {}
            mgr.save_config(_ACct(
                account_id=aid,
                display_name=str(d.get("display_name") or aid),
                backend_name=str(b.get("name") or "onebot"),
                ws_url=str(b.get("ws_url") or "ws://127.0.0.1:3001"),
                http_url=str(b.get("http_url") or "http://127.0.0.1:3000"),
                access_token=str(b.get("access_token") or ""),
            ))
            return {"account_id": aid}

        _reg_rpc(hub, "/api/accounts", lambda p=None: _acct_rows() if p is None or p == {} else _acct_create(p))

        def _acct_dyn(p=None):
            """动态账号路径兜底：/api/accounts/<id>/{update,delete,start,stop}"""
            d = p or {}
            path = str(d.get("__path") or d.get("path") or "")
            body = d.get("body") or {}
            m = re.match(r"^/api/accounts/([^/]+)/(update|delete|start|stop)$", path)
            if not m:
                raise ValueError(f"不支持的账号路径: {path}")
            aid, act = m.group(1), m.group(2)
            cfg = mgr.get_config(aid)
            if cfg is None:
                raise KeyError(f"账号不存在: {aid}")
            if act == "update":
                b = body.get("backend") or {}
                cfg.display_name = str(body.get("display_name") or cfg.display_name)
                if b.get("ws_url"):
                    cfg.ws_url = str(b["ws_url"])
                if b.get("http_url"):
                    cfg.http_url = str(b["http_url"])
                if "access_token" in b:
                    cfg.access_token = str(b.get("access_token") or "")
                mgr.save_config(cfg)
                return {"account_id": aid, "updated": True}
            if act == "delete":
                mgr.delete(aid) if hasattr(mgr, "delete") else None
                return {"account_id": aid, "deleted": True}
            if act == "start":
                return {"account_id": aid, "started": True}     # 真正起停由面板按钮走 /api/app/*
            return {"account_id": aid, "stopped": True}

        _reg_rpc(hub, "/api/app/account-dyn", _acct_dyn)
    except Exception:                       # noqa: BLE001
        logging.getLogger("astercore.launch").exception("账号 CRUD RPC 注册失败")


    hub.register("app.quit", lambda p: (_quit(), {"ok": True})[1])
    hub.register("window.hide", lambda p: (_hide(), {"ok": True})[1])
    hub.register("app.info", lambda p: {
        "version": __version__,
        "desktop": True,
        "web_url": http_url,
        "transport": "jsapi",
    })

    hub.register("app.quit", lambda _p=None: (holder.get("shell").quit() if holder.get("shell") else None) or {"ok": True})
    shell = ShellApp(controller=controller, url=http_url, hub=hub, html_path=html,
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


def _interactive_stdin() -> bool:
    """有没有能问问题的终端：windowed 模式没有控制台，input() 会直接 EOF/崩"""
    import sys
    try:
        return sys.stdin is not None and sys.stdin.isatty()
    except Exception:                       # noqa: BLE001
        return False


def _entry_url(http_url: str, state) -> str:
    """首启入口：当前运行方式对应的组件还没装时，直接把用户带到连接引导页

    计划书 §3.1 的主路径是"引导下载 → 自动写配置 → 扫码 → 自动连接"，
    所以第一次打开面板应该落在引导页，而不是让人自己找。
    """
    try:
        from astercore import components as C
        from astercore.paths import app_base_dir
        # 用户明确：**引导不在首次启动拦人**，而是在"新建账号"时通过面板内的
        # 「连接引导」页走完（见 ui_pages 的 guide-page 与账号页入口）。
        # 所以这里不再做任何启动拦截，直接进主界面。
        _ = (C, app_base_dir)
    except Exception:                       # noqa: BLE001 引导入口不能拖垮启动
        pass
    return http_url


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
