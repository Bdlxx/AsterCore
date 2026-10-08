# -*- coding: utf-8 -*-
"""PyInstaller runtime hook：让冻结版里的 pythonnet / pywebview 内嵌窗口能起来。

真机 v0.3.0 抓到（本机 Windows，未受限运行也复现）：

    WARNING astercore.shell: pywebview 启动失败，回退浏览器:
      Failed to resolve Python.Runtime.Loader.Initialize from
      <app>\\_internal\\pythonnet\\runtime\\Python.Runtime.dll

根因：Windows 上 pywebview 靠 pythonnet + clr_loader 的 **netfx** 路径，它要用原生
`ClrLoader.dll` 建 AppDomain 再按名字取 `Python.Runtime.Loader.Initialize` 函数指针
（见 clr_loader/netfx.py::_get_callable）。冻结后如果没把包内目录加进 DLL 搜索路径、
没告诉 pythonnet 包内的 pythonXY.dll 在哪，这一步就返回 NULL → 报上面那句。
（同一问题的社区做法：pywebview#1215、ouroboros 的 scripts/pyi_rth_pythonnet.py。）

这里做四件事，**必须在任何 `import clr` / `import webview` 之前执行**：
1. 强制 `PYTHONNET_RUNTIME=netfx`（Windows 上就是 .NET Framework 版 pythonnet）；
2. `PYTHONNET_PYDLL` 指向包内的 `pythonXY.dll`；
3. 把 `_MEIPASS`、`pythonnet\\runtime`、`webview\\lib` 加进 PATH 与 `os.add_dll_directory`；
4. 去掉 `:Zone.Identifier`（MOTW）—— 用户从浏览器下载 zip 解压后，.NET 会拒绝加载
   带 MOTW 的程序集，这是"别人机器上才复现"的经典坑。
"""

import os
import sys

_DLL_DIR_HANDLES: list = []


def _setup() -> None:
    if sys.platform != "win32":
        return

    base = getattr(sys, "_MEIPASS", None) or os.path.dirname(os.path.abspath(sys.executable))
    exe_dir = os.path.dirname(os.path.abspath(sys.executable))
    pydll_name = f"python{sys.version_info[0]}{sys.version_info[1]}.dll"

    os.environ["PYTHONNET_RUNTIME"] = "netfx"

    def unblock(path: str) -> None:
        try:
            os.remove(f"{path}:Zone.Identifier")
        except OSError:
            pass

    # pythonXY.dll：先找常规位置，再全树兜底
    candidates = [os.path.join(base, pydll_name), os.path.join(exe_dir, pydll_name)]
    for root, _dirs, files in os.walk(base):
        if pydll_name in files:
            candidates.append(os.path.join(root, pydll_name))
            break
    for path in candidates:
        if os.path.isfile(path):
            os.environ.setdefault("PYTHONNET_PYDLL", path)
            unblock(path)
            break

    runtime_dir = os.path.join(base, "pythonnet", "runtime")
    lib_dir = os.path.join(base, "webview", "lib")

    search_dirs = []
    for path in (base, exe_dir, runtime_dir, lib_dir):
        if os.path.isdir(path) and path not in search_dirs:
            search_dirs.append(path)

    for path in (runtime_dir, lib_dir):
        for root, _dirs, files in os.walk(path):
            for name in files:
                if os.path.splitext(name)[1].lower() in {".dll", ".exe", ".pyd"}:
                    unblock(os.path.join(root, name))

    os.environ["PATH"] = os.pathsep.join(
        search_dirs
        + [p for p in os.environ.get("PATH", "").split(os.pathsep) if p and p not in search_dirs]
    )
    if hasattr(os, "add_dll_directory"):
        for path in search_dirs:
            try:
                _DLL_DIR_HANDLES.append(os.add_dll_directory(path))
            except OSError:
                pass


_setup()
