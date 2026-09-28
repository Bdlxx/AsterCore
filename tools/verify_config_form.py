# -*- coding: utf-8 -*-
"""浏览器级验证：**插件声明 schema → WebUI 自动渲染表单**（11 种字段类型逐个过）。

为什么要有这个脚本：schema/映射这套东西"看起来对"很容易，真跑起来才发现
某个控件没渲染、某个值收不上来、保存后又变回原样。这里用真 Chromium 点一遍，
并且**真的保存、真的回读**。

面板结构变了注意同步本脚本（2026-09-28 面板改版：骨架与字段控件都在两端共用的
`panel.js` / `cfg_form.js` 里，页面片段由后端 `panel_pages.py` 生成）：
  · 表单容器 `#configForm`（旧：`#cfg-form`）  · 声明徽标 `#cfgSrc`（旧：`#cfg-src`）
  · JSON 页签 `#configJson`（旧：`#cfg-text`） · 消息位 `#cfgMsg`（旧：`#cfg-msg`）
  · 打开页面走导航项 `[data-open=config][data-key=…]`（旧：`pick()` + 「配置」按钮）

用法：python3 tools/verify_config_form.py
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
PLUGIN = "schema_demo"

# 覆盖需求表里全部 11 种类型的声明（外加 json 扩展）
SCHEMA_PLUGIN = '''
__meta__ = {"name": "schema_demo", "name_cn": "全类型示例", "version": "1.0.0"}

__config_schema__ = {
    "title":    {"type": "text", "label": "标题", "maxlength": 20,
                 "placeholder": "给个标题"},
    "notes":    {"type": "textarea", "label": "备注", "rows": 3},
    "ratio":    {"type": "number", "label": "概率", "min": 0, "max": 100,
                 "step": 5, "unit": "%"},
    "enabled":  {"type": "bool", "label": "启用"},
    "mode":     {"type": "select", "label": "模式",
                 "options": [{"value": "fast", "label": "快速"},
                             {"value": "safe", "label": "稳妥"}]},
    "features": {"type": "multiselect", "label": "功能",
                 "options": ["签到", "抽奖", "点歌"]},
    "keywords": {"type": "list", "item_type": "text", "label": "关键词"},
    "api_key":  {"type": "password", "label": "API 密钥"},
    "groups":   {"type": "group_select", "label": "生效群"},
    "theme":    {"type": "color", "label": "主题色"},
    "cookie":   {"type": "file", "label": "Cookie 文件", "accept": ".json,.txt"},
    "extra":    {"type": "json", "label": "高级配置", "advanced": True},
}


def handle(event: dict) -> bool:
    return False
'''

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, bool(ok), detail))
    print(f"  {'✅' if ok else '❌'} {name}" + (f" — {detail}" if detail else ""))


@register_backend
class GroupBackend(Backend):
    """只为一件事：让 /groups 有数据可返回（群号选择器要靠它）"""
    name = "groupstub"

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    async def check(self) -> ActionResult:
        return ActionResult.success({})

    async def action(self, action: str, params: dict) -> ActionResult:
        if action == "get_group_list":
            return ActionResult.success([
                {"group_id": 315471269, "group_name": "测试群"},
                {"group_id": 957918829, "group_name": "词库群"},
            ])
        return ActionResult.success({"echo": action, **params})


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main() -> int:
    from playwright.sync_api import sync_playwright

    tmp = tempfile.TemporaryDirectory()
    base = Path(tmp.name)
    pdir = base / "plugins"
    pdir.mkdir(parents=True)
    (pdir / f"{PLUGIN}.py").write_text(SCHEMA_PLUGIN, encoding="utf-8")

    loop = asyncio.new_event_loop()
    threading.Thread(target=loop.run_forever, daemon=True).start()
    accounts = base / "accounts"
    inst = accounts / ACCT
    (inst / "plugins").mkdir(parents=True)
    (inst / "data").mkdir(parents=True)
    # 账号必须能被 scan() 认出来（否则前端拿不到 aid，群列表接口也不会被调）
    (inst / "backend.json").write_text(json.dumps({
        "display_name": "全类型示例号", "enabled": True,
        "backend": {"name": "groupstub", "ws_url": "", "http_url": "",
                    "access_token": ""},
    }, ensure_ascii=False), encoding="utf-8")

    rt = AccountRuntime(account_id=ACCT, data_dir=base / "data" / ACCT,
                        backend=GroupBackend(BackendConfig()),
                        plugin_dir=pdir, instance_dir=inst)
    asyncio.run_coroutine_threadsafe(rt.start(), loop).result(10)

    mgr = AccountManager(accounts, plugin_dir=pdir, data_root=base / "data")
    mgr.runtimes[ACCT] = rt
    panel = ManagerWebPanel(mgr, loop_provider=lambda: loop,
                            auth_file=base / "web_auth.json")
    port = free_port()
    serve_in_background(panel, host="127.0.0.1", port=port)
    for _ in range(60):
        with socket.socket() as s:
            s.settimeout(0.2)
            try:
                s.connect(("127.0.0.1", port))
                break
            except OSError:
                time.sleep(0.05)

    url = f"http://127.0.0.1:{port}/"
    errs: list[str] = []
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            page = browser.new_page()
            page.on("pageerror", lambda e: errs.append(str(e)))
            page.goto(url, wait_until="domcontentloaded")
            page.wait_for_timeout(2200)

            print("\n[1] 打开配置页（走真实导航项，不是直接调函数）")
            item = f'[data-open="config"][data-key="{PLUGIN}"]'
            exists = page.evaluate("s => !!document.querySelector(s)", item)
            check("侧栏插件菜单里有这个插件的入口", exists, item)
            if exists:
                page.click(item)
            else:
                page.evaluate("() => PANEL.openPage('config', '%s')" % PLUGIN)
            page.wait_for_timeout(1800)

            got = page.evaluate("""() => ({
                fields: [...document.querySelectorAll('#configForm .cf-field')]
                          .map(f => f.dataset.field),
                src: (document.querySelector('#cfgSrc') || {}).textContent || '',
                title: (document.querySelector('#cfgTitle') || {}).textContent || '',
                count: (document.querySelector('#cfgCount') || {}).textContent || '',
            })""")
            check("表单渲染出全部 12 个字段",
                  len(got["fields"]) == 12, f"实际 {len(got['fields'])}: {got['fields']}")
            check("标明了『由插件声明』", "由插件声明" in got["src"], got["src"])
            check("字段计数徽标正确", "12" in got["count"], got["count"])

            print("\n[2] 逐类型检查控件（需求给的映射表）")
            probes = page.evaluate("""() => {
              const f = n => document.querySelector(`#configForm [data-field="${n}"]`);
              const q = (n, sel) => f(n) ? f(n).querySelector(sel) : null;
              return {
                text:     q('title','input[type=text]')?.getAttribute('maxlength'),
                text_ph:  q('title','input[type=text]')?.getAttribute('placeholder'),
                textarea: q('notes','textarea')?.getAttribute('rows'),
                number:   [q('ratio','input[type=number]')?.min,
                           q('ratio','input[type=number]')?.max,
                           q('ratio','input[type=number]')?.step],
                unit:     f('ratio')?.querySelector('.cf-unit')?.textContent,
                bool:     !!q('enabled','input[type=checkbox]'),
                select:   [...(q('mode','select')?.options||[])].map(o => o.textContent),
                multi:    f('features')?.querySelectorAll('input[type=checkbox]').length,
                listRows: f('keywords')?.querySelectorAll('.cf-rowitem').length,
                listAdd:  !!f('keywords')?.querySelector('button'),
                pwd:      q('api_key','input')?.type,
                pwdBtn:   !!f('api_key')?.querySelector('button'),
                gsel:     q('groups','select')?.multiple,
                gopts:    [...(q('groups','select')?.options||[])].map(o => o.textContent),
                color:    q('theme','input[type=color]')?.type,
                colorHex: q('theme','input[type=text]')?.type,
                file:     q('cookie','input[type=text]')?.type,
                fileHint: f('cookie')?.querySelector('.cf-help')?.textContent || '',
                adv:      !!document.querySelector('#configForm .cf-advanced'),
              };
            }""")
            check("text → 单行输入（maxlength 生效）", probes["text"] == "20",
                  f"maxlength={probes['text']}")
            check("text → placeholder 生效", probes["text_ph"] == "给个标题")
            check("textarea → 多行（rows=3）", probes["textarea"] == "3")
            check("number → 数字输入（min/max/step）",
                  probes["number"] == ["0", "100", "5"], str(probes["number"]))
            check("number → 单位后缀", probes["unit"] == "%")
            check("bool → 开关（checkbox）", probes["bool"])
            check("select → 下拉且带选项",
                  probes["select"] == ["（未设置）", "快速", "稳妥"], str(probes["select"]))
            check("multiselect → 多选（3 项）", probes["multi"] == 3, str(probes["multi"]))
            check("list → 动态列表行 + 添加按钮",
                  probes["listAdd"] and probes["listRows"] >= 0,
                  f"rows={probes['listRows']}")
            check("password → 密文框 + 显示按钮",
                  probes["pwd"] == "password" and probes["pwdBtn"], str(probes["pwd"]))
            # 首屏就要有群可选项：群数据晚到会让下拉是空的，用户以为"一个群都没有"
            check("group_select → 多选下拉，且群列表首屏已就位",
                  probes["gsel"] and any("测试群" in o for o in probes["gopts"]),
                  str(probes["gopts"]))
            check("color → 颜色控件 + hex 输入", probes["color"] == "color"
                  and probes["colorHex"] == "text")
            check("file → 路径输入 + accept 提示",
                  probes["file"] == "text" and ".json" in probes["fileHint"],
                  probes["fileHint"])
            check("advanced → 折叠在『高级选项』里", probes["adv"])

            print("\n[3] 动态列表真的能加/删")
            # 注意：第一次插入后 `[data-list] button` 会先匹配到新行的「删除」按钮，
            # 所以必须**先拿到"添加"按钮这个元素**再连点，不能每次都重新 querySelector
            page.evaluate("""() => {
                const box = document.querySelector('#configForm [data-list="keywords"]');
                const add = [...box.querySelectorAll('button')]
                  .find(b => b.textContent.includes('添加'));
                add.click(); add.click();
            }""")
            page.wait_for_timeout(250)
            added = page.evaluate("""() => document.querySelectorAll(
                '#configForm [data-list="keywords"] .cf-rowitem').length""")
            check("点『添加一项』真的新增行", added == 2, f"加后 {added} 行")
            page.evaluate("""() => document.querySelector(
                '#configForm [data-list="keywords"] .cf-rowitem button').click()""")
            page.wait_for_timeout(200)
            removed = page.evaluate("""() => document.querySelectorAll(
                '#configForm [data-list="keywords"] .cf-rowitem').length""")
            check("点『删除』真的移除行", removed == 1, f"删后 {removed} 行")

            print("\n[4] 填表 → 保存 → 回读（服务端校验过的值）")
            page.evaluate("""() => {
              const set = (n, v) => {
                const el = document.querySelector(`#configForm [data-k="${n}"]`);
                if (el) { el.value = v; el.dispatchEvent(new Event('input', {bubbles: true})); }
              };
              set('title', '我的标题');
              set('notes', '多行\\n备注');
              set('ratio', '55');
              set('api_key', 'sk-secret');
              set('theme', '#ff8800');
              set('cookie', 'C:/data/cookies.json');
              const on = document.querySelector('#configForm [data-field="enabled"] input[type=checkbox]');
              on.checked = true; on.dispatchEvent(new Event('change', {bubbles: true}));
              const md = document.querySelector('#configForm [data-k="mode"]');
              md.value = 'safe';
              const feats = document.querySelectorAll('#configForm [data-multi="features"]');
              feats[0].checked = true; feats[2].checked = true;
              const kw = document.querySelector('#configForm [data-list="keywords"] [data-item]');
              if (kw) kw.value = '签到';
              const gs = document.querySelector('#configForm [data-multi-select="groups"]');
              if (gs) [...gs.options].forEach(o => { o.selected = (o.value === '315471269'); });
            }""")
            page.evaluate("() => PANEL.saveConfig()")
            page.wait_for_timeout(1200)
            toast = page.evaluate("() => (document.querySelector('#toast')||{}).textContent || ''")
            check("保存后给出成功反馈", "已保存" in toast, toast)

            _code, body = _api_get(url, f"/api/accounts/{ACCT}/plugins/{PLUGIN}/config")
            cfg = body.get("data", {})
            check("bool 存成真值 true", cfg.get("enabled") is True, repr(cfg.get("enabled")))
            check("number 存成数字 55", cfg.get("ratio") == 55, repr(cfg.get("ratio")))
            check("list 存成列表", cfg.get("keywords") == ["签到"], repr(cfg.get("keywords")))
            check("multiselect 存成列表（勾了第 1、3 项）",
                  cfg.get("features") == ["签到", "点歌"],
                  repr(cfg.get("features")))
            check("group_select 存成**列表**（不是字符串）",
                  cfg.get("groups") == ["315471269"], repr(cfg.get("groups")))
            check("text/textarea/password/color/file 原样",
                  cfg.get("title") == "我的标题" and cfg.get("api_key") == "sk-secret"
                  and cfg.get("theme") == "#ff8800"
                  and cfg.get("cookie") == "C:/data/cookies.json",
                  json.dumps({k: cfg.get(k) for k in
                              ("title", "api_key", "theme", "cookie")},
                             ensure_ascii=False))
            check("保存后表单回读服务端值",
                  page.eval_on_selector('#configForm [data-k="title"]', "e => e.value")
                  == "我的标题")

            print("\n[5] 切到 JSON 页签应看到同一份数据（双向不丢）")
            page.click("#tab-json")
            page.wait_for_timeout(300)
            js_text = page.evaluate("() => document.querySelector('#configJson').value")
            try:
                parsed = json.loads(js_text)
            except ValueError as e:
                parsed = {}
                check("JSON 页签内容是合法 JSON", False, str(e))
            else:
                check("JSON 页签内容是合法 JSON", True)
            check("JSON 页签含刚保存的改造后的值",
                  parsed.get("ratio") == 55 and parsed.get("groups") == ["315471269"],
                  json.dumps({k: parsed.get(k) for k in ("ratio", "groups")},
                             ensure_ascii=False))

            print("\n[6] 服务端校验：坏值应标红到字段上")
            page.click("#tab-form")
            page.wait_for_timeout(300)
            page.evaluate("""() => {
              const el = document.querySelector('#configForm [data-k="ratio"]');
              el.value = '999'; el.dispatchEvent(new Event('input', {bubbles: true}));
            }""")
            page.evaluate("() => PANEL.saveConfig()")
            page.wait_for_timeout(1200)
            bad = page.evaluate("""() => ({
              bad: document.querySelectorAll('#configForm .cf-bad').length,
              err: (document.querySelector('#configForm .cf-err:not([hidden])')||{}).textContent||'',
              msg: (document.querySelector('#cfgMsg')||{}).textContent||'',
            })""")
            check("越界值被服务端拦下并标红该字段",
                  bad["bad"] >= 1 and ("不能大于" in bad["err"] or "ratio" in bad["err"]),
                  f"bad={bad['bad']} err={bad['err']!r} msg={bad['msg']!r}")
            _code, body2 = _api_get(url, f"/api/accounts/{ACCT}/plugins/{PLUGIN}/config")
            check("校验失败时一字节都没落盘（ratio 仍是 55）",
                  body2.get("data", {}).get("ratio") == 55,
                  repr(body2.get("data", {}).get("ratio")))

            check("全程零 JS 异常", not errs, "; ".join(errs[:3]))
            browser.close()
    finally:
        asyncio.run_coroutine_threadsafe(rt.stop(), loop).result(10)
        loop.call_soon_threadsafe(loop.stop)
        tmp.cleanup()

    failed = [n for n, ok, _ in results if not ok]
    print(f"\n{'=' * 60}\n通过 {len(results) - len(failed)}/{len(results)}")
    if failed:
        print("失败项：\n  - " + "\n  - ".join(failed))
    return 1 if failed else 0


def _api_get(base: str, path: str):
    import urllib.request
    with urllib.request.urlopen(base.rstrip("/") + path, timeout=10) as r:
        return r.status, json.loads(r.read().decode("utf-8"))


if __name__ == "__main__":
    sys.exit(main())
