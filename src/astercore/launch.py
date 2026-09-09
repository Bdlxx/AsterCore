# 栖星 AsterCore · 启动器（编译前控制台版；Windows 桌面壳窗口化后复用同一决策）
# 流程：加载 AppState → 首次需向导时控制台交互（运行方式+风险确认）→
#       启动账号管理器 + Web 面板 → 自动打开浏览器。
# 用法：python -m astercore.launch [--data-dir data] [--accounts-dir accounts]
#       [--port 8080] [--no-browser]

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("astercore.launch")


def _console_wizard(state) -> str:
    """控制台版首启向导：返回选定 backend_mode"""
    from astercore.app import BACKEND_MODES
    print("\n===== 栖星 AsterCore · 首启向导 =====")
    for k, m in BACKEND_MODES.items():
        stars = "★" * m["risk"] + "☆" * (5 - m["risk"])
        print(f"  {k:10} {m['label']}  [{stars}]")
        print(f"            {m['desc']}")
    while True:
        mode = input("选择运行方式（napcat / lagrange / null）: ").strip().lower()
        if mode not in BACKEND_MODES:
            print("  未知选项，重试")
            continue
        meta = BACKEND_MODES[mode]
        if meta["needs_ack"]:
            ack = input("⚠️ 该模式非官方协议，存在封号风险（建议小号）。输入 yes 确认: ")
            if ack.strip().lower() != "yes":
                print("  已取消")
                continue
        ok, msg = state.set_backend(mode, ack_risk=True)
        print(("  ✓ " if ok else "  ✗ ") + msg)
        return mode


async def amain(args: argparse.Namespace) -> int:
    from astercore.app import AppState
    from astercore.core.manager import AccountManager
    from astercore.web.server import ManagerWebPanel, serve_in_background

    data_root = Path(args.data_dir)
    data_root.mkdir(parents=True, exist_ok=True)
    state = AppState(data_root)
    state.load()

    # 向导决策
    if state.needs_wizard():
        _console_wizard(state)
    else:
        print(f"[launch] 运行方式: {state.backend_mode}（跳过向导，重置请在面板操作）")

    # 账号 + 后端
    import astercore.backends.onebot  # noqa: F401
    import astercore.backends.null    # noqa: F401
    mgr = AccountManager(args.accounts_dir, data_root=data_root)
    started = []
    for a in mgr.scan():
        try:
            await mgr.start(a["account_id"])
            started.append(a["account_id"])
        except Exception as e:
            print(f"[launch] 账号 {a['account_id']} 启动失败: {e}")
    print(f"[launch] 已启动账号: {started or '（无——请在面板新建）'}")

    # 面板
    loop = asyncio.get_running_loop()
    panel = ManagerWebPanel(mgr, loop_provider=lambda: loop, app_state=state)
    serve_in_background(panel, host="127.0.0.1", port=args.port)
    url = f"http://127.0.0.1:{args.port}"
    print(f"[launch] Web 面板: {url}")

    if args.shell:
        from astercore.shell import ShellApp
        shell = ShellApp(url, on_quit=lambda: None)
        shell.open_panel()  # 阻塞（内嵌或浏览器）
        await mgr.stop_all()
        return 0
    if not args.no_browser:
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


def main() -> None:
    ap = argparse.ArgumentParser(prog="astercore.launch", description="栖星 AsterCore 启动器")
    ap.add_argument("--data-dir", default="data")
    ap.add_argument("--accounts-dir", default="accounts")
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--shell", action="store_true",
                    help="桌面壳模式：pywebview 内嵌窗口(可装[desktop]后体验)；否则系统浏览器")
    args = ap.parse_args()
    try:
        sys.exit(asyncio.run(amain(args)))
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    main()
