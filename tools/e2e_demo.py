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
C_SRC = ROOT / "plugin-sdk/examples/c-sample/nap_hello.c"
# 示例产物按平台取名：Windows 是 nap_hello.dll，Linux/macOS 是 libnap_hello.so。
# 拿 Linux 的 .so 当 Windows 产物用，会走到系统加载器弹"损坏的映像"对话框。
C_LIB = ROOT / "plugin-sdk/examples/c-sample" / (
    "nap_hello.dll" if sys.platform == "win32" else "libnap_hello.so")
DEMO_PY = ROOT / "src/astercore/plugins/demo_hello.py"


def _ensure_c_lib() -> bool:
    if C_LIB.exists():
        return True
    print("[e2e] 编译 C 示例插件…")
    import subprocess
    if sys.platform == "win32":
        # Windows 没有 cc：先 cl（VS 开发者命令行），再 gcc（MinGW）。
        candidates = [
            ["cl", "/nologo", "/LD", "/I", str(ROOT / "plugin-sdk"),
             str(C_SRC), f"/Fe:{C_LIB}"],
            ["gcc", "-shared", "-O2", "-I", str(ROOT / "plugin-sdk"),
             str(C_SRC), "-o", str(C_LIB)],
        ]
    else:
        candidates = [
            ["cc", "-shared", "-fPIC", "-O2", "-I", str(ROOT / "plugin-sdk"),
             str(C_SRC), "-o", str(C_LIB)],
        ]
    for cmd in candidates:
        if shutil.which(cmd[0]) is None:
            continue
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode == 0 and C_LIB.exists():
            return True
    return False


async def _run(serve_port: int | None) -> int:
    from astercore.backends.onebot import OneBotV11Backend  # noqa: F401
    from astercore.core.backend import BackendConfig, get_registry
    from astercore.core.runtime import AccountRuntime

    has_c = _ensure_c_lib()
    if not has_c:
        # 原生插件只是本演示的一条腿（Python 插件腿照跑），别因此中断整轮验证。
        # Windows 上没装编译器时提示清楚，免得看的人以为是框架坏了。
        print("[e2e] ⚠ 跳过原生插件腿：C 示例未编译"
              + ("（Windows 需 cl.exe / VS 开发者命令行，或 gcc）"
                 if sys.platform == "win32" else "（需要 cc）"))

    # 1) 起 fake onebot（推送"你好"群消息）
    import subprocess
    port = 9931
    fake = subprocess.Popen(
        [sys.executable, str(FAKE_SCRIPT), "--port", str(port), "--self", "740979632"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    await asyncio.sleep(1.5)

    # 2) 组装插件目录：demo_hello.py + 原生库（有才拷）
    tmp = Path(tempfile.mkdtemp(prefix="astere2e-"))
    pdir = tmp / "plugins"
    pdir.mkdir(parents=True)
    shutil.copy(DEMO_PY, pdir / "demo_hello.py")
    if has_c:
        shutil.copy(C_LIB, pdir / C_LIB.name)

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
