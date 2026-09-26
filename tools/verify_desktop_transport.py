# -*- coding: utf-8 -*-
"""浏览器级验证：同一份面板 HTML 在两种传输通道下都能工作（施工手册 §1.5）。

  A. 浏览器模式：真实 Flask 服务 + http:// 打开 → 走 HTTP
  B. 桌面模式  ：file:// 打开 + 注入 window.pywebview.api.rpc 替身
                 → 走 js_api 进程内 RPC，**全程不起任何 HTTP 监听**

B 是最关键的一条：如果 fetch 垫片或 pywebviewready 处理有问题，
面板会白屏 / #ver 为空，因为 file:// 下 fetch('/api/...') 根本不可能成功。

用法：python3 tools/verify_desktop_transport.py
"""
from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from astercore.core.manager import AccountManager            # noqa: E402
from astercore.rpc import build_hub                          # noqa: E402
from astercore.web.server import ManagerWebPanel, serve_in_background  # noqa: E402

INDEX = Path(__file__).resolve().parent.parent / "src/astercore/web/static/index.html"
results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, bool(ok), detail))
    print(f"  {'✅' if ok else '❌'} {name}" + (f" — {detail}" if detail else ""))


def make_panel(tmp: Path):
    accounts = tmp / "accounts"
    accounts.mkdir(parents=True, exist_ok=True)
    acct = accounts / "10001"
    acct.mkdir()
    (acct / "backend.json").write_text(json.dumps({
        "account_id": "10001", "display_name": "测试号",
        "backend_name": "onebot", "ws_url": "ws://127.0.0.1:3001",
        "http_url": "http://127.0.0.1:3000", "access_token": "",
    }, ensure_ascii=False), encoding="utf-8")
    loop = asyncio.new_event_loop()
    mgr = AccountManager(accounts_dir=accounts, data_root=tmp / "data")
    panel = ManagerWebPanel(mgr, loop_provider=lambda: loop,
                            auth_file=tmp / "web_auth.json")
    return panel, mgr


def main() -> int:
    from playwright.sync_api import sync_playwright

    with tempfile.TemporaryDirectory() as td, sync_playwright() as pw:
        tmp = Path(td)
        panel, _mgr = make_panel(tmp)
        hub = build_hub()
        hub.bind_panel(panel)

        browser = pw.chromium.launch()
        try:
            # ---------------- A. 浏览器模式（HTTP） ----------------
            print("\n[A] 浏览器模式（HTTP）")
            port = _free_port()
            serve_in_background(panel, host="127.0.0.1", port=port)
            _wait_listen("127.0.0.1", port)
            page = browser.new_page()
            errs: list[str] = []
            page.on("pageerror", lambda e: errs.append(str(e)))
            page.goto(f"http://127.0.0.1:{port}/", wait_until="domcontentloaded")
            page.wait_for_timeout(1200)
            link = page.inner_text("#link")
            ver = page.inner_text("#ver")
            check("通道判定为浏览器模式", "浏览器模式" in link, link)
            check("数据经 HTTP 到达（版本号已填充）", ver.strip() != "", repr(ver))
            check("零 JS 异常", not errs, "; ".join(errs[:3]))
            page.close()

            # ---------------- B. 桌面模式（file:// + js_api） ----------------
            print("\n[B] 桌面模式（file:// + 注入 pywebview，进程内 RPC）")
            page = browser.new_page()
            errs = []
            page.on("pageerror", lambda e: errs.append(str(e)))

            def rpc(method, params=None):
                return hub.dispatch(method, params or {})

            page.expose_function("__acRpc", rpc)
            # 在页面脚本之前注入 pywebview 替身（模拟 pywebview 的 js_api 注入）
            page.add_init_script("""
                window.pywebview = { api: { rpc: (m, p) => window.__acRpc(m, p) } };
            """)
            page.goto(INDEX.as_uri(), wait_until="domcontentloaded")
            page.wait_for_timeout(1200)
            link = page.inner_text("#link")
            ver = page.inner_text("#ver")
            check("通道判定为桌面模式", "桌面模式" in link, link)
            check("数据经 js_api 到达（版本号已填充）", ver.strip() != "", repr(ver))
            check("fetch 垫片已安装", page.evaluate("() => !!window.__acShim"))
            check("零 JS 异常", not errs, "; ".join(errs[:3]))
            page.close()

            # ---------------- C. 延迟注入（pywebviewready 等待路径） ----------------
            print("\n[C] 桌面模式 · pywebview 延迟注入（模拟慢注入）")
            page = browser.new_page()
            errs = []
            page.on("pageerror", lambda e: errs.append(str(e)))
            page.expose_function("__acRpc2", rpc)
            page.add_init_script("""
                // 故意不注入，等页面加载后再注入并派发 pywebviewready
                window.__lateRpc = (m, p) => window.__acRpc2(m, p);
            """)
            page.goto(INDEX.as_uri(), wait_until="domcontentloaded")
            page.wait_for_timeout(200)
            page.evaluate("""() => {
                window.pywebview = { api: { rpc: window.__lateRpc } };
                window.dispatchEvent(new Event('pywebviewready'));
            }""")
            page.wait_for_timeout(1200)
            link = page.inner_text("#link")
            ver = page.inner_text("#ver")
            check("慢注入后仍切到桌面模式", "桌面模式" in link, link)
            check("慢注入后数据仍到达", ver.strip() != "", repr(ver))
            check("零 JS 异常", not errs, "; ".join(errs[:3]))
            page.close()

            # ---------------- D. 日志 DOM 上限（手册 §6.3） ----------------
            print("\n[D] 日志渲染上限（防几千个 DOM 节点卡死）")
            page = browser.new_page()
            errs = []
            page.on("pageerror", lambda e: errs.append(str(e)))
            page.expose_function("__acRpc3", rpc)
            page.add_init_script("""
                window.pywebview = { api: { rpc: (m, p) => window.__acRpc3(m, p) } };
            """)
            page.goto(INDEX.as_uri(), wait_until="domcontentloaded")
            page.wait_for_timeout(1000)
            n = page.evaluate("""() => {
                const box = document.querySelector('#logs');
                box.innerHTML = '';
                for (let i = 0; i < 2000; i++) {
                  const d = document.createElement('div'); d.className = 'l';
                  box.appendChild(d);
                  trimLogs(box, LOG_MAX_ROWS);
                }
                return box.childElementCount;
            }""")
            check(f"2000 条日志后 DOM 被裁到上限（实测 {n}）", n <= 500 and n > 0,
                  f"childElementCount={n}, LOG_MAX_ROWS=500")
            check("零 JS 异常", not errs, "; ".join(errs[:3]))
            page.close()
        finally:
            browser.close()

    bad = [n for n, ok, _ in results if not ok]
    print(f"\n{'=' * 56}\n通过 {len(results) - len(bad)}/{len(results)}")
    if bad:
        print("失败项：" + "、".join(bad))
    return 1 if bad else 0


def _free_port() -> int:
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_listen(host: str, port: int, tries: int = 60) -> None:
    import socket
    import time
    for _ in range(tries):
        with socket.socket() as s:
            s.settimeout(0.2)
            try:
                s.connect((host, port))
                return
            except OSError:
                time.sleep(0.05)


if __name__ == "__main__":
    sys.exit(main())
