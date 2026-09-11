# 栖星 AsterCore · 启动器（桌面/控制台入口）
# 流程：解析路径 → 首启引导（建目录+播种示例插件）→ 运行方式向导（可跳过）
#       → 启动账号管理器 + Web 面板 → 自动打开浏览器。
# 用法：python -m astercore.launch [--port 8080] [--no-browser] [--yes] [--shell]
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

    # 面板
    loop = asyncio.get_running_loop()
    panel = ManagerWebPanel(mgr, loop_provider=lambda: loop, app_state=state)
    port = _pick_port(args.host, args.port) if args.port else args.port
    if port != args.port:
        print(f"[launch] 端口 {args.port} 被占用，改用 {port}")
    serve_in_background(panel, host=args.host, port=port)
    url = f"http://{args.host}:{port}"
    print(f"[launch] Web 面板已就绪: {url}")
    if not started:
        print("[launch] 提示：打开面板 → 账号 → 新建账号 填入你的 QQ 号与 NapCat 地址")

    if args.shell:
        from astercore.shell import ShellApp
        shell = ShellApp(url, on_quit=lambda: None)
        shell.open_panel()  # 阻塞（内嵌或浏览器）
        await mgr.stop_all()
        return 0

    import os as _os
    if not args.no_browser and not _os.environ.get("ASTER_NO_OPEN"):
        import threading
        threading.Timer(0.8, _open_browser, args=(url,)).start()

    try:
        while True:
            await asyncio.sleep(3600)
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
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
    ap.add_argument("--port", type=int, default=8080, help="Web 面板端口")
    ap.add_argument("--host", default="127.0.0.1", help="Web 面板监听地址")
    ap.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    ap.add_argument("--yes", action="store_true", help="跳过首启向导，直接使用推荐运行方式")
    ap.add_argument("--shell", action="store_true",
                    help="桌面壳模式：pywebview 内嵌窗口（需安装 [desktop] 依赖）")
    return ap


def main(argv: list[str] | None = None) -> None:
    args = _build_parser().parse_args(argv)
    try:
        sys.exit(asyncio.run(amain(args)))
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    main()
