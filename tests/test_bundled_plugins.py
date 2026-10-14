# -*- coding: utf-8 -*-
"""内置插件（随冻结包发布、首启播种）的契约守卫。

背景：用户要求"把原词库插件内置进冻结版"。做法是把 `wordlib.py` 放进
`src/astercore/plugin_templates/` —— 该目录在 `pack/astercore.spec` 里已作为 datas 分发，
且 `bootstrap.seed_example_plugins()` 首启把它抄进用户的 `plugins/`（**同名永不覆盖**），
所以零 CI 改动即可内置。

这条守卫钉住三件事，防止以后有人"顺手"把内置插件改成非兼容写法导致冻结版里跑不起来：
1. 语法可编译；
2. 保留 Linux 老插件的 5 个 dunder 元数据（`__plugin_name_cn__` 等）；
3. **只**依赖 stdlib + `utils.*` 兼容层 + `requests` —— 不许 import astercore 内部
   （老插件契约；一旦 import 内部，兼容层之外的地方就跑不了）。
"""
from __future__ import annotations

import ast
import unittest
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = ROOT / "src" / "astercore" / "plugin_templates"
BUNDLED = sorted(TEMPLATES.glob("*.py"))
# 走 **Linux 老插件契约** 的内置插件（只依赖 stdlib + utils 兼容层 + requests）；
# 其余是 AsterCore **原生**示例插件（demo_*），它们本来就 import astercore 内部，不受这条约束。
LEGACY = {"wordlib.py"}

# 允许的第三方：requests（pyproject 核心依赖，冻结包会带上）
ALLOWED_THIRD_PARTY = {"requests"}
STDLIB = set(sys.stdlib_module_names) if hasattr(sys, "stdlib_module_names") else set()


def _top_imports(src: str) -> list[str]:
    """收集模块级与函数级 import 的顶层包名。"""
    names: list[str] = []
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Import):
            names += [a.name.split(".")[0] for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.append(node.module.split(".")[0])
    return names


class BundledPluginGuardTest(unittest.TestCase):
    def test_wordlib_is_bundled(self):
        self.assertTrue((TEMPLATES / "wordlib.py").is_file(),
                        "词库插件应内置在 plugin_templates/（冻结包靠它随包发布 + 首启播种）")

    def test_all_bundled_plugins_compile(self):
        for f in BUNDLED:
            with self.subTest(plugin=f.name):
                compile(f.read_text(encoding="utf-8"), str(f), "exec")

    def test_wordlib_keeps_legacy_contract(self):
        src = (TEMPLATES / "wordlib.py").read_text(encoding="utf-8")
        for dunder in ("__plugin_name_cn__", "__plugin_name_en__", "__plugin_version__",
                       "__plugin_desc__", "__plugin_author__"):
            self.assertIn(dunder, src, f"老插件契约要求保留 {dunder}")

    def test_legacy_bundled_plugins_do_not_import_astercore_internals(self):
        for f in [x for x in BUNDLED if x.name in LEGACY]:
            with self.subTest(plugin=f.name):
                bad = [n for n in _top_imports(f.read_text(encoding="utf-8"))
                       if n == "astercore" or n.startswith("astercore.")]
                self.assertFalse(bad, f"{f.name} 不该 import astercore 内部（老插件靠 utils 兼容层）")

    def test_legacy_bundled_plugin_deps_are_available(self):
        """legacy 内置插件的第三方依赖必须在核心依赖里（否则冻结包里跑不起来）。"""
        for f in [x for x in BUNDLED if x.name in LEGACY]:
            for n in set(_top_imports(f.read_text(encoding="utf-8"))):
                if n in STDLIB or n in ("utils",) or n in ALLOWED_THIRD_PARTY:
                    continue
                self.fail(f"{f.name} 依赖了 {n}：请确认它在 pyproject 核心依赖里，"
                          f"或加进 ALLOWED_THIRD_PARTY 并同步 spec")


if __name__ == "__main__":
    import unittest as _u
    _u.main()
