# -*- coding: utf-8 -*-
"""栖星 AsterCore · 插件脚手架：生成一个新插件（并把 nap_sdk.py 拷到同目录）

用法：
    python new_plugin.py 我的插件 --name-en my_plugin [--dir <插件目录>] [--force]
    python new_plugin.py 签到 --name-en sign_in --dir "D:/AsterCore/data/10001/plugins"

不给 --dir 就只打印"该放到哪儿"（两端插件目录约定）。
"""
from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SDK_NAME = "nap_sdk.py"

TEMPLATE = '''# -*- coding: utf-8 -*-
"""{name_cn} —— 一句话说明这个插件干什么（这份文档会显示在面板的字段说明里）"""

try:                                     # 宿主机：两端都把插件目录挂成 plugins 命名空间
    from plugins import nap_sdk as sdk
except ImportError:                      # 离线自测：同目录直接跑
    import nap_sdk as sdk

# ---------------- 元数据 ----------------
sdk.set_meta(globals(),
             name_cn="{name_cn}",
             name_en="{name_en}",
             version="0.1.0",
             desc="一句话描述",
             author="你的名字")

# ---------------- 配置（一次声明：Linux 的 WebUI 与 Windows 面板都会出表单） ----------------
sdk.declare_config({{
    "commands": {{
        "hello": ("你好", "打个招呼"),
    }},
    "settings": {{
        "reply": ("回复文案", "你好呀～"),
    }},
    "messages": {{}},
}}, scope=globals())


# ---------------- 规则 ----------------
@sdk.rule(sdk.cmd("你好", "hi"))
def hello(event: dict) -> bool:
    """回一句问候（示例；改这里就行）"""
    sdk.reply(event, sdk.cfg("settings.reply", "你好呀～"))
    return True


# 需要"每个群单独开关"时加这一行（默认所有群关闭，与 Linux 版一致）：
#   @sdk.rule(sdk.in_group(sdk.cmd("签到")))
#   def sign_in(event):
#       if not sdk.enabled(event):
#           return False
#       ...

sdk.help_rule()                          # 发「帮助」回一条命令表（可选）


if __name__ == "__main__":               # 离线自测：python {name_en}.py
    for text in ("你好", "帮助"):
        sdk.dev(globals(), raw_message=text)
    print("检查：", sdk.check(globals()) or "通过 ✅")
'''

PLUGIN_DIR_HINT = """该把插件放到哪：
  Windows : data/<账号ID>/plugins/            （面板里「账号」页也能看到数据目录）
  Linux   : instances/<账号ID>/plugins/
  文件名必须等于 --name-en（本例 {name_en}.py），否则分群开关按名字匹配会失效。"""


def _slug(text: str) -> str:
    """把中文名转成一个可用的英文名（用户没给 --name-en 时的兜底）"""
    s = re.sub(r"[^0-9a-zA-Z_]+", "_", text.strip().lower()).strip("_")
    return s or "my_plugin"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="new_plugin", description="生成一个 AsterCore 插件")
    ap.add_argument("name_cn", help="插件中文名（面板显示）")
    ap.add_argument("--name-en", default="", help="插件英文名/文件名（默认按中文名转写）")
    ap.add_argument("--dir", default="", help="插件目录（不给就只打印提示）")
    ap.add_argument("--force", action="store_true", help="已存在同名文件时覆盖")
    args = ap.parse_args(argv)

    name_en = (args.name_en or _slug(args.name_cn)).strip()
    if not re.fullmatch(r"[0-9a-zA-Z_]+", name_en):
        print(f"英文名只能用字母数字下划线：{name_en!r}")
        return 2

    if not args.dir:
        print(PLUGIN_DIR_HINT.format(name_en=name_en))
        code = TEMPLATE.format(name_cn=args.name_cn, name_en=name_en)
        print("\n生成内容预览（加 --dir 就会落地）：\n" + "-" * 60)
        print(code)
        return 0

    out = Path(args.dir).expanduser()
    out.mkdir(parents=True, exist_ok=True)
    target = out / f"{name_en}.py"
    if target.exists() and not args.force:
        print(f"已存在：{target}（要覆盖加 --force）")
        return 1
    target.write_text(TEMPLATE.format(name_cn=args.name_cn, name_en=name_en),
                      encoding="utf-8", newline="\n")

    sdk_src = HERE / SDK_NAME
    sdk_dst = out / SDK_NAME
    if sdk_src.is_file() and (not sdk_dst.exists() or args.force):
        shutil.copy(sdk_src, sdk_dst)

    print(f"✅ 已生成 {target}")
    print(f"✅ SDK  : {sdk_dst}" + ("" if sdk_src.is_file() else "（没找到 nap_sdk.py，请手动拷过去）"))
    print("\n下一步：")
    print(f'  cd "{out}" && python {name_en}.py        # 离线自测')
    print("  面板里刷新账号 → 插件列表应能看到它；群里发「你好」试试")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())