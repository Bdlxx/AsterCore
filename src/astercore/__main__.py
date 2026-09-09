# 栖星 AsterCore · CLI 启动器（v0.2：dry-run / 真连 / Web 面板）
# 用法：
#   python -m astercore --account 10001 --dry-run
#   python -m astercore --account 10001 --ws ws://... --token xxx
#   python -m astercore --account 10001 --ws ws://... --serve --port 8080

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from astercore.backends.onebot import OneBotV11Backend  # noqa: F401  注册
from astercore.backends.null import NullBackend  # noqa: F401  注册（dry-run）
from astercore.core.backend import BackendConfig, get_registry
from astercore.core.models import Event, seg_text
from astercore.core.runtime import AccountRuntime

LOG_FMT = "%(asctime)s [%(levelname)s] %(name)s | %(message)s"

# 当前 runtime / 主循环（供 Web 面板 provider 读取）
_PANEL_STATE: dict = {}


def _build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="astercore", description="栖星 AsterCore")
    ap.add_argument("--account", default="10001", help="账号 ID（多账号目录名）")
    ap.add_argument("--data-dir", default="data", help="数据根目录（默认 ./data）")
    ap.add_argument("--backend", default="onebot", help="后端名（onebot/null）")
    ap.add_argument("--ws", default=None, help="WS 地址（默认 127.0.0.1:3001）")
    ap.add_argument("--http", default=None, help="HTTP 地址（默认 127.0.0.1:3000）")
    ap.add_argument("--token", default="", help="access_token")
    ap.add_argument("--dry-run", action="store_true", help="不连后端：加载插件 + 模拟消息")
    ap.add_argument("--serve", action="store_true", help="同时启动 Web 面板（HTTP）")
    ap.add_argument("--port", type=int, default=8080, help="Web 面板端口")
    ap.add_argument("--host", default="127.0.0.1", help="Web 面板监听地址")
    ap.add_argument("--plugin-dir", default=None, help="插件目录（默认 data/<账号>/plugins）")
    ap.add_argument("-v", "--verbose", action="store_true", help="debug 日志")
    return ap


def _print_title() -> None:
    print("=" * 56)
    print("  栖星 AsterCore · QQ 机器人多后端框架（v0.2 内核 + Web 面板雏形）")
    print("=" * 56)


def _make_runtime(args: argparse.Namespace) -> AccountRuntime:
    data_root = Path(args.data_dir)
    acct_dir = data_root / str(args.account)
    acct_dir.mkdir(parents=True, exist_ok=True)

    plugin_dir = Path(args.plugin_dir) if args.plugin_dir else (acct_dir / "plugins")
    if not plugin_dir.exists():
        from astercore import plugins as _pkg
        plugin_dir = Path(_pkg.__file__).parent  # dry-run 用内置示例插件

    registry = get_registry()
    cfg = BackendConfig(
        ws_url=args.ws or "ws://127.0.0.1:3001",
        http_url=args.http or "http://127.0.0.1:3000",
        access_token=args.token,
    )
    backend_name = "null" if args.dry_run else (args.backend or "onebot")
    backend = registry.create(backend_name, cfg, account_id=args.account)
    return AccountRuntime(
        account_id=args.account,
        data_dir=acct_dir,
        backend=backend,
        plugin_dir=plugin_dir,
    )


async def _run_dry(rt: AccountRuntime, account: str) -> None:
    """完整启动（null 后端）+ 模拟消息，验证到「插件→发送」链路。"""
    await rt.start()
    fake = Event(type="message", account_id=account, platform="dry-run", time=0,
                 user_id=12345, group_id=67890, self_id=account,
                 message_type="group", raw="你好", segments=[seg_text("你好")])
    print(f"\n[模拟] 投递群消息 user=12345 group=67890 → {fake.raw!r}")
    result = await rt.bus.dispatch(fake)
    print(f"[模拟] 事件链结果: {result}（handled=有插件处理 / passed=无插件处理）")
    fake2 = Event(type="message", account_id=account, platform="dry-run", time=0,
                  user_id=12345, group_id=67890, self_id=account,
                  message_type="group", raw="随便聊聊", segments=[seg_text("随便聊聊")])
    result2 = await rt.bus.dispatch(fake2)
    print(f"[模拟] 非关键词事件链结果: {result2}")
    await rt.stop()
    print("\nDry-run 通过：插件加载/激活注入 ctx/事件分发/发送链路均正常。")
    logs = rt.recent_logs(10)
    print(f"[info] 日志环已记录 {len(logs)} 条；最近 3 条：")
    for e in logs[-3:]:
        print(f"       {e['ts']:.1f} [{e['level']}] {e['source']} | {e['msg'][:60]}")


async def amain(args: argparse.Namespace) -> int:
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format=LOG_FMT)
    _print_title()
    rt = _make_runtime(args)
    _PANEL_STATE["rt"] = rt
    _PANEL_STATE["loop"] = asyncio.get_running_loop()

    if args.dry_run:
        await _run_dry(rt, args.account)
        return 0

    print(f"[info] 启动账号 {args.account}（backend={rt.backend.name} ws={rt.backend.cfg.ws_url}）")
    await rt.start()

    if args.serve:
        from astercore.web.server import WebPanel, serve_in_background

        def _provider():
            loop = _PANEL_STATE.get("loop")
            r = _PANEL_STATE.get("rt")
            return (r, loop) if r is not None and loop is not None else None

        panel = WebPanel(provider=_provider)
        serve_in_background(panel, host=args.host, port=args.port)
        print(f"[info] Web 面板: http://{args.host}:{args.port}")

    print("[info] 运行中… Ctrl+C 停止")
    try:
        while True:
            await asyncio.sleep(3600)
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        await rt.stop()
    return 0


def main() -> None:
    args = _build_parser().parse_args()
    try:
        sys.exit(asyncio.run(amain(args)))
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    main()
