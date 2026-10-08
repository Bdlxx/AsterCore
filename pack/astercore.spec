# -*- mode: python ; coding: utf-8 -*-
import os
import sys

from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_submodules

BASE = os.path.abspath(os.path.join(os.path.dirname(SPEC), '..'))

hiddenimports = []
binaries = []
datas = []

hiddenimports += collect_submodules('astercore')

# Windows 上 ZoneInfo("Asia/Shanghai") 依赖 tzdata 数据文件（老插件要用）
try:
    datas += collect_data_files('tzdata')
except Exception:
    pass

# 桌面壳可选依赖（装了才收集：pywebview 内嵌窗口 / pystray 托盘 / PIL 图标）
for _opt in ('webview', 'pystray', 'PIL', 'waitress'):
    try:
        hiddenimports += collect_submodules(_opt)
    except Exception:
        pass

# pythonnet + clr_loader：**内嵌窗口的关键**。pywebview 的 winforms 后端走 pythonnet，
# 而 pythonnet 的 netfx 路径要用 clr_loader 的原生 ClrLoader.dll 建 AppDomain。
# 这两包只在 pywebview 真正建窗口时才 import，隐藏得深 → 必须 collect_all：
# 它会把 pythonnet/runtime/*.dll（含 Python.Runtime.dll）与 clr_loader 的原生库都收进来。
# （真机 v0.3.0：没收全时 pywebview 报 "Failed to resolve Python.Runtime.Loader.Initialize"
#   然后静默回退浏览器 —— 桌面版就没窗口了。配套 pack/rth_pythonnet.py。）
for _pkg in ('pythonnet', 'clr_loader'):
    try:
        _b, _d, _h = collect_all(_pkg)
        binaries += _b
        datas += _d
        hiddenimports += _h
    except Exception:
        pass

# ---- 视频解析插件（video_parser_core）依赖链 ----
# 这些包光靠 import 分析收不全：yt_dlp 的 extractor 是动态 import；curl_cffi /
# msgspec / lxml / PIL 带 .pyd 二进制；bilibili_api 子模块多；gallery_dl 要能
# 作为 `python -m gallery_dl` 跑；qrcode_terminal 是 bilibili_api 的依赖。
# 整组依赖见 pyproject 的 `parser` extra（36 个包，全有 Windows 轮子、零编译）。
# 装了才收集、缺了不阻塞构建 —— 这样 Linux 上的测试构建也不会因为缺包而挂。
for _pkg in ('yt_dlp', 'bilibili_api', 'curl_cffi', 'msgspec', 'lxml', 'bs4', 'json5',
             'aiohttp', 'httpx', 'aiofiles', 'apscheduler', 'gallery_dl', 'tqdm',
             'yarl', 'multidict', 'soupsieve', 'PIL', 'qrcode_terminal'):
    try:
        _b, _d, _h = collect_all(_pkg)
        binaries += _b
        datas += _d
        hiddenimports += _h
    except Exception:
        pass

# Web 面板静态资源（index.html）必须打进包，否则 Flask 404；
# plugin_templates 是首启播种用的示例插件源码（必须解压可见，故按 datas 分发）
datas += [(os.path.join(BASE, 'src', 'astercore', 'web', 'static'),
           'astercore/web/static'),
          (os.path.join(BASE, 'src', 'astercore', 'plugin_templates'),
           'astercore/plugin_templates')]

# Windows 随包带 ffmpeg.exe：`bootstrap.find_ffmpeg()` 会认
# <exe 同级的 tools/ffmpeg/bin/ffmpeg.exe> 并把它插到 PATH 最前面，插件用裸命令名调用。
# 二进制**不进 git**：构建前先跑 `python tools/fetch_ffmpeg.py`（CI 里就是这么做的）。
_ff = os.path.join(BASE, 'tools', 'ffmpeg', 'bin')
if sys.platform == 'win32' and os.path.isdir(_ff):
    datas.append((_ff, 'tools/ffmpeg/bin'))

a = Analysis(
    [os.path.join(BASE, 'src/astercore/__main__.py')],
    pathex=[os.path.join(BASE, 'src')],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[os.path.join(SPECPATH, 'rth_pythonnet.py')],
    excludes=['test'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='astercore',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='astercore',
)
