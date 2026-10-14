# -*- coding: utf-8 -*-
"""校验一个易语言/原生插件 DLL 的导出是否符合 AsterCore 的 ABI。

用法：
    python check_dll_exports.py --dll 你的插件.dll

输出：6 个必需导出是否都在（含"被修饰的名字"探测）+ abi_version / name / version 的实际值。
退出码：0=必需导出齐全；1=有缺失或读不出来。

为什么单独做这个工具：易语言的"公开子程序"导出名可能带修饰（`_name` / `_name@N`），
用户看到的报错往往只是"属性不存在"。这里直接把**实际找到了哪个名字**打出来。
"""
from __future__ import annotations

import argparse
import ctypes
import sys
from pathlib import Path

REQUIRED = ("nap_plugin_abi_version", "nap_plugin_name", "nap_plugin_version",
            "nap_plugin_init", "nap_plugin_on_event", "nap_plugin_unload")
OPTIONAL = ("nap_plugin_reload",)

try:                                     # 仓库内运行时复用宿主那份（保持同源）
    _ROOT = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(_ROOT / "src"))
    from astercore.native import symbol_candidates as _host_candidates  # type: ignore

    def candidates(name: str) -> list[str]:
        return _host_candidates(name)
except Exception:                        # noqa: BLE001 单独拷出去也能用（候选名必须与宿主一致）
    def candidates(name: str) -> list[str]:
        return [name, f"_{name}", f"{name}@4", f"_{name}@4"]


def found(lib, name: str) -> str:
    """返回实际命中的导出名；没找到返回空串"""
    for cand in candidates(name):
        try:
            getattr(lib, cand)
            return cand
        except AttributeError:
            continue
    return ""


def describe(lib) -> dict:
    """扫描导出并尽量读出 name/version/abi_version（读不到就留空）"""
    out: dict = {"exports": {}, "missing": [], "values": {}}
    for name in REQUIRED + OPTIONAL:
        hit = found(lib, name)
        out["exports"][name] = hit
        if not hit and name in REQUIRED:
            out["missing"].append(name)
    try:
        fn = getattr(lib, out["exports"]["nap_plugin_abi_version"] or "nap_plugin_abi_version")
        fn.restype = ctypes.c_int
        out["values"]["abi_version"] = int(fn())
    except Exception as e:                  # noqa: BLE001 坏的 DLL 读不出值很正常
        out["values"]["abi_version"] = f"<读失败: {type(e).__name__}>"
    for key, api_name in (("name", "nap_plugin_name"), ("version", "nap_plugin_version")):
        hit = out["exports"].get(api_name) or ""
        if not hit:
            continue
        try:
            fn = getattr(lib, hit)
            fn.restype = ctypes.c_char_p
            out["values"][key] = (fn() or b"").decode("utf-8", "replace")
        except Exception as e:              # noqa: BLE001
            out["values"][key] = f"<读失败: {type(e).__name__}>"
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="校验原生插件 DLL 的 ABI 导出")
    ap.add_argument("--dll", required=True, help="插件 DLL 路径")
    args = ap.parse_args(argv)
    p = Path(args.dll)
    if not p.is_file():
        print(f"找不到文件：{p}")
        return 1
    try:
        lib = ctypes.CDLL(str(p))
    except OSError as e:
        print(f"加载失败：{e}")
        print("提示：确认是 **x64** DLL（AsterCore 是 64 位进程），且依赖的支持库在同目录")
        return 1

    info = describe(lib)
    print(f"文件：{p}")
    print("导出检查：")
    for name in REQUIRED:
        hit = info["exports"].get(name) or ""
        mark = "✅" if hit else "❌"
        extra = f"（实际导出名：{hit}）" if hit and hit != name else ""
        need = "" if name != "nap_plugin_reload" else "（可选）"
        print(f"  {mark} {name}{need} {extra}")
    for name in OPTIONAL:
        hit = info["exports"].get(name) or ""
        print(f"  {'✅' if hit else '⚪'} {name}（可选）" + (f"（实际导出名：{hit}）" if hit and hit != name else ""))
    print(f"  ABI 版本：{info['values'].get('abi_version')}（必须 = 1）")
    print(f"  名字    ：{info['values'].get('name', '<未导出>')}")
    print(f"  版本    ：{info['values'].get('version', '<未导出>')}")
    if info["missing"]:
        print("\n缺少必需导出：" + " / ".join(info["missing"]))
        print("易语言提示：把对应子程序设为「公开」，并确认编译目标为 x64；"
              "导出名带修饰（_name@N）宿主也能找到，但**调用约定必须是 cdecl**。")
        return 1
    print("\n全部必需导出齐全 ✅（真机再放进插件目录跑一次，看面板日志里的『已登记原生插件』）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
