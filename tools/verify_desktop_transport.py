# -*- coding: utf-8 -*-
"""浏览器级验证：同一份面板在两种传输通道下都能工作（施工手册 §1.5）。

  A. 浏览器模式：真实 Flask 服务 + http:// 打开 → 走 HTTP
  B. 桌面模式  ：file:// 打开 + 注入 window.pywebview.api.rpc 替身
                 → 走 js_api 进程内 RPC，**全程不起任何 HTTP 监听**
  C. 延迟注入  ：模拟 pywebview 慢注入（必须等 pywebviewready）
  D. 日志 DOM 上限：2000 行日志后只渲染最近 N 行（手册 §6.3）

B/C 是最关键的：如果 Transport 或 fetch 垫片有问题，file:// 下
`fetch('/api/...')` 根本不可能成功，面板会白屏 / 通道标签为空。

**面板结构变了注意同步本脚本**：通道与版本都显示在 `#headerExtraSlot` 里的
`#transportTag`（骨架与交互由两端共用的 panel.js 注入，见 tools/verify_panel_ui.py）。

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


def _text(page, sel: str) -> str:
    """元素不在就返回空串（比 inner_text 抛异常更适合做断言）"""
    try:
        return page.inner_text(sel)
    except Exception:
        return ""


def make_panel(tmp: Path):
    accounts = tmp / "accounts"
    accounts.mkdir(parents=True, exist_ok=True)
    acct = accounts / "10001"
    acct.mkdir()
    (acct / "backend.json").write_text(json.dumps({
        "display_name": "测试号", "enabled": True,
        "backend": {"name": "onebot", "ws_url": "ws://127.0.0.1:3001",
                    "http_url": "http://127.0.0.1:3000", "access_token": ""},
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
            def rpc(method, params=None):
                return hub.dispatch(method, params or {})

            # ---------------- A. 浏览器模式（HTTP） ----------------
            print("\n[A] 浏览器模式（HTTP）")
            port = _free_port()
            serve_in_background(panel, host="127.0.0.1", port=port)
            _wait_listen("127.0.0.1", port)
            page = browser.new_page()
            errs: list[str] = []
            page.on("pageerror", lambda e: errs.append(str(e)))
            page.goto(f"http://127.0.0.1:{port}/", wait_until="domcontentloaded")
            page.wait_for_timeout(1800)
            tag = _text(page, "#transportTag")
            extra = _text(page, "#headerExtraSlot")
            check("通道判定为浏览器模式", "浏览器模式" in tag, tag)
            check("数据经 HTTP 到达（版本号已填充）", "v" in extra, repr(extra))
            check("零 JS 异常", not errs, "; ".join(errs[:3]))
            page.close()

            def open_desktop_page(**kwargs):
                """file:// + 注入 pywebview 替身（B/C/D 共用）"""
                p = browser.new_page()
                e: list[str] = []
                p.on("pageerror", lambda ev: e.append(str(ev)))
                p.expose_function("__acRpc", rpc)
                p.add_init_script("""
                    window.pywebview = { api: { rpc: (m, pr) => window.__acRpc(m, pr) } };
                """)
                p.goto(INDEX.as_uri(), wait_until="domcontentloaded")
                p.wait_for_timeout(1800)
                return p, e

            # ---------------- B. 桌面模式（file:// + js_api） ----------------
            print("\n[B] 桌面模式（file:// + 注入 pywebview，进程内 RPC）")
            page, errs = open_desktop_page()
            tag = _text(page, "#transportTag")
            extra = _text(page, "#headerExtraSlot")
            check("通道判定为桌面模式", "桌面模式" in tag, tag)
            check("数据经 js_api 到达（版本号已填充）", "v" in extra, repr(extra))
            check("fetch 垫片已安装", page.evaluate("() => !!window.__acShim"))
            # 全程没有任何 HTTP 监听：账号列表也走了进程内 RPC
            check("进程内 RPC 真的取到了数据",
                  "测试号" in _text(page, "#botName"), _text(page, "#botName"))
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
            page.wait_for_timeout(1800)
            tag = _text(page, "#transportTag")
            extra = _text(page, "#headerExtraSlot")
            check("慢注入后仍切到桌面模式", "桌面模式" in tag, tag)
            check("慢注入后数据仍到达", "v" in extra, repr(extra))
            check("零 JS 异常", not errs, "; ".join(errs[:3]))
            page.close()

            # ---------------- D. 日志 DOM 上限（手册 §6.3） ----------------
            print("\n[D] 日志渲染上限（防几千个 DOM 节点卡死）")
            page, errs = open_desktop_page()
            page.evaluate("() => PANEL.goPage('logs')")
            page.wait_for_timeout(1500)
            got = page.evaluate("""() => {
                const st = PANEL.logState('');
                st.raw = Array.from({length: 2000},
                    (_, i) => '01-01 00:00:00 [info] 第 ' + i + ' 行日志').join('\\n');
                PANEL.filter = 'info';
                PANEL.renderLogViewer('');
                const v = document.querySelector('#logViewer');
                return {rows: v.childElementCount, max: PANEL.LOG_MAX_ROWS,
                        hint: (v.textContent || '').indexOf('已省略') >= 0};
            }""")
            check(f"2000 行日志后 DOM 被裁到上限（实测 {got['rows']} 行）",
                  got["rows"] > 0 and got["rows"] <= got["max"] + 1,
                  f"childElementCount={got['rows']}, LOG_MAX_ROWS={got['max']}")
            check("裁剪后给出提示（不是静默丢行）", got["hint"], got)
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
