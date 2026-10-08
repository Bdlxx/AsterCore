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


def _raw_dist_specs() -> set[str]:
    import tomllib
    with (ROOT / "pyproject.toml").open("rb") as f:
        groups = tomllib.load(f)["project"]["optional-dependencies"]
    return {s.lower() for s in groups.get("parser", [])}


if __name__ == "__main__":
    unittest.main()
