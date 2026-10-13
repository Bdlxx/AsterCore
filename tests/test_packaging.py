# -*- coding: utf-8 -*-
"""打包配方的一致性守卫（Windows 发布最容易"静默半死"的地方）

背景：视频解析依赖链有 36 个包，漏一个 → 现场是"启动告警 + 发链接毫无反应"，
而不是构建失败。所以这里把三件事钉住：
  ① 启动自检要探的模块（`bootstrap.PARSER_DEPS`）必须都能由 pyproject 的 `parser` extra 提供
  ② spec 必须逐个 `collect_all`（yt_dlp 的 extractor 是动态 import、curl_cffi/msgspec/lxml
     带 .pyd，光靠 import 分析收不全）
  ③ spec 必须把 `tools/ffmpeg/bin` 作为 datas 收进去（`find_ffmpeg` 只认这个位置）
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 模块名 → 发行包名（PyPI 上名字不一样的那几个，写错就装错）
MODULE_TO_DIST = {
    "aiohttp": "aiohttp",
    "yt_dlp": "yt-dlp",
    "bilibili_api": "bilibili-api-python",   # ⚠ 不是 bilibili_api（那是另一个老包）
    "curl_cffi": "curl_cffi",
    "msgspec": "msgspec",
    "PIL": "pillow",
    "json5": "json5",
    "bs4": "beautifulsoup4",
    "httpx": "httpx",
    "aiofiles": "aiofiles",
    "apscheduler": "APScheduler",
}


def _dist_names() -> set[str]:
    import tomllib
    with (ROOT / "pyproject.toml").open("rb") as f:
        groups = tomllib.load(f)["project"]["optional-dependencies"]
    out = set()
    for spec in groups.get("parser", []):
        out.add(re.split(r"[=<>!\[;\s]", spec, 1)[0].strip().lower())
    return out


class ParserDependencyTest(unittest.TestCase):
    def setUp(self):
        try:
            import tomllib  # noqa: F401
        except ImportError:
            self.skipTest("需要 Python 3.11+ 的 tomllib")

    def test_pyproject_has_parser_extra(self):
        names = _dist_names()
        self.assertTrue(names, "pyproject 缺少 parser 可选依赖组")
        # 版本锚定与 Linux 线上一致的几个关键包，必须 pin 住
        for must in ("yt-dlp==2026.8.19", "bilibili-api-python==17.4.2",
                     "curl_cffi==0.16.2", "msgspec==0.21.1"):
            self.assertIn(must.lower(), _raw_dist_specs(), f"parser 组里应显式锁 {must}")

    def test_every_probed_module_is_declared(self):
        """启动自检探的每个模块，都要能在 parser 组里找到对应发行包"""
        from astercore.bootstrap import PARSER_DEPS
        declared = _dist_names()
        for mod in PARSER_DEPS:
            dist = MODULE_TO_DIST.get(mod)
            self.assertIsNotNone(dist, f"PARSER_DEPS 多了个没登记发行包名的模块：{mod}")
            self.assertIn(dist.lower(), declared,
                          f"自检要探 {mod}（发行包 {dist}），但 parser 组没声明它 → "
                          "构建出来会缺依赖、现场只留一条告警")

    def test_spec_collects_parser_chain_and_ffmpeg(self):
        spec = (ROOT / "pack" / "astercore.spec").read_text(encoding="utf-8")
        for pkg in ("yt_dlp", "bilibili_api", "curl_cffi", "msgspec", "lxml",
                    "gallery_dl", "qrcode_terminal", "bs4", "aiohttp"):
            self.assertIn(f"'{pkg}'", spec, f"spec 没 collect_all({pkg})")
        self.assertIn("collect_all", spec)
        self.assertIn("tools/ffmpeg/bin", spec,
                      "spec 没有把 tools/ffmpeg/bin 收进包（find_ffmpeg 只认这个位置）")

    def test_ci_installs_parser_extra_and_fetches_ffmpeg(self):
        wf = (ROOT / ".github" / "workflows" / "windows-build.yml").read_text(encoding="utf-8")
        self.assertIn("parser", wf, "CI 没装 parser extra")
        self.assertIn("tools/fetch_ffmpeg.py", wf, "CI 没取 ffmpeg.exe")
        self.assertIn("PyInstaller", wf)

    def test_spec_collects_pythonnet_and_registers_runtime_hook(self):
        """内嵌窗口（pythonnet/pywebview）必须收全 + 挂 runtime hook。

        真机 v0.3.0 教训：pythonnet 只在 pywebview 真建窗口时才 import，藏得深。
        没收全 / 没挂 hook 时不会构建失败，而是运行时
        "Failed to resolve Python.Runtime.Loader.Initialize" → **静默回退浏览器**，
        桌面版就没窗口了（现场只留一条 WARNING，极难发现）。
        """
        spec = (ROOT / "pack" / "astercore.spec").read_text(encoding="utf-8")
        for pkg in ("pythonnet", "clr_loader"):
            self.assertIn(f"'{pkg}'", spec, f"spec 没 collect_all({pkg})")
        self.assertIn("rth_pythonnet.py", spec, "spec 没挂 pack/rth_pythonnet.py 这个 runtime hook")

        hook = (ROOT / "pack" / "rth_pythonnet.py").read_text(encoding="utf-8")
        self.assertIn("PYTHONNET_RUNTIME", hook, "hook 必须强制 netfx 运行时")
        self.assertIn("PYTHONNET_PYDLL", hook, "hook 必须把包内 pythonXY.dll 指给 pythonnet")
        self.assertIn("add_dll_directory", hook, "hook 必须把包内目录加进 DLL 搜索路径")
        self.assertIn("Zone.Identifier", hook, "hook 要清 MOTW（浏览器下载的 zip 解压后会带）")

    def test_ships_exe_config_with_load_from_remote_sources(self):
        """`astercore.exe.config` 必须随包放在 exe 同级，且开着 loadFromRemoteSources。

        真机 v0.3.0 逐层排查的结论：内嵌窗口起不来（静默回退浏览器）的真根因是
        .NET 把包内程序集判为"网络位置"→ Assembly.LoadFrom 抛 0x80131515 →
        clr_loader 的原生 shim 把异常吞掉，只留一句 "Failed to resolve
        Python.Runtime.Loader.Initialize"。开这个开关就正常了。
        PyInstaller 的 datas 只会进 _internal，而 .NET 只认 <exe 名>.config，所以必须由
        CI 单独拷到 dist/astercore/ —— 这条测试就是把"两边都别漏"钉住。
        """
        cfg = ROOT / "pack" / "astercore.exe.config"
        self.assertTrue(cfg.is_file(), "缺 pack/astercore.exe.config")
        text = cfg.read_text(encoding="utf-8")
        self.assertIn("loadFromRemoteSources", text, "config 里必须开 loadFromRemoteSources")
        self.assertRegex(text, r'loadFromRemoteSources\s+enabled="true"',
                         "loadFromRemoteSources 必须 enabled=true")

        wf = (ROOT / ".github" / "workflows" / "windows-build.yml").read_text(encoding="utf-8")
        self.assertIn("pack/astercore.exe.config", wf, "CI 没把 config 拷进包")
        self.assertIn("dist/astercore/astercore.exe.config", wf,
                      "config 必须拷到 dist/astercore/（exe 同级），否则 .NET 不认")

    def test_version_info_matches_and_spec_is_windowed(self):
        """exe 版本资源要与 __version__ 一致；spec 必须是 **windowed**。

        用户明确要求"启动时不应唤起 cmd"：console=True 会弹黑色控制台窗口。
        代价是 stdout/stderr 为 None → 入口必须最早期调用
        `bootstrap.ensure_stdio_for_windowed()`，否则第一条 print 就崩。
        """
        from astercore import __version__

        vi = (ROOT / "pack" / "version_info.txt").read_text(encoding="utf-8")
        self.assertIn(f'"{__version__}"', vi,
                      "version_info.txt 的 ProductVersion 与 astercore.__version__ 不一致")
        self.assertIn("CompanyName", vi, "要写发布者信息（少报『未知发布者』）")

        spec = (ROOT / "pack" / "astercore.spec").read_text(encoding="utf-8")
        self.assertIn("console=False", spec, "必须是 windowed：不该弹控制台")
        self.assertIn("version_info.txt", spec, "exe 要带版本资源")

        main = (ROOT / "src" / "astercore" / "__main__.py").read_text(encoding="utf-8")
        self.assertIn("ensure_stdio_for_windowed", main,
                      "windowed 下 stdout 兜底必须在入口最早期调用")

    def test_runtime_hook_is_valid_python(self):
        """hook 在 exe 启动最早期执行，语法/名字错了就是"整个程序起不来"。"""
        import ast
        src = (ROOT / "pack" / "rth_pythonnet.py").read_text(encoding="utf-8")
        ast.parse(src)
        self.assertIn("_setup()", src, "hook 末尾必须真的调用 _setup()，否则等于没挂")


def _raw_dist_specs() -> set[str]:
    import tomllib
    with (ROOT / "pyproject.toml").open("rb") as f:
        groups = tomllib.load(f)["project"]["optional-dependencies"]
    return {s.lower() for s in groups.get("parser", [])}


if __name__ == "__main__":
    unittest.main()


class AppIconTest(unittest.TestCase):
    """应用图标（栖星）：exe 图标、随包、托盘可用、面板 favicon —— 缺哪个都算回归"""

    def setUp(self):
        self.root = Path(__file__).resolve().parent.parent

    def test_ico_shipped(self):
        ico = self.root / "pack" / "astercore.ico"
        self.assertTrue(ico.is_file(), "pack/astercore.ico 必须存在（exe 图标用）")
        self.assertGreater(ico.stat().st_size, 10_000)
        self.assertEqual(ico.read_bytes()[:4], b"\x00\x00\x01\x00", "必须是合法 .ico（有 ICONDIR 头）")

    def test_spec_is_valid_python(self):
        """.spec 就是 Python 脚本：插 icon= 时我就制造过 positional-after-keyword 语法错误"""
        import ast
        spec = (self.root / "pack" / "astercore.spec").read_text(encoding="utf-8")
        ast.parse(spec)          # 语法不过直接失败

    def test_spec_sets_icon_and_bundles_it(self):
        spec = (self.root / "pack" / "astercore.spec").read_text(encoding="utf-8")
        self.assertIn("_ICON", spec)
        self.assertIn("icon=_ICON", spec, "EXE 要带图标")
        self.assertIn("datas.append((_ICON", spec, "图标也要随包（托盘运行时读）")

    def test_tray_uses_bundled_icon_with_fallback(self):
        sh = (self.root / "src" / "astercore" / "shell.py").read_text(encoding="utf-8")
        self.assertIn("astercore.ico", sh, "托盘要优先用随包图标")
        self.assertIn("_MEIPASS", sh)
        self.assertIn("改用内置画法", sh, "拿不到图标必须退回内置画法，不能让托盘罢工")

    def test_panel_has_favicon(self):
        html = (self.root / "src" / "astercore" / "web" / "static" / "index.html").read_text(encoding="utf-8")
        self.assertIn('rel="icon"', html)
        self.assertTrue((self.root / "src" / "astercore" / "web" / "static" / "favicon.ico").is_file())

