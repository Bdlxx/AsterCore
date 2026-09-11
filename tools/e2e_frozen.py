#!/usr/bin/env python3
# 栖星 AsterCore · 冻结版（打包产物）端到端验收
#
# 模拟真实用户：把打包目录当成"解压出来的文件夹"，直接运行主程序，
# 然后只用网页面板 API 完成「建号 → 启动 → 连后端 → 插件工作 → 收到消息 → 插件回复」。
#
# 用法：
#   python tools/e2e_frozen.py                  # 自动找 dist/astercore
#   python tools/e2e_frozen.py --app-dir dist/astercore --exe-name astercore.exe
#
# 退出码 0 = 全流程通过；非 0 = 有环节失败（会打印失败原因）。

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_APP = ROOT / "dist" / "astercore"


def log(msg: str) -> None:
    print(f"[e2e] {msg}", flush=True)


def http_json(url: str, method: str = "GET", body: dict | None = None,
              timeout: float = 5.0):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if data:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def wait_for(fn, timeout: float, interval: float = 0.4, desc: str = "条件"):
    """轮询直到 fn() 返回真值；返回该值，超时抛错"""
    end = time.time() + timeout
    last = None
    while time.time() < end:
        try:
            last = fn()
            if last:
                return last
        except Exception as e:  # 服务还没起来等
            last = e
        time.sleep(interval)
    raise TimeoutError(f"等待超时（{timeout}s）：{desc}（最后结果: {last!r}）")


def free_port(start: int) -> int:
    import socket
    for p in range(start, start + 50):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", p))
                return p
            except OSError:
                continue
    raise RuntimeError("找不到空闲端口")


class Checks:
    def __init__(self) -> None:
        self.failed: list[str] = []
        self.passed = 0

    def ok(self, cond: bool, desc: str, extra: str = "") -> bool:
        if cond:
            self.passed += 1
            log(f"  ✓ {desc}")
        else:
            self.failed.append(desc)
            log(f"  ✗ {desc} {extra}")
        return bool(cond)


def main() -> int:
    ap = argparse.ArgumentParser(description="冻结版端到端验收")
    ap.add_argument("--app-dir", default=str(DEFAULT_APP))
    ap.add_argument("--exe-name", default=None, help="默认自动探测 astercore(.exe)")
    ap.add_argument("--keep", action="store_true", help="保留临时目录（排障）")
    ap.add_argument("--browser", action="store_true",
                    help="额外做浏览器级验证（需 playwright；验证面板 JS 真的在运行）")
    args = ap.parse_args()

    src_app = Path(args.app_dir)
    if not src_app.is_dir():
        log(f"找不到打包目录 {src_app}（先运行 PyInstaller 构建）")
        return 2

    exe_name = args.exe_name or ("astercore.exe" if os.name == "nt" else "astercore")
    tmp = Path(tempfile.mkdtemp(prefix="astercore-e2e-"))
    workdir = tmp / "astercore"          # 模拟用户解压出来的目录
    shutil.copytree(src_app, workdir)
    exe = workdir / exe_name
    if not exe.exists():
        log(f"找不到主程序 {exe}")
        return 2

    ws_port = free_port(19801)
    panel_port = free_port(19901)
    checks = Checks()
    procs: list[subprocess.Popen] = []
    fake_log = tmp / "fake.log"
    app_log = tmp / "app.log"

    try:
        # ---------- 1. 起假 OneBot 后端（代替 NapCat） ----------
        log(f"启动假 OneBot 后端 :{ws_port}")
        fake_out = open(fake_log, "w", encoding="utf-8")
        procs.append(subprocess.Popen(
            [sys.executable, str(ROOT / "tools" / "fake_onebot.py"),
             "--port", str(ws_port), "--self", "10001"],
            stdout=fake_out, stderr=subprocess.STDOUT))
        wait_for(lambda: http_json(f"http://127.0.0.1:{ws_port}/received"), 15,
                 desc="假后端就绪")

        # ---------- 2. 像用户一样运行主程序（零额外配置） ----------
        log(f"运行打包主程序: {exe.name} --yes --no-browser --port {panel_port}")
        app_out = open(app_log, "w", encoding="utf-8")
        env = dict(os.environ, ASTER_NO_OPEN="1")
        procs.append(subprocess.Popen(
            [str(exe), "--yes", "--no-browser", "--port", str(panel_port)],
            cwd=str(workdir), stdout=app_out, stderr=subprocess.STDOUT, env=env))

        base = f"http://127.0.0.1:{panel_port}"
        info = wait_for(lambda: http_json(f"{base}/api/info")["data"], 60,
                        desc="面板启动")

        log("检查：首启引导与自描述")
        checks.ok(info.get("frozen") is True, "运行于打包模式(frozen)")
        checks.ok(bool(info.get("version")), f"版本号可得: v{info.get('version')}")
        checks.ok("data" in (info.get("data_dir") or ""), "数据目录指向 exe 同级",
                  f"({info.get('data_dir')})")
        checks.ok(Path(info["data_dir"]).parent.resolve() == workdir.resolve(),
                  "数据目录根 = 解压目录（不随启动位置乱跑）")

        # ---------- 3. 目录与示例插件自动就位 ----------
        log("检查：目录自动创建 + 示例插件播种")
        for d in ("data", "accounts", "plugins"):
            checks.ok((workdir / d).is_dir(), f"自动创建 {d}/ 目录")
        seeded = sorted(p.name for p in (workdir / "plugins").glob("*.py"))
        checks.ok("demo_hello.py" in seeded, "示例插件 demo_hello.py 已播种",
                  f"(实际: {seeded})")
        checks.ok("demo_counter.py" in seeded, "示例插件 demo_counter.py 已播种")

        # ---------- 4. 面板界面可访问（静态资源在包里） ----------
        with urllib.request.urlopen(f"{base}/", timeout=5) as r:
            html = r.read().decode("utf-8", "replace")
        checks.ok(r.status == 200 and "栖星 AsterCore" in html, "面板首页可打开")
        checks.ok("plugDir" in html, "面板含插件目录提示")

        # ---------- 5. 面板建号 → 启动 → 连后端 ----------
        log("检查：通过面板建号并启动")
        acct = "10001"
        r = http_json(f"{base}/api/accounts", "POST", {
            "account_id": acct,
            "display_name": "验收机器人",
            "backend": {"name": "onebot",
                        "ws_url": f"ws://127.0.0.1:{ws_port}",
                        "http_url": f"http://127.0.0.1:{ws_port}",
                        "access_token": ""},
        })
        checks.ok(r.get("ok") is True, "新建账号成功", str(r))

        r = http_json(f"{base}/api/accounts/{acct}/start", "POST", {})
        checks.ok(r.get("ok") is True, "启动账号成功", str(r))

        st = wait_for(
            lambda: (lambda d: d if d.get("connected") else None)(
                http_json(f"{base}/api/accounts/{acct}/status")["data"]),
            20, desc="账号连接到后端")
        checks.ok(st.get("connected") is True, "账号已连接后端(WS 握手成功)")

        # ---------- 6. 插件加载 ----------
        log("检查：插件被加载（来自用户可见的 plugins/ 目录）")
        plugs = http_json(f"{base}/api/accounts/{acct}/plugins")["data"]
        names = [p["name"] for p in plugs]
        checks.ok("demo_hello" in names, "demo_hello 已加载", f"(实际: {names})")
        checks.ok("demo_counter" in names, "demo_counter 已加载")
        hello = next((p for p in plugs if p["name"] == "demo_hello"), {})
        checks.ok(bool(hello.get("enabled", True)), "demo_hello 处于启用状态")

        # ---------- 7. 消息 → 插件 → 回复（真实闭环） ----------
        log("检查：假后端推送『你好』→ 插件回复")
        called = wait_for(lambda: any(
            c["action"] == "send_group_msg" for c in
            http_json(f"http://127.0.0.1:{ws_port}/received")["calls"]),
            15, desc="插件发出回复动作")
        calls = http_json(f"http://127.0.0.1:{ws_port}/received")["calls"]
        replies = [c for c in calls if c["action"] == "send_group_msg"]
        payload = json.dumps(replies[-1]["params"], ensure_ascii=False)
        checks.ok(bool(replies), "插件调用了 send_group_msg")
        checks.ok("你好呀" in payload, "回复内容正确(你好呀～)", f"({payload[:120]})")
        checks.ok(str(replies[-1]["params"].get("group_id")) == "987654321",
                  "回复目标群正确")

        # ---------- 8. 日志可见（用户排障依赖） ----------
        logs = http_json(f"{base}/api/accounts/{acct}/logs?after=0")["data"]["logs"]
        checks.ok(len(logs) > 0, f"面板可读到运行日志（{len(logs)} 条）")
        joined = json.dumps(logs, ensure_ascii=False)
        checks.ok("demo_hello" in joined or "命中" in joined,
                  "日志中有插件命中记录")

        # ---------- 8.5 浏览器级验证（面板 JS 是否真的在运行） ----------
        if args.browser:
            log("检查：浏览器级渲染（Playwright）")
            try:
                from playwright.sync_api import sync_playwright
            except ImportError:
                checks.ok(False, "playwright 未安装（--browser 需要）")
            else:
                with sync_playwright() as pw:
                    br = pw.chromium.launch()
                    page = br.new_page()
                    js_errors: list[str] = []
                    page.on("console", lambda m: js_errors.append(m.text)
                            if m.type == "error" else None)
                    page.on("pageerror", lambda e: js_errors.append(str(e)))
                    page.goto(base + "/", wait_until="networkidle", timeout=20000)
                    page.wait_for_timeout(2500)
                    ver_txt = page.inner_text("#ver").strip()
                    acct_txt = page.inner_text("#accts").strip()
                    plug_txt = page.inner_text("#plugins").strip()
                    dir_txt = page.inner_text("#plugDir").strip()
                    checks.ok(ver_txt.startswith("v"), f"面板显示版本号 ({ver_txt})")
                    checks.ok("加载中" not in acct_txt, "账号区未卡在“加载中…”")
                    checks.ok("验收机器人" in acct_txt or acct in acct_txt,
                              "账号卡片已渲染")
                    # 插件区应显示中文插件名（自动选中运行中账号）
                    checks.ok("打招呼" in plug_txt or "计数器" in plug_txt,
                              "插件列表已渲染(自动选中运行中账号)",
                              f"({plug_txt[:80]!r})")
                    checks.ok("插件目录" in dir_txt, "面板显示插件目录提示")
                    checks.ok(not js_errors, "面板无 JS 报错", f"({js_errors[:2]})")
                    page.screenshot(path=str(Path(tmp) / "panel.png"), full_page=True)
                    log(f"  面板截图: {Path(tmp) / 'panel.png'}")
                    br.close()

        # ---------- 9. 停止账号 ----------
        r = http_json(f"{base}/api/accounts/{acct}/stop", "POST", {})
        checks.ok(r.get("ok") is True, "停止账号成功")

    except Exception as e:
        checks.failed.append(f"异常中断: {type(e).__name__}: {e}")
        log(f"异常: {type(e).__name__}: {e}")
    finally:
        for p in procs:
            try:
                p.terminate()
                p.wait(timeout=8)
            except Exception:
                try:
                    p.kill()
                except Exception:
                    pass

        print("\n" + "=" * 58)
        if checks.failed:
            print(f"验收失败：通过 {checks.passed} 项，失败 {len(checks.failed)} 项")
            for f in checks.failed:
                print(f"  ✗ {f}")
        else:
            print(f"验收通过：全部 {checks.passed} 项检查通过 ✅")
        print(f"应用日志: {app_log}")
        print(f"假后端日志: {fake_log}")
        print("=" * 58)

        if args.keep:
            log(f"保留临时目录: {tmp}")
        else:
            if checks.failed:
                log(f"存在失败项，保留临时目录便于排障: {tmp}")
            else:
                shutil.rmtree(tmp, ignore_errors=True)

    return 1 if checks.failed else 0


if __name__ == "__main__":
    sys.exit(main())
