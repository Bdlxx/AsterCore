# -*- coding: utf-8 -*-
"""浏览器级验证：**两端同一套侧边栏多页面板**在 Windows 端真的可用。

覆盖：骨架由共用 panel.js 注入、六个页面全部渲染、多账号（新建/切换/编辑/删除）、
插件配置（schema 驱动 + 真保存回读）、运行日志、运行方式/安全设置、登录闸门、
零 JS 异常。与 tools/verify_config_form.py 分工：那个专测"控件类型 → 表单"，
这个测"整套界面骨架与页面流程"。

用法：python3 tools/verify_panel_ui.py
"""
from __future__ import annotations

import asyncio
import json
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from astercore.core.backend import Backend, BackendConfig, register_backend  # noqa: E402
from astercore.core.manager import AccountManager                            # noqa: E402
from astercore.core.models import ActionResult                               # noqa: E402
from astercore.core.runtime import AccountRuntime                            # noqa: E402
from astercore.web.server import ManagerWebPanel, serve_in_background        # noqa: E402

ACCT = "10001"
OTHER = "10002"

DEMO_PLUGIN = '''
from astercore.core.plugin import Plugin, PluginMeta

__meta__ = PluginMeta(name="ui_demo", name_cn="界面示例", version="1.0.0")

__config_schema__ = {
    "greeting": {"type": "text", "label": "问候语", "group": "基础", "default": "你好"},
    "times":    {"type": "number", "label": "次数", "group": "基础", "min": 1, "max": 50, "default": 3},
    "enabled":  {"type": "bool", "label": "启用", "group": "基础", "default": True},
}


class ui_demo(Plugin):
    async def handle(self, event):
        return None


plugin = ui_demo()
'''

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok), str(detail)[:200]))
    print(("  ✅ " if ok else "  ❌ ") + name + ((" — " + str(detail)[:150]) if detail else ""))


class GroupBackend(Backend):
    """假后端：让状态/群列表/发送都有回应"""
    name = "fakestub"

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    async def check(self) -> ActionResult:
        return ActionResult.success({})

    async def action(self, action: str, params: dict) -> ActionResult:
        if action == "get_group_list":
            return ActionResult.success([{"group_id": 315471269, "group_name": "测试群"}])
        return ActionResult.success({"echo": action, **params})

    def status(self) -> dict:
        return {"connected": True}


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_port(port: int) -> None:
    for _ in range(80):
        with socket.socket() as s:
            s.settimeout(0.2)
            try:
                s.connect(("127.0.0.1", port))
                return
            except OSError:
                time.sleep(0.05)


