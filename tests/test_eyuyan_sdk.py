# -*- coding: utf-8 -*-
"""易语言 SDK 的一致性测试

本机没有易语言、也没有 C 编译器 → 真机编译验证做不了；能钉住的是**文档/声明/骨架/桥/校验脚本
与 ABI 头文件之间不许漂移**，以及导出查找逻辑与宿主一致。
"""
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SDK = ROOT / "plugin-sdk" / "eyuyan"
HEADER = ROOT / "plugin-sdk" / "nap_plugin.h"
CHECKER = SDK / "check_dll_exports.py"

sys.path.insert(0, str(ROOT / "src"))


def _header_exports() -> set[str]:
    src = HEADER.read_text(encoding="utf-8")
    return set(re.findall(r"NAP_EXPORT\s+[\w\s\*]+?\s+(nap_plugin_\w+)\s*\(", src))


class EyuyanSdkConsistencyTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        import importlib.util
        spec = importlib.util.spec_from_file_location("check_dll_exports", CHECKER)
        cls.chk = importlib.util.module_from_spec(spec)
        sys.modules["check_dll_exports"] = cls.chk
        spec.loader.exec_module(cls.chk)
        cls.header_names = _header_exports()

    def test_checker_covers_every_header_export(self):
        """校验脚本的必查清单要覆盖头文件里的每个 NAP_EXPORT（漏一个就等于放过）"""
        listed = set(self.chk.REQUIRED) | set(self.chk.OPTIONAL)
        self.assertTrue(self.header_names, "头文件里没解析出导出名？")
        missing = self.header_names - listed
        self.assertEqual(missing, set(), f"校验脚本没覆盖：{missing}")
        self.assertIn("nap_plugin_reload", self.chk.OPTIONAL, "reload 是可选导出")

    def test_candidate_list_matches_host(self):
        """离线/独立运行时用的候选名，必须与宿主 native.symbol_candidates 完全一致"""
        from astercore.native import symbol_candidates
        for name in sorted(self.header_names):
            self.assertEqual(self.chk.candidates(name), symbol_candidates(name),
                             f"{name} 的候选名与宿主不一致（会出现在宿主里找不到、这里却通过）")

    def test_describe_reports_decorated_names(self):
        """被修饰的导出名要能认出来，并把"实际导出名"报给用户"""
        class Fake:
            pass

        lib = Fake()
        lib._nap_plugin_abi_version = lambda: 1
        lib.nap_plugin_name = lambda: b"my_plugin"
        lib._nap_plugin_version = lambda: b"0.1.0"
        lib.nap_plugin_init = lambda *a: 0
        lib._nap_plugin_on_event = lambda *a: 0
        lib.nap_plugin_unload = lambda: None
        info = self.chk.describe(lib)
        self.assertEqual(info["missing"], [], f"不该报缺失：{info}")
        self.assertEqual(info["values"]["abi_version"], 1)
        self.assertEqual(info["values"]["name"], "my_plugin")
        self.assertEqual(info["values"]["version"], "0.1.0")
        self.assertEqual(info["exports"]["nap_plugin_on_event"], "_nap_plugin_on_event")

    def test_describe_reports_missing(self):
        class Fake:
            pass

        info = self.chk.describe(Fake())
        self.assertIn("nap_plugin_init", info["missing"])
        self.assertEqual(info["exports"]["nap_plugin_name"], "")

    def test_declaration_and_skeleton_cover_exports(self):
        """声明文件与骨架里都要出现这 7 个导出名（文档不许悄悄漏）"""
        decl = (SDK / "nap_plugin_易语言声明.txt").read_text(encoding="utf-8")
        skel = (SDK / "骨架_易语言.txt").read_text(encoding="utf-8")
        for name in sorted(self.header_names):
            self.assertIn(name, decl, f"声明文件缺 {name}")
            self.assertIn(name, skel, f"骨架缺 {name}")

    def test_bridge_matches_napapi_field_order(self):
        """C 桥的结构体字段顺序必须与 nap_plugin.h 的 NapApi 一致，且 README 的偏移表对得上"""
        bridge = (SDK / "nap_eyuyan_bridge.c").read_text(encoding="utf-8")
        body = bridge.split("typedef struct NapApi {", 1)[1].split("} NapApi;", 1)[0]
        seen: list[str] = []
        for name in re.findall(r"\b(abi_version|action|log|alloc|free)\b", body):
            if name not in seen:
                seen.append(name)
        self.assertEqual(seen, ["abi_version", "action", "log", "alloc", "free"],
                         f"C 桥的结构体字段顺序必须与 nap_plugin.h 一致：{body}")
        # README 的偏移表（x64：int 后 4 字节填充 → 指针从 8 开始，每 8 字节一个）
        readme = (SDK / "README.md").read_text(encoding="utf-8")
        for off in ("**8**", "**16**", "**24**", "**32**", "| 40 |"):
            self.assertIn(off, readme, f"README 的偏移表缺 {off}（x64 对齐后指针从 8 开始）")
        self.assertIn("结构体大小 40 字节", (SDK / "nap_plugin_易语言声明.txt").read_text(encoding="utf-8"))

    def test_old_examples_path_points_here(self):
        old = (ROOT / "plugin-sdk" / "examples" / "yiyuyan" / "README.md").read_text(encoding="utf-8")
        self.assertIn("eyuyan", old, "旧路径要指到新目录，不然两份文档会漂移")


if __name__ == "__main__":
    unittest.main()