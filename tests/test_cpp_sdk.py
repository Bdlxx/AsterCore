# -*- coding: utf-8 -*-
"""C++ 插件 SDK（plugin-sdk/cpp）测试

两层：
  ① **静态守卫**（永远跑）：SDK / 示例 / 构建脚本 / README 齐备；SDK 生成的导出名与
     `plugin-sdk/nap_plugin.h` 一致；不许 include 平台专属头、不许用 C++14/17 写法（保持 C++11）；
  ② **真编译 + 真加载**（本机有编译器才跑，没有就 skip）：编 SDK 自测 exe 跑 100+ 条断言 →
     编示例插件 DLL → `plugin-sdk/tools/check_dll_exports.py` 校验导出 → **用真实宿主**
     (`astercore.native.NativePlugin`) 派发事件、断言插件真的请求了 send_group。

编译器查找：环境变量 `ASTERCORE_CXX` → `python -m ziglang`（pip 装过就用；本机就是这么跑的）
→ PATH 里的 `cl` / `clang++` / `g++`。找不到就只跑静态那一层（**不静默跳过**：会打印原因）。
"""
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SDK_DIR = ROOT / "plugin-sdk" / "cpp"
HEADER = SDK_DIR / "nap_plugin.hpp"
EXAMPLE = SDK_DIR / "example" / "hello_plugin.cpp"
SDK_TESTS = SDK_DIR / "tests" / "test_sdk.cpp"
CHECKER = ROOT / "plugin-sdk" / "tools" / "check_dll_exports.py"
ABI_HEADER = ROOT / "plugin-sdk" / "nap_plugin.h"

sys.path.insert(0, str(ROOT / "src"))

REQUIRED_EXPORTS = ("nap_plugin_abi_version", "nap_plugin_name", "nap_plugin_version",
                    "nap_plugin_init", "nap_plugin_on_event", "nap_plugin_unload")
OPTIONAL_EXPORTS = ("nap_plugin_reload",)


def _compiler_candidates() -> list[list[str]]:
    """候选编译器（按优先级）：环境变量 ASTERCORE_CXX → zig（pip 装过就有）→ PATH 里的 cl/clang++/g++"""
    out: list[list[str]] = []
    env = os.environ.get("ASTERCORE_CXX")
    if env:
        out.append(shlex.split(env))
    try:
        import ziglang  # noqa: F401
        out.append([sys.executable, "-m", "ziglang", "c++"])
    except Exception:
        pass
    for name in ("cl", "clang++", "g++"):
        path = shutil.which(name)
        if path:
            out.append([path])
    return out


def _pe_machine(path: Path) -> int | None:
    """读 PE 头里的机器码：0x8664 = x64、0x14c = x86；不是 PE 返回 None。

    为什么要它：CI 的 PATH 上可能有**别的架构/别的目标**的编译器（例如 MSYS2 那套），
    编出来的 DLL 是 32 位 → 本测试（x64 Python 加载）会失败，但那**不是 SDK 的问题**。
    所以先探针、只认能产出 x64 的编译器。
    """
    try:
        with open(path, "rb") as f:
            head = f.read(0x400)
        if head[:2] != b"MZ" or len(head) < 0x40:
            return None
        off = int.from_bytes(head[0x3C:0x40], "little")
        with open(path, "rb") as f:
            f.seek(off)
            sig = f.read(6)
        if sig[:4] != b"PE\0\0" or len(sig) < 6:
            return None
        return int.from_bytes(sig[4:6], "little")
    except OSError:
        return None


_X64 = 0x8664


def _tail(r: subprocess.CompletedProcess, lines: int) -> str:
    """编译/运行输出的最后 N 行（诊断用：不猜、也不截断关键信息）"""
    out = ((r.stdout or "") + (r.stderr or "")).rstrip()
    if not out:
        return "（没有任何输出）"
    return "\n".join(out.splitlines()[-lines:])


class CppSdkStaticTest(unittest.TestCase):
    """不依赖编译器的那层（CI/别人的机器上也永远跑）"""

    def setUp(self):
        self.header = HEADER.read_text(encoding="utf-8")
        self.sdk_test = SDK_TESTS.read_text(encoding="utf-8")
        self.example = EXAMPLE.read_text(encoding="utf-8")

    def test_files_exist(self):
        for p in (HEADER, EXAMPLE, SDK_TESTS, SDK_DIR / "README.md",
                  SDK_DIR / "CMakeLists.txt"):
            self.assertTrue(p.is_file(), f"缺 {p.relative_to(ROOT)}")

    def test_generates_all_abi_exports(self):
        """SDK 的注册宏必须生成全部 6 个必需导出（+可选 reload），名字与 ABI 头逐字一致"""
        for name in REQUIRED_EXPORTS + OPTIONAL_EXPORTS:
            self.assertIn(name, self.header, f"SDK 没生成 {name}")
        # 与 nap_plugin.h 里的 NAP_EXPORT 清单对齐
        import re
        declared = set(re.findall(r"NAP_EXPORT\s+[\w\s\*]+?\s+(nap_plugin_\w+)\s*\(", 
                                  ABI_HEADER.read_text(encoding="utf-8")))
        self.assertTrue(declared)
        for name in sorted(declared):
            self.assertIn(name, self.header, f"ABI 头里有 {name}，SDK 却没生成 —— 会漏导出")
        self.assertIn('extern "C"', self.header, "导出必须是 extern \"C\"（否则 C++ 会改名）")

    def test_no_platform_specific_headers(self):
        """SDK 只许用标准库（否则换个编译器/平台就编不过）"""
        for bad in ("windows.h", "Windows.h", "afx", "atlbase"):
            self.assertNotIn(bad, self.header, f"SDK 不该依赖 {bad}")

    def test_stays_cpp11(self):
        """保持 C++11：老编译器（VC6 之后的 MSVC / 老 GCC）也得能编"""
        for bad, why in ((("string_view"), "C++17"), (("if constexpr"), "C++17"),
                         (("std::optional"), "C++17"), (("make_unique"), "C++14"),
                         (("auto "), "C++11 的 auto 能用但这里统一避免")):
            self.assertNotIn(bad, self.header, f"SDK 里不该出现 {bad}（{why}）")

    def test_documents_both_event_shapes(self):
        """⚠ 原生插件收到的是 raw/segments，老插件契约是 raw_message/message —— 两种都要认"""
        self.assertIn('get("raw_message")', self.header)
        self.assertIn('get("raw")', self.header)
        self.assertIn('get("segments")', self.header)
        self.assertIn('get("message")', self.header)
        self.assertIn("两种事件形状都认", self.header)

    def test_example_uses_macro_and_utf8(self):
        self.assertIn("NAP_PLUGIN(", self.example, "示例要用注册宏")
        self.assertIn("nap::Plugin", self.example)
        self.assertIn("/utf-8", (SDK_DIR / "README.md").read_text(encoding="utf-8"),
                      "README 必须写明 MSVC 要加 /utf-8（中文字面量编码）")

    def test_sdk_selftest_covers_key_paths(self):
        for need, why in (("raw_message()", "事件取值"),
                          ("at_me()", "@我"),
                          ("NAP_PLUGIN 宏生成的导出", "宏生成的导出"),
                          ("JSON 错误输入要报错而不是崩", "畸形输入"),
                          ("动作/日志（走宿回调表）", "动作/日志")):
            self.assertIn(need, self.sdk_test, f"SDK 自测缺 {why}")


class CppSdkBuildTest(unittest.TestCase):
    """真编译 + 真加载（有编译器才跑）"""

    @classmethod
    def setUpClass(cls):
        if sys.platform != "win32":
            raise unittest.SkipTest("原生插件 ABI 是 Windows 专用（本测试要真加载 DLL）")
        # ignore_cleanup_errors：Windows 上 DLL 一旦被 ctypes 加载就一直锁着文件，
        # 临时目录删不掉（不是测试失败）。Python 3.10 以下没有这个参数，退化处理。
        try:
            cls.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        except TypeError:
            cls.tmp = tempfile.TemporaryDirectory()
        cls.dir = Path(cls.tmp.name)

        # 候选编译器要先用**探针**验一遍：能编出 x64 才算数。
        # 不然 CI 上 PATH 里那个（可能是 MSYS2 的 32 位/异目标）gcc 会让这条测试假红 ✗
        tried: list[str] = []
        cls.cc = None
        for cand in _compiler_candidates():
            ok, why = cls._probe_x64(cand)
            tried.append(f"{' '.join(cand)} → {why}")
            if ok:
                cls.cc = cand
                break
        if cls.cc is None:
            raise unittest.SkipTest(
                "没找到能产出 x64 的 C++ 编译器（这一层是「真编译」验证，环境不具备就跳）\n  试过："
                + "\n        ".join(tried or ["（PATH 里连 cl/clang++/g++ 都没有）"])
                + "\n  想在本机跑：python -m pip install ziglang（或在 ASTERCORE_CXX 里指定编译器）")
        # 1) SDK 自测 exe
        cls.test_exe = cls.dir / "nap_sdk_tests.exe"
        cls._build(cls.test_exe, SDK_TESTS)
        # 2) 示例插件 DLL
        cls.dll = cls.dir / "nap_hello_cpp.dll"
        cls._build(cls.dll, EXAMPLE)

    @classmethod
    def _probe_x64(cls, cc: list[str]) -> tuple[bool, str]:
        """用一行源码试编一个 exe，验证这个编译器**确实能产出 x64**"""
        # 先排掉"不是原生 Windows 目标"的编译器：MSYS2 的 usr/bin/g++（msys 目标）编出来的
        # DLL 依赖 msys-2.0.dll、Cygwin 那套依赖 cygwin1.dll —— 都能编、都能是 PE64，
        # 但**不是**宿主能当原生插件加载的东西。ABI 要的是原生 Windows x64。
        joined = " ".join(cc)
        if "ziglang" not in joined and os.path.basename(cc[-1]).lower() not in ("cl", "cl.exe"):
            try:
                d = subprocess.run(cc + ["-dumpmachine"], capture_output=True, text=True,
                                   errors="replace", timeout=30)
                triple = (d.stdout or "").strip()
                if any(t in triple.lower() for t in ("msys", "cygwin")):
                    return False, f"目标三元组是 {triple}（不是原生 Windows，编出来的 DLL 宿主加载不了）"
            except (OSError, subprocess.SubprocessError):
                pass
        src = cls.dir / "probe.cpp"
        src.write_text("int main() { return 0; }\n", encoding="utf-8")
        out = cls.dir / "probe.exe"
        r = cls._compile(cc, out, src, shared=False)
        if r.returncode != 0:
            first = _tail(r, 1)
            return False, f"编不过（{first}）"
        machine = _pe_machine(out)
        if machine != _X64:
            return False, f"产出不是 x64（PE 机器码 {hex(machine) if machine else '未知'}）"
        return True, "能产出 x64 ✓"

    @classmethod
    def _compile(cls, cc: list[str], out: Path, src: Path, *, shared: bool) -> subprocess.CompletedProcess:
        joined = " ".join(cc)
        is_msvc = os.path.basename(cc[-1]).lower() in ("cl", "cl.exe")
        cmd = list(cc)
        if "ziglang" in joined:
            cmd += ["-target", "x86_64-windows-gnu", "-Wno-nullability-completeness"]
        if is_msvc:
            obj = out.parent / "obj"
            obj.mkdir(exist_ok=True)
            cmd += ["/nologo", "/O2", "/MT", "/EHsc", "/std:c++14", "/utf-8",
                    f"/Fo{obj}\\", "/I", str(ROOT / "plugin-sdk"), "/I", str(SDK_DIR)]
            if shared:
                cmd += ["/LD"]
            cmd += [f"/Fe:{out}", str(src)]
        else:
            # ⚠ -m64：明确要 64 位产物（32 位 DLL 加载必失败，别让它"编成功却不能用"）
            # ⚠ -fexec-charset=UTF-8：中文字面量必须走 UTF-8，否则关键词匹配永远失败
            # ⚠ -static-libgcc/-static-libstdc++：DLL 自带运行库，CI 上不依赖 PATH 里的 libstdc++-6.dll
            cmd += ["-m64", "-std=c++11", "-O1", "-finput-charset=UTF-8", "-fexec-charset=UTF-8",
                    "-static-libgcc", "-static-libstdc++",
                    "-I", str(ROOT / "plugin-sdk"), "-I", str(SDK_DIR)]
            if shared:
                cmd += ["-shared"]
            cmd += ["-o", str(out), str(src)]
        return subprocess.run(cmd, capture_output=True, text=True, errors="replace")

    @classmethod
    def _build(cls, out: Path, src: Path) -> None:
        r = cls._compile(cls.cc, out, src, shared=(out.suffix == ".dll"))
        if r.returncode != 0:
            raise AssertionError(
                f"用编译好的编译器（{' '.join(cls.cc)}）编 {src.name} 失败 —— 这属于**真问题**：\n"
                + _tail(r, 40))
        machine = _pe_machine(out)
        if machine != _X64:
            raise AssertionError(
                f"{out.name} 不是 x64（PE 机器码 {hex(machine) if machine else '未知'}）"
                " —— 编译参数里少了 -m64 / x64 目标")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_sdk_selftest_passes(self):
        try:
            r = subprocess.run([str(self.test_exe)], capture_output=True, text=True, errors="replace")
        except OSError as e:                     # 起不来通常是环境缺运行库，不是 SDK 的问题
            self.skipTest(f"自测 exe 起不来（环境问题）：{e}")
        self.assertEqual(r.returncode, 0, (r.stdout or "") + (r.stderr or ""))
        self.assertIn("全部通过", r.stdout, r.stdout)
        self.assertIn("失败 0", r.stdout, r.stdout)

    def test_example_dll_exports_ok(self):
        r = subprocess.run([sys.executable, str(CHECKER), "--dll", str(self.dll)],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("全部必需导出齐全", r.stdout)
        self.assertIn("ABI 版本：1", r.stdout)
        self.assertIn("nap_hello_cpp", r.stdout)

    def test_loaded_by_real_host(self):
        """最强的一环：真实宿主加载 → 派发事件 → 插件按 UTF-8 关键词命中并请求动作"""
        from astercore.core.models import Event, seg_text
        from astercore.native import NativePlugin

        seen = []
        np = NativePlugin(self.dll, lambda req: (seen.append(req), {"ok": True})[1])
        try:
            self.assertEqual(np.init({}), 0)
            self.assertEqual(np.name(), "nap_hello_cpp")
            self.assertEqual(np.version(), "0.1.0")

            hit = Event(type="message", account_id="10001", platform="onebot", time=0,
                        user_id=1, group_id=98765, self_id="1", message_type="group",
                        raw="你好", segments=[seg_text("你好")])
            self.assertTrue(np.handle_event(hit), "「你好」应命中（中文关键词按 UTF-8 比对）")

            miss = Event(type="message", account_id="10001", platform="onebot", time=0,
                         user_id=1, group_id=98765, self_id="1", message_type="group",
                         raw="随便聊聊", segments=[seg_text("随便聊聊")])
            self.assertFalse(np.handle_event(miss), "没命中要放行（返回 0）")

            private = Event(type="message", account_id="10001", platform="onebot", time=0,
                            user_id=1, group_id=None, self_id="1", message_type="private",
                            raw="你好", segments=[seg_text("你好")])
            self.assertFalse(np.handle_event(private), "私聊不该管")
        finally:
            np.unload()

        self.assertEqual(len(seen), 1, f"只该发一条动作：{seen}")
        req = seen[0]
        self.assertEqual(req["action"], "send_group")
        self.assertEqual(req["group_id"], 98765)
        self.assertEqual(req["message"][0]["data"]["text"], "你好呀～")


if __name__ == "__main__":
    unittest.main()