def main() -> int:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("需要 playwright：pip install playwright && playwright install chromium")
        return 2
    register_backend("fake", GroupBackend)

    tmp = tempfile.TemporaryDirectory()
    base = Path(tmp.name)
    pdir = base / "plugins"
    pdir.mkdir(parents=True)
    (pdir / "ui_demo.py").write_text(DEMO_PLUGIN, encoding="utf-8")

    loop = asyncio.new_event_loop()
    threading.Thread(target=loop.run_forever, daemon=True).start()

    accounts = base / "accounts"
    inst = accounts / ACCT
    (inst / "plugins").mkdir(parents=True)
    (inst / "data").mkdir(parents=True)
    (inst / "backend.json").write_text(json.dumps({
        "display_name": "依星", "enabled": True,
        "backend": {"name": "fake", "ws_url": "", "http_url": "", "access_token": ""},
    }, ensure_ascii=False), encoding="utf-8")

    rt = AccountRuntime(account_id=ACCT, data_dir=base / "data" / ACCT,
                        backend=GroupBackend(BackendConfig()),
                        plugin_dir=pdir, instance_dir=inst)
    asyncio.run_coroutine_threadsafe(rt.start(), loop).result(10)

    mgr = AccountManager(accounts, plugin_dir=pdir, data_root=base / "data")
    mgr.runtimes[ACCT] = rt
    panel = ManagerWebPanel(mgr, loop_provider=lambda: loop, auth_file=base / "web_auth.json")
    port = free_port()
    serve_in_background(panel, host="127.0.0.1", port=port)
    wait_port(port)

    url = f"http://127.0.0.1:{port}/"
    errs: list[str] = []
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            page = browser.new_page(viewport={"width": 1440, "height": 940})
            page.on("pageerror", lambda e: errs.append(str(e)))
            page.goto(url, wait_until="domcontentloaded")
            page.wait_for_timeout(2500)

            print("\n[1] 骨架由共用 panel.js 注入（两端结构一致）")
            shell = page.evaluate("""() => {
                const has = s => !!document.querySelector(s);
                return {ready: has('#appReady'), sidebar: has('#sidebarNav'), header: has('header.header'),
                        content: has('#pageContent'), bar: has('#actionBar'), toast: has('#toast'),
                        modal: has('#uiModal'), icons: document.querySelectorAll('svg.ico').length,
                        tokens: getComputedStyle(document.documentElement).getPropertyValue('--bg-card').trim()};
            }""")
            check("外壳骨架齐全", all([shell["ready"], shell["sidebar"], shell["header"],
                                       shell["content"], shell["bar"], shell["toast"], shell["modal"]]), shell)
            check("图标精灵已水合", shell["icons"] >= 8, f'{shell["icons"]} 个')
            check("设计令牌已生效（与 Linux 同源）", bool(shell["tokens"]), shell["tokens"])

            print("\n[2] 侧边栏导航与六个页面")
            nav = page.eval_on_selector_all(".nav-item", "els=>els.map(e=>e.textContent.trim())")
            check("导航项与 Linux 同一套结构（桌面端多账号/设置）",
                  len(nav) >= 6 and any("概览" in n for n in nav), nav)
            pages = {"dashboard": ["#dashBotVal", ".stat-card"], "accounts": ["#ac_id", ".stat-card"],
                     "config": ["#configForm", "#tab-json"], "logs": ["#logViewer", "#logLinesSel"],
                     "settings-runtime": ["#rt_mode"], "settings-auth": ["#au_mode"]}
            for p, sels in pages.items():
                page.evaluate("p => PANEL.goPage(p)", p)
                page.wait_for_timeout(1400)
                got = page.evaluate("s => s.map(x => !!document.querySelector(x))", sels)
                check(f"页面[{p}] 渲染", all(got), f'锚点={got} crumb={page.text_content("#breadcrumb").strip()}')
            check("逐页切换零 JS 异常", not errs, errs[:2])

            print("\n[3] 多账号：切换 / 新建 / 编辑 / 删除")
            page.evaluate("() => PANEL.openPage('accounts')")
            page.wait_for_timeout(1200)
            page.click("[data-acct-pick]")
            page.wait_for_timeout(1200)
            check("点账号切换成功（侧栏身份同步）",
                  "依星" in page.text_content("#botName"), page.text_content("#botName"))

            page.evaluate("""() => {
                const set = (id, v) => { const el = document.getElementById(id); el.value = v; };
                set('ac_id', '%s'); set('ac_name', '第二个账号');
                set('ac_ws', 'ws://127.0.0.1:3999'); set('ac_http', 'http://127.0.0.1:3998');
            }""" % OTHER)
            page.click("[data-act='create-account']")
            page.wait_for_timeout(1500)
            accs = page.eval_on_selector_all("[data-acct-pick]", "els=>els.map(e=>e.getAttribute('data-acct-pick'))")
            check("新建账号出现在列表", OTHER in accs, accs)

            page.click(f"[data-act='edit-account'][data-key='{OTHER}']")
            page.wait_for_timeout(1500)
            check("编辑态表单预填现值",
                  page.eval_on_selector("#ac_id", "e=>e.value") == OTHER
                  and page.eval_on_selector("#ac_name", "e=>e.value") == "第二个账号",
                  page.eval_on_selector("#ac_name", "e=>e.value"))
            page.eval_on_selector("#ac_name", "e=>{e.value='改名了';}")
            page.click("[data-act='save-account']")
            page.wait_for_timeout(1500)
            page.evaluate("() => PANEL.goPage('accounts')")
            page.wait_for_timeout(1200)
            names = page.eval_on_selector_all(".a-name", "els=>els.map(e=>e.textContent.trim())")
            check("账号改名已保存", "改名了" in names, names)

            page.click(f"[data-act='del-account'][data-key='{OTHER}']")
            page.wait_for_timeout(500)
            check("删除账号前有确认弹窗", page.is_visible("#uiModal") and "删除账号" in page.text_content("#uiModalTitle"))
            page.click("#uiModalOk")
            page.wait_for_timeout(1600)
            accs2 = page.eval_on_selector_all("[data-acct-pick]", "els=>els.map(e=>e.getAttribute('data-acct-pick'))")
            check("账号已删除", OTHER not in accs2, accs2)

            print("\n[4] 插件配置：schema 驱动 + 真保存回读")
            page.evaluate("() => PANEL.openPage('config', 'ui_demo')")
            page.wait_for_timeout(2000)
            check("插件声明徽标", "由插件声明" in page.text_content("#cfgSrc"), page.text_content("#cfgSrc"))
            check("控件按声明渲染", page.eval_on_selector_all("#configForm .cf-field", "e=>e.length") == 3,
                  page.eval_on_selector_all("#configForm .cf-field", "e=>e.length"))
            page.eval_on_selector("[data-k='greeting']", "e=>{e.value='改过的问候'; e.dispatchEvent(new Event('input',{bubbles:true}))}")
            page.wait_for_timeout(300)
            check("编辑后出现未保存标记", page.is_visible("#actionBarDirty"))
            page.click("#actionSaveBtn")
            page.wait_for_timeout(2000)
            saved = json.loads((base / "data" / ACCT / "plugins" / "ui_demo" / "config.json").read_text(encoding="utf-8"))
            check("保存真的写进配置文件", saved.get("greeting") == "改过的问候", saved)
            check("保存后表单回读服务端值",
                  page.eval_on_selector("[data-k='greeting']", "e=>e.value") == "改过的问候")

            print("\n[5] 运行日志页（结构化条目 → 文本行）")
            page.evaluate("() => PANEL.goPage('logs')")
            page.wait_for_timeout(2000)
            text = page.text_content("#logViewer") or ""
            check("日志查看器有内容", len(text.strip()) > 0, text.strip()[:80])
            check("行数下拉可用", page.eval_on_selector("#logLinesSel", "e=>!!e"))

            print("\n[6] 运行方式 / 安全设置可保存")
            page.evaluate("() => PANEL.goPage('settings-auth')")
            page.wait_for_timeout(1500)
            page.select_option("#au_mode", "password")
            page.eval_on_selector("#au_pwd", "e=>{e.value='test-password-123';}")
            page.click("#actionSaveBtn")
            page.wait_for_timeout(1800)
            auth = json.loads((base / "web_auth.json").read_text(encoding="utf-8"))
            check("安全设置已落盘", auth.get("mode") == "password", auth.get("mode"))

            print("\n[7] 登录闸门（改成 password 后要求登录）")
            page.context.clear_cookies()
            page.goto(url, wait_until="domcontentloaded")
            page.wait_for_timeout(2000)
            check("闸门出现", page.is_visible("#loginGate"), page.text_content("#gateHint")[:60])
            page.eval_on_selector("#gatePwd", "e=>{e.value='wrong';}")
            page.click("#gateBtn")
            page.wait_for_timeout(1200)
            check("错误密码被拒", "闸门出现" and page.is_visible("#loginGate"), page.text_content("#gateHint")[:60])
            page.eval_on_selector("#gatePwd", "e=>{e.value='test-password-123';}")
            page.click("#gateBtn")
            page.wait_for_timeout(3000)
            check("正确密码可进入面板", not page.is_visible("#loginGate")
                  and page.eval_on_selector("#pageContent", "e=>e.textContent.length > 20"))

            check("全程零 JS 异常", not errs, errs[:3])
            browser.close()
    finally:
        tmp.cleanup()

    bad = [r for r in RESULTS if not r[1]]
    print("\n" + "=" * 58)
    print(f"通过 {len(RESULTS) - len(bad)}/{len(RESULTS)}")
    for n, _, d in bad:
        print("  失败：", n, "—", d)
    print("=" * 58)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
