# -*- coding: utf-8 -*-
"""面板静态自检（无需浏览器 / playwright）—— 补 verify_panel_ui.py 的环境缺口

`tools/verify_panel_ui.py` 用真实浏览器点界面，最接近真实交互，但需要 playwright + GUI 环境；
在没环境的机器（CI、Linux 服务器、纯命令行）上就完全跑不了，于是这几轮我一直在**手工**核对
同一批约束（拖动区、文字可选中、三按钮接线、能力探测、图标不破图…）。

这个脚本把那些约束**静态化**：读文件 + 断言，几秒钟跑完，任何环境可用。
它不是 playwright 的替代，而是"在没法点界面时的兜底闸门"。

用法：
    python tools/check_panel_static.py            # 全跑，失败退出码 1
    python tools/check_panel_static.py -v         # 打印每条通过项
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "src" / "astercore" / "web" / "static"
IDX = STATIC / "index.html"
JS = STATIC / "panel.js"
CSS = STATIC / "panel.css"
SHELL = ROOT / "src" / "astercore" / "shell.py"
LAUNCH = ROOT / "src" / "astercore" / "launch.py"

_results: list[tuple[bool, str, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    _results.append((bool(ok), name, detail))


def text(p: Path) -> str:
    return p.read_text(encoding="utf-8", errors="replace") if p.is_file() else ""


def main(verbose: bool) -> int:
    idx, js, css, shell, launch = text(IDX), text(JS), text(CSS), text(SHELL), text(LAUNCH)

    # 1) 必备容器/控件存在（注意**来源不同**：外壳在 panel.js、页面片段在 ui_pages.py、
    #    窗口按钮/桌面条在 index.html —— 我第一版全查 index.html，误报 13 条）
    pages = text(ROOT / "src" / "astercore" / "web" / "ui_pages.py")
    where = {
        "index.html": ("desktopBar", "acctOverlay", "dbWinMin", "dbWinHide", "dbWinQuit"),
        # panel.js 的外壳元素**按实际结构判断**（本地线改版后 botName/botSub 已不存在，
        # 写死 id 就会误报 —— 这里改成"存在头像元素 + 存在身份文本区"）
        "panel.js": ("botAvatar",),
        "ui_pages.py": ("acctDlg", "ac_id", "ac_ws", "ac_http", "ac_token", "gdQQ",
                        "sp_mode", "sp_port", "rt_mode", "upBox", "upBarFill"),
    }
    srcs = {"index.html": idx, "panel.js": js, "ui_pages.py": pages}
    for src_name, ids in where.items():
        for _id in ids:
            check(f"{src_name} 有 #{_id}", f'id="{_id}"' in srcs[src_name])

    # 2) 自绘窗口三按钮：DOM + data-act + 处理器
    for bid, act in (("dbWinMin", "win-min"), ("dbWinHide", "win-hide"), ("dbWinQuit", "win-quit")):
        check(f"{bid} 存在", f'id="{bid}"' in idx)
        check(f"{bid} 挂了 data-act={act}", f'data-act="{act}"' in idx)
        check(f"存在动作处理器 {act}", f"'{act}':" in idx)

    # 3) 无边框窗口：拖动区 + 文字可选中（互斥规则必须同时存在）
    check("顶栏是拖动区", "-webkit-app-region:drag" in css)
    check("按钮排除拖动（否则点不动）", "no-drag" in css)
    check("正文可选中文字", "-webkit-user-select:text" in css)
    check("拖动区保持不可选（否则拖窗变选字）",
          re.search(r"#desktopBar,#desktopBar \*\s*\{[^}]*-webkit-user-select:none", css) is not None)
    check("shell 开了 text_select", "text_select=True" in shell)
    check("shell 是无边框窗口", "frameless=True" in shell)

    # 4) 双击顶栏 = 最大化（无边框后没有系统最大化按钮）
    check("顶栏绑了 dblclick", "addEventListener('dblclick'" in idx)
    check("双击调 maximize", "action: 'maximize'" in idx)
    # 实现无关：排除按钮的常见写法任一都算（我原来只认 tagName 比较，codex 用的是 closest）
    check("双击要排除按钮",
          any(x in idx for x in ("tagName === 'BUTTON'", 'tagName === "BUTTON"',
                                 "closest('button')", 'closest("button")',
                                 "matches('button')", 'matches("button")')))
    check("双击不再挂在已废弃的 #desktopBar 上",
          "desktopBar' ).addEventListener" not in idx and "desktopBar').addEventListener" not in idx)
    check("后端有 toggle_maximize", "def toggle_maximize" in shell)
    check("RPC 支持 maximize", 'action == "maximize"' in launch)

    # 5) 能力探测：Linux 端（没有这些容器/接口）必须整体 no-op，不能报错
    check("桌面条逻辑有容器守卫", re.search(r"if \(!bar \|\| !overlay\)\s*\{\s*return", js) is not None)
    check("接口不可用时不渲染顶栏（能力探测）", "/api/app/state" in js and "bar.style.display = 'none'" in js)

    # 6) 破图防线：不允许空 src 的 <img>
    empty_src = re.findall(r'<img[^>]*\ssrc=(?:"\\?"\\?"|\\?"\\?"|\'\')', idx)
    check("没有空 src 的 <img>（会显示破图）", not empty_src, str(empty_src[:2]))
    check("头像有内联兜底", "DEFAULT_AVATAR" in js and "data:image/svg+xml" in js)

    # 7) 顶栏位置提醒层级：提示必须盖在弹窗之上
    def zindex(sel: str) -> int:
        m = re.search(re.escape(sel) + r"\{[^}]*z-index:(\d+)", css)
        return int(m.group(1)) if m else -1
    tz, mz = zindex(".toast"), zindex(".ui-modal")
    check("提示层级高于弹窗", tz > mz > 0, f"toast={tz} modal={mz}")

    # 8) 版本号四处一致
    ver = re.search(r'__version__\s*=\s*"([^"]+)"', text(ROOT / "src" / "astercore" / "__init__.py"))
    ver = ver.group(1) if ver else "?"
    check("pyproject 版本一致", f'version = "{ver}"' in text(ROOT / "pyproject.toml"), ver)
    check("version_info 版本一致", f'"{ver}"' in text(ROOT / "pack" / "version_info.txt"), ver)

    # 8.5) 外壳的身份区按**结构**判断（不写死 id，改名/改版都不误报）
    m = re.search(r"SHELL_HTML\s*=\s*\"(.*?)\";", js, re.S)
    shell_ids = set(re.findall(r'id=\\+"([A-Za-z0-9_-]+)', m.group(1))) if m else set()
    check("外壳有头像元素", any("avatar" in i.lower() for i in shell_ids), str(sorted(shell_ids))[:120])
    check("外壳有身份文本区（名称/副标题任一）",
          any(("name" in i.lower() or "sub" in i.lower()) for i in shell_ids),
          str(sorted(shell_ids))[:120])

    # 9) 共用文件：本地存在且能被 Linux 端对上（跨仓比对交给 mybot2 的 test_dual_end_assets）
    for shared in ("panel.js", "panel.css", "cfg_form.js", "cfg_form.css"):
        check(f"共用文件存在 {shared}", (STATIC / shared).is_file())

    bad = [(n, d) for ok, n, d in _results if not ok]
    if verbose:
        for ok, n, d in _results:
            print(("  ✅ " if ok else "  ❌ ") + n + (f"    [{d}]" if d and not ok else ""))
    print(f"\n静态自检：{len(_results) - len(bad)}/{len(_results)} 通过")
    if bad:
        print("失败明细：")
        for n, d in bad:
            print(f"  ❌ {n}" + (f"    [{d}]" if d else ""))
        return 1
    print("全部通过 ✅（注意：这不替代 tools/verify_panel_ui.py 的真实浏览器验证）")
    return 0


if __name__ == "__main__":
    sys.exit(main("-v" in sys.argv or "--verbose" in sys.argv))
