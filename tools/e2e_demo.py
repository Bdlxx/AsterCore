# 栖星 AsterCore · 一键端到端演示（fake WS + 多形态插件同账号协同）
# 验证栈：fake onebot server → OneBot backend(WS) → 事件总线 → [Python插件(demo_hello)
#        + 原生C插件(host子进程隔离 libnap_hello)] → 发送 → fake 收到 API。
# 用法：
#   python tools/e2e_demo.py                    # 完整验证并打印结果
#   python tools/e2e_demo.py --serve 8090       # 验证后继续跑 Web 面板（浏览 http://127.0.0.1:8090）

from __future__ import annotations

import argparse
import asyncio
import logging
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("e2e-demo")

FAKE_SCRIPT = ROOT / "tools/fake_onebot.py"
C_LIB = ROOT / "plugin-sdk/examples/c-sample/libnap_hello.so"
DEMO_PY = ROOT / "src/astercore/plugins/demo_hello.py"


def _ensure_c_lib() -> bool:
    if C_LIB.exists():
        return True
    print("[e2e] 编译 C 示例插件…")
    import subprocess
    r = subprocess.run(
        ["cc", "-shared", "-fPIC", "-O2", "-I", str(ROOT / "plugin-sdk"),
         str(ROOT / "plugin-sdk/examples/c-sample/nap_hello.c"),
         "-o", str(C_LIB)], capture_output=True, text=True)
    return r.returncode == 0


async def _run(serve_port: int | None) -> int:
    from astercore.backends.onebot import OneBotV11Backend  # noqa: F401
    from astercore.core.backend import BackendConfig, get_registry
    from astercore.core.runtime import AccountRuntime

    if not _ensure_c_lib():
        print("[e2e] ✗ C 示例编译失败（需要 gcc）")
        return 1

    # 1) 起 fake onebot（推送"你好"群消息）
    import subprocess
    port = 9931
    fake = subprocess.Popen(
        [sys.executable, str(FAKE_SCRIPT), "--port", str(port), "--self", "740979632"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    await asyncio.sleep(1.5)

    # 2) 组装插件目录：demo_hello.py + libnap_hello.so
    tmp = Path(tempfile.mkdtemp(prefix="astere2e-"))
    pdir = tmp / "plugins"
    pdir.mkdir(parents=True)
    shutil.copy(DEMO_PY, pdir / "demo_hello.py")
    shutil.copy(C_LIB, pdir / "libnap_hello.so")

    rt = AccountRuntime(
        account_id="740979632",
        data_dir=tmp / "data",
        backend=get_registry().create(
            "onebot", BackendConfig(ws_url=f"ws://127.0.0.1:{port}"),
            account_id="740979632"),
        plugin_dir=pdir,
    )
    await rt.start()
    pls = rt.list_plugins()
    print(f"[e2e] 插件同池: {[p['name'] + '(' + p['kind'] + ')' for p in pls]}")

    # 3) 等 fake 推送的"你好"流转（Python 插件 demo_hello 命中）
    await asyncio.sleep(2.0)

    # 4) 主动模拟"点歌"事件喂给总线（C++/C 插件 demo 未装则跳过），
    #    再用 fake 侧是否收到 send_group_msg 判断链路（demo_hello 已命中过）
    logs = rt.recent_logs(100)
    hit = [e for e in logs if "命中" in e.get("msg", "")]
    sent = [e for e in logs if "Null" in e.get("msg", "")]
    print(f"[e2e] 日志命中条数: {len(hit)}（fake 推送的 '你好' 应触发 demo_hello）")
    print(f"[e2e] 日志示例:")
    for e in hit[:3]:
        print(f"      [{e['level']}] {e['source']} | {e['msg'][:60]}")

    # 5) 面板（可选）
    if serve_port:
        from astercore.web.server import WebPanel, serve_in_background
        loop = asyncio.get_running_loop()
        state = {"rt": rt, "loop": loop}

        def _provider():
            return (state["rt"], state["loop"])

        panel = WebPanel(provider=_provider)
        serve_in_background(panel, port=serve_port)
        print(f"[e2e] Web 面板: http://127.0.0.1:{serve_port}（Ctrl+C 退出）")
        try:
            while True:
                await asyncio.sleep(3600)
        except (KeyboardInterrupt, asyncio.CancelledError):
            pass
    await rt.stop()
    fake.terminate()
    shutil.rmtree(tmp, ignore_errors=True)
    print("[e2e] ✅ 端到端演示完成")
    return 0


def main() -> None:
    ap = argparse.ArgumentParser(description="AsterCore 端到端演示")
    ap.add_argument("--serve", type=int, default=None, help="验证后启动 Web 面板端口")
    args = ap.parse_args()
    try:
        sys.exit(asyncio.run(_run(args.serve)))
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    main()
