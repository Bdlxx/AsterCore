# 栖星 AsterCore · CLI 启动器（v0.1 骨架 / dry-run 演示）
# 用法：
#   python -m astercore --account 10001 --dry-run
#      （dry-run：不连后端，加载插件 + 模拟一条消息事件验证链路）
#   python -m astercore --account 10001 --ws ws://127.0.0.1:3001 --token xxx
#      （连 OneBot v11 后端真跑）

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from astercore.backends.onebot import OneBotV11Backend  # noqa: F401  确保注册
from astercore.backends.null import NullBackend  # noqa: F401  确保注册（dry-run）
from astercore.core.backend import BackendConfig, get_registry
from astercore.core.models import Event, Segment, seg_text
from astercore.core.runtime import AccountRuntime

LOG_FMT = "%(asctime)s [%(levelname)s] %(name)s | %(message)s"


def _build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="astercore", description="栖星 AsterCore")
    ap.add_argument("--account", default="10001", help="账号 ID（多账号目录名）")
    ap.add_argument("--data-dir", default="data", help="数据根目录（默认 ./data）")
    ap.add_argument("--backend", default="onebot", help="后端名（默认 onebot）")
    ap.add_argument("--ws", default=None, help="WS 地址（默认 127.0.0.1:3001）")
    ap.add_argument("--http", default=None, help="HTTP 地址（默认 127.0.0.1:3000）")
    ap.add_argument("--token", default="", help="access_token")
    ap.add_argument("--dry-run", action="store_true", help="不连后端：加载插件 + 模拟消息")
    ap.add_argument("--plugin-dir", default=None, help="插件目录（默认 data/<账号>/plugins）")
    ap.add_argument("-v", "--verbose", action="store_true", help="debug 日志")
    return ap


def _print_title() -> None:
    print("=" * 52)
    print("  栖星 AsterCore · QQ 机器人多后端框架（v0.1 骨架）")
    print("=" * 52)


async def _run_dry(rt: AccountRuntime, account: str) -> None:
    """走完整启动（null 后端 + 插件激活注入 ctx）+ 模拟消息，验证到「插件→发送」链路。"""
    await rt.start()
    # 模拟事件：群消息 "你好" → demo 插件应通过 ctx.send_group 调 null 后端发送
    fake = Event(
        type="message",
        account_id=account,
        platform="dry-run",
        time=0,
        user_id=12345,
        group_id=67890,
        self_id=account,
        message_type="group",
        raw="你好",
        segments=[seg_text("你好")],
    )
    print(f"\n[模拟] 投递群消息 user=12345 group=67890 → {fake.raw!r}")
    result = await rt.bus.dispatch(fake)
    print(f"[模拟] 事件链结果: {result}（handled=有插件处理 / passed=无插件处理）")
    # 非关键词消息 → 放行
    fake2 = Event(
        type="message", account_id=account, platform="dry-run", time=0,
        user_id=12345, group_id=67890, self_id=account,
        message_type="group", raw="随便聊聊", segments=[seg_text("随便聊聊")],
    )
    result2 = await rt.bus.dispatch(fake2)
    print(f"[模拟] 非关键词事件链结果: {result2}")
    await rt.stop()
    print("\nDry-run 通过：插件加载/激活注入 ctx/事件分发/发送链路均正常。")
    # 演示结构化日志环（面板实时日志的前置能力）
    logs = rt.recent_logs(10)
    print(f"[info] 日志环已记录 {len(logs)} 条；最近 3 条：")
    for e in logs[-3:]:
        print(f"       {e['ts']:.1f} [{e['level']}] {e['source']} | {e['msg'][:60]}")


async def amain(args: argparse.Namespace) -> int:
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format=LOG_FMT)
    _print_title()

    data_root = Path(args.data_dir)
    acct_dir = data_root / str(args.account)
    acct_dir.mkdir(parents=True, exist_ok=True)

    plugin_dir = Path(args.plugin_dir) if args.plugin_dir else (acct_dir / "plugins")
    # dry-run 演示插件：若目录不存在，用内置示例插件目录
    if not plugin_dir.exists() or args.dry_run and args.plugin_dir is None:
        from astercore import plugins as _pkg
        plugin_dir = Path(_pkg.__file__).parent
        print(f"[info] 插件目录: {plugin_dir}（dry-run 使用内置示例）")

    registry = get_registry()
    print(f"[info] 可用后端: {registry.names()}")

    cfg = BackendConfig(
        ws_url=args.ws or "ws://127.0.0.1:3001",
        http_url=args.http or "http://127.0.0.1:3000",
        access_token=args.token,
    )
    backend = registry.create(args.backend, cfg, account_id=args.account)
    rt = AccountRuntime(
        account_id=args.account,
        data_dir=acct_dir,
        backend=backend,
        plugin_dir=plugin_dir,
    )

    if args.dry_run:
        # dry-run 强制 null 后端（不连接协议）
        rt = AccountRuntime(
            account_id=args.account,
            data_dir=acct_dir,
            backend=get_registry().create("null", cfg, account_id=args.account),
            plugin_dir=plugin_dir,
        )
        await _run_dry(rt, args.account)
        return 0

    print(f"[info] 启动账号 {args.account}（backend={args.backend} ws={cfg.ws_url}）")
    await rt.start()
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
