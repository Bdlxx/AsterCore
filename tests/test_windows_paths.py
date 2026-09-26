# -*- coding: utf-8 -*-
# Windows 移植 · 老插件「NapCat 缓存目录」路径契约
#
# 背景（实测结论，不是推测）：老插件把 Linux + Docker 时代的**绝对路径**写死在代码里
# （/root/napcat/cache/images、/root/napcat2/cache/images）。Windows 上这类以 "/"
# 开头的路径会被当作「当前盘根目录」，于是：
#     os.makedirs("/root/napcat/cache/images")  →  真的建出 C:\root\napcat\cache\images
#     open(..., "w")                            →  也真的写得进去
# 也就是说**不会报错**，只是文件落在了 NapCat 根本不会去读的地方；而发送时用的
# /app/cache/images/… 会被兼容层按前缀映射到 %USERPROFILE%\napcat\cache\images\…
# 两边不是同一个目录 →「解析成功、下载成功，就是发不出去」。
#
# 本测试锁定三件事：
#   1) Linux（两个线上实例）上的解析结果必须与改动前**完全一致**（零回归）
#   2) Windows 上必须落在 NapCat 真正会读的目录（默认 ~/napcat/cache/images）
#   3) Windows 上「写入目录」与「发给 NapCat 的路径」必须收敛到同一个文件
#      （jm 的 PDF 走 NapCat HTTP 直发，不经过兼容层出口映射，尤其不能只对一半）
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from astercore.compat.utils_pkg import install as compat_install

INSTANCE_SRC = Path("/root/mybot2/instances/740979632")
PLUGINS_SRC = INSTANCE_SRC / "plugins"
NEEDED = ("parser_bridge.py", "marry.py", "jm_downloader.py")

# 改动前 parser_bridge/marry 里写死的老规则（用于"Linux 零回归"对照）
LEGACY_ROOT = {"740979632": "/root/napcat/cache/images",
               "2551736206": "/root/napcat2/cache/images"}


class _FakeRT:
    """兼容层只用到 instance_dir / account_id / backend / display_name"""

    def __init__(self, instance_dir, account_id=740979632):
        self.instance_dir = Path(instance_dir)
        self.account_id = account_id
        self.backend = None
        self.display_name = "测试星"


@unittest.skipUnless(all((PLUGINS_SRC / f).exists() for f in NEEDED)
                     and (PLUGINS_SRC / "video_parser_core").is_dir(),
                     "未找到线上插件（视频解析全家桶）")
