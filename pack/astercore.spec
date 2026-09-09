# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_submodules

hiddenimports = []
hiddenimports += collect_submodules('astercore')
# 桌面壳可选依赖（装了才收集：pywebview 内嵌窗口 / pystray 托盘 / PIL 图标）
for _opt in ('webview', 'pystray', 'PIL'):
    try:
        hiddenimports += collect_submodules(_opt)
    except Exception:
        pass


import os
BASE = os.path.abspath(os.path.join(os.path.dirname(SPEC), '..'))

a = Analysis(
    [os.path.join(BASE, 'src/astercore/__main__.py')],
    pathex=[os.path.join(BASE, 'src')],
        binaries=[],
    # Web 面板静态资源（index.html）必须打进包，否则 Flask 404
    datas=[(os.path.join(BASE, 'src', 'astercore', 'web', 'static'),
            'astercore/web/static')],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
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