class NapCatCachePathTest(unittest.TestCase):
    """三个插件各自解析「NapCat 宿主机缓存目录」的方式必须一致且跨平台正确"""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        base = Path(cls.tmp.name)
        cls.instance = base / "accounts" / "740979632"
        (cls.instance / "plugins").mkdir(parents=True)
        (cls.instance / "data").mkdir(parents=True)
        for f in NEEDED:
            shutil.copy2(PLUGINS_SRC / f, cls.instance / "plugins" / f)
        shutil.copytree(PLUGINS_SRC / "video_parser_core",
                        cls.instance / "plugins" / "video_parser_core",
                        ignore=shutil.ignore_patterns("__pycache__"))
        (cls.instance / "data" / "marry_config.json").write_text(
            json.dumps({"enabled": False}, ensure_ascii=False), encoding="utf-8")
        # 关掉自动更新线程：导入 jm_downloader 时不要真去连网
        # （cfg() 优先读 settings 段，所以必须写在 settings 里）
        (cls.instance / "data" / "jm_downloader_config.json").write_text(
            json.dumps({"settings": {"auto_update": False}},
                       ensure_ascii=False), encoding="utf-8")
        cls._write_cfg({"BOT_NAME": "依星", "BOT_QQ": 740979632,
                        "MASTER_QQ": [2840771765],
                        "NAPCAT_HTTP": "http://127.0.0.1:3000"})

        compat_install()
        from astercore.compat import context as cctx
        cls.rt = _FakeRT(cls.instance)
        cls._token = cctx.set_current(cls.rt)

        cls._path_added = str(cls.instance / "plugins")
        sys.path.insert(0, cls._path_added)
        import importlib
        cls.pb = importlib.import_module("parser_bridge")
        cls.marry = importlib.import_module("marry")
        cls.jm = importlib.import_module("jm_downloader")
        cls.vpc_config = importlib.import_module("video_parser_core.config")

    @classmethod
    def tearDownClass(cls):
        from astercore.compat import context as cctx
        cctx.reset_current(cls._token)
        try:
            sys.path.remove(cls._path_added)
        except ValueError:
            pass
        for name in ("parser_bridge", "marry", "jm_downloader",
                     "video_parser_core", "video_parser_core.config",
                     "video_parser_core.main", "video_parser_core.sender"):
            sys.modules.pop(name, None)
        cls.tmp.cleanup()

    # ---------- 工具 ----------
    @classmethod
    def _write_cfg(cls, cfg: dict):
        (cls.instance / "config.json").write_text(
            json.dumps(cfg, ensure_ascii=False), encoding="utf-8")

    def setUp(self):
        self.marry._CACHE_DIR = None          # marry 会把结果缓存起来，逐例重置
        self.home = tempfile.TemporaryDirectory()
        self._home_env = mock.patch.dict(os.environ,
                                         {"HOME": self.home.name}, clear=False)
        self._home_env.start()

    def tearDown(self):
        self._home_env.stop()
        self.home.cleanup()
        self._write_cfg({"BOT_NAME": "依星", "BOT_QQ": 740979632,
                         "MASTER_QQ": [2840771765],
                         "NAPCAT_HTTP": "http://127.0.0.1:3000"})

    def _as_windows(self):
        """把三个插件的平台判定都改成 Windows（Linux 上模拟）"""
        return [mock.patch.object(m, "_is_windows", return_value=True)
                for m in (self.pb, self.marry, self.jm)]

    # ---------- 1) Linux 零回归 ----------
    def test_linux_results_identical_to_old_logic(self):
        """线上（Linux+Docker）解析结果必须与改动前逐字节相同"""
        for qq, root in LEGACY_ROOT.items():
            with self.subTest(bot_qq=qq):
                self._write_cfg({"BOT_QQ": int(qq)})
                self.marry._CACHE_DIR = None
                self.assertEqual(self.pb.get_cache_dir(),
                                 os.path.join(root, "parser_cache"))
                self.assertEqual(self.marry._get_cache_dir(), root)
                cands = self.jm._napcat_shared_candidates()
                self.assertEqual(cands[0], (root, "/app/cache/images"),
                                 "首选候选必须是该实例的线上目录")
                self.assertEqual(cands[-1],
                                 (os.path.expanduser("~/napcat/cache/images"),
                                  "/app/cache/images"))
        # 未知 QQ：与老逻辑一致，直接用 ~/napcat
        self._write_cfg({"BOT_QQ": 123456})
        self.marry._CACHE_DIR = None
        self.assertEqual(self.pb.get_cache_dir(),
                         os.path.join(os.path.expanduser("~"),
                                      "napcat", "cache", "images", "parser_cache"))
        self.assertEqual(self.marry._get_cache_dir(),
                         os.path.expanduser("~/napcat/cache/images"))

    # ---------- 2) Windows：必须落到 NapCat 真正读的目录 ----------
    def test_windows_never_uses_linux_absolute_paths(self):
        """BOT_QQ 就是线上那两个号时，Windows 上也不能用 /root/napcat/…"""
        local = os.path.join(self.home.name, "napcat", "cache", "images")
        for qq in ("740979632", "2551736206"):
            with self.subTest(bot_qq=qq):
                self._write_cfg({"BOT_QQ": int(qq)})
                self.marry._CACHE_DIR = None
                patches = self._as_windows()
                for p in patches:
                    p.start()
                try:
                    pb_dir = self.pb.get_cache_dir()
                    marry_dir = self.marry._get_cache_dir()
                    cands = self.jm._napcat_shared_candidates()
                finally:
                    for p in patches:
                        p.stop()
                self.assertEqual(pb_dir, os.path.join(local, "parser_cache"))
                self.assertEqual(marry_dir, local)
                self.assertEqual(cands, [(local, local)],
                                 "Windows：写入目录与发送路径必须是同一个本机目录")
                for v in (pb_dir, marry_dir) + tuple(cands[0]):
                    self.assertNotIn("/root/napcat", v.replace("\\", "/"),
                                     f"Windows 上不得再出现 Linux 容器路径: {v}")

    def test_windows_send_path_uses_native_separators(self):
        """发给 NapCat 的路径：Windows 用 os.path.join，Linux 固定用 '/'"""
        local = r"C:\Users\x\napcat\cache\images"
        with mock.patch.object(self.jm, "_is_windows", return_value=True):
            self.assertEqual(self.jm._send_path(local, "jm_pdf", "1.pdf"),
                             os.path.join(local, "jm_pdf", "1.pdf"))
        with mock.patch.object(self.jm, "_is_windows", return_value=False):
            self.assertEqual(self.jm._send_path("/app/cache/images", "jm_pdf", "1.pdf"),
                             "/app/cache/images/jm_pdf/1.pdf")

    # ---------- 3) 显式配置优先（双端一致，与兼容层同一个键）----------
    def test_explicit_config_wins_on_both_platforms(self):
        custom = str(Path(self.home.name) / "custom_cache")
        self._write_cfg({"BOT_QQ": 740979632, "NAPCAT_CACHE_HOST": custom})
        self.marry._CACHE_DIR = None
        self.assertEqual(self.pb.get_cache_dir(),
                         os.path.join(custom, "parser_cache"))
        self.assertEqual(self.marry._get_cache_dir(), custom)
        self.assertEqual(self.jm._napcat_shared_candidates()[0],
                         (custom, "/app/cache/images"))

        self.marry._CACHE_DIR = None
        patches = self._as_windows()
        for p in patches:
            p.start()
        try:
            self.assertEqual(self.pb.get_cache_dir(),
                             os.path.join(custom, "parser_cache"))
            self.marry._CACHE_DIR = None
            self.assertEqual(self.marry._get_cache_dir(), custom)
            self.assertEqual(self.jm._napcat_shared_candidates(), [(custom, custom)])
        finally:
            for p in patches:
                p.stop()

    # ---------- 4) 收敛性：写进去的文件 = 兼容层发出去的路径 ----------
    def test_written_file_is_exactly_what_compat_layer_sends(self):
        """Windows 上：解析核心写盘的位置，必须正是兼容层映射后要读的位置。

        这是本次修复的核心断言——只修好写盘目录、或只修好发送路径，都不算修好。
        """
        from astercore.compat import paths as cpaths

        patches = self._as_windows()
        for p in patches:
            p.start()
        try:
            with mock.patch.object(cpaths, "is_windows", return_value=True):
                written = self.pb.get_cache_dir()          # 真实写盘目录（已建好）
                probe = os.path.join(written, "_probe.mp4")
                Path(probe).write_bytes(b"x")
                # 插件发给 NapCat 的是容器路径；兼容层出口会做映射
                sent = cpaths.map_segments(
                    [{"type": "video",
                      "data": {"file": "/app/cache/images/parser_cache/_probe.mp4"}}],
                    self.rt)
        finally:
            for p in patches:
                p.stop()

        mapped = sent[0]["data"]["file"]
        # 注意：这里在 Linux 上模拟 Windows，兼容层按 Windows 规则拼的是反斜杠，
        # 而实际写盘目录是 POSIX 路径 —— 比较前把分隔符统一（真机 Windows 上一致）
        def _norm(p):
            return os.path.normcase(os.path.normpath(p.replace("\\", "/")))
        self.assertEqual(_norm(mapped), _norm(probe),
                         f"映射后的路径 {mapped} 与实际写盘位置 {probe} 不一致")
        self.assertTrue(os.path.exists(mapped.replace("\\", os.sep)),
                        "映射后的路径必须指向真实存在的文件")

    # ---------- 5) 解析核心的共享 cookie 池 ----------
    def test_cookie_root_defaults_to_repo_root(self):
        """未设 ASTERCORE_HOME 时按原有 5 层 dirname 推算（Linux 行为不变）"""
        old = os.environ.pop("ASTERCORE_HOME", None)
        try:
            cfg = self.vpc_config.PluginConfig(
                config_dir=str(Path(self.home.name) / "pc"))
            repo = (self.instance / "plugins" / "video_parser_core")
            expect = repo.parents[3] / "data" / "cookies"
            self.assertEqual(cfg.cookie_dir, expect)
        finally:
            if old is not None:
                os.environ["ASTERCORE_HOME"] = old

    def test_cookie_root_honors_astercore_home(self):
        """设了 ASTERCORE_HOME 就用它当仓库根（Windows 上防止写到安装目录之外）"""
        home = Path(self.home.name) / "appbase"
        old = os.environ.get("ASTERCORE_HOME")
        os.environ["ASTERCORE_HOME"] = str(home)
        try:
            cfg = self.vpc_config.PluginConfig(
                config_dir=str(Path(self.home.name) / "pc"))
            self.assertEqual(cfg.cookie_dir, home / "data" / "cookies")
            self.assertTrue(cfg.cookie_dir.is_dir(), "cookie 池目录应被创建")
        finally:
            if old is None:
                os.environ.pop("ASTERCORE_HOME", None)
            else:
                os.environ["ASTERCORE_HOME"] = old


    # ---------- 6) JM 子进程的解释器 ----------
    def test_venv_python_probes_windows_layout(self):
        """Windows 的 venv 解释器在 Scripts/ 下；只会退回 bin/python"""
        venv = self.instance / "venv"
        shutil.rmtree(venv, ignore_errors=True)
        scripts = venv / "Scripts" / "python.exe"
        binpy = venv / "bin" / "python"
        try:
            scripts.parent.mkdir(parents=True, exist_ok=True)
            scripts.write_bytes(b"MZ")
            self.assertEqual(self.jm._venv_python(), str(scripts),
                             "Windows venv 的解释器在 Scripts 下，必须优先命中")
            scripts.unlink()
            binpy.parent.mkdir(parents=True, exist_ok=True)
            binpy.write_bytes(b"\x7fELF")     # 兼容层放的副本 / Linux venv
            self.assertEqual(self.jm._venv_python(), str(binpy))
        finally:
            shutil.rmtree(venv, ignore_errors=True)

    def test_venv_python_refuses_frozen_host(self):
        """冻结版 sys.executable 是主程序本身：拿它跑 -c/-m pip 等于再启动一份自己"""
        shutil.rmtree(self.instance / "venv", ignore_errors=True)
        with mock.patch.object(sys, "frozen", True, create=True):
            self.assertIsNone(self.jm._venv_python(),
                              "冻结且无 venv 时不得把主程序当解释器")
            with self.assertRaises(RuntimeError):
                self.jm._venv_python_or_raise()

    def test_venv_python_still_uses_venv_on_linux(self):
        """Linux 上首选仍是 venv/bin/python（线上行为不变）"""
        venv = self.instance / "venv"
        shutil.rmtree(venv, ignore_errors=True)
        binpy = venv / "bin" / "python"
        try:
            binpy.parent.mkdir(parents=True, exist_ok=True)
            binpy.write_bytes(b"\x7fELF")
            self.assertEqual(self.jm._venv_python(), str(binpy))
        finally:
            shutil.rmtree(venv, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
