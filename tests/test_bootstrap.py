# -*- coding: utf-8 -*-
# 首启引导 / 路径解析 / 版本一致性测试
# 背景：打包版必须"解压双击即可用"——目录自动创建、示例插件自动播种，
# 且数据目录不随启动位置乱跑（冻结时以 exe 所在目录为基准）。
import os
import tempfile
import unittest
from pathlib import Path

from astercore import __version__
from astercore import paths
from astercore.bootstrap import (bootstrap, ensure_layout, has_plugins,
                                 seed_example_plugins)

ROOT = Path(__file__).resolve().parent.parent


class PathsTest(unittest.TestCase):

    def test_not_frozen_in_tests(self):
        self.assertFalse(paths.is_frozen())

    def test_astercore_home_env_overrides(self):
        with tempfile.TemporaryDirectory() as td:
            old = os.environ.get("ASTERCORE_HOME")
            os.environ["ASTERCORE_HOME"] = td
            try:
                self.assertEqual(paths.app_base_dir(), Path(td).resolve())
                self.assertEqual(paths.default_data_dir(), Path(td).resolve() / "data")
                self.assertEqual(paths.default_plugins_dir(), Path(td).resolve() / "plugins")
            finally:
                if old is None:
                    os.environ.pop("ASTERCORE_HOME", None)
                else:
                    os.environ["ASTERCORE_HOME"] = old

    def test_explicit_arg_wins(self):
        self.assertEqual(paths.resolve_data_dir("/tmp/xyz"), Path("/tmp/xyz"))
        self.assertEqual(paths.resolve_accounts_dir("/tmp/abc"), Path("/tmp/abc"))

    def test_defaults_when_arg_none(self):
        self.assertEqual(paths.resolve_data_dir(None), paths.default_data_dir())
        self.assertEqual(paths.resolve_accounts_dir(None), paths.default_accounts_dir())

    def test_plugin_templates_dir_exists(self):
        d = paths.plugin_templates_dir()
        self.assertTrue(d.is_dir(), f"示例插件模板目录缺失: {d}")
        self.assertTrue(list(d.glob("*.py")), "模板目录里没有示例插件")


class BootstrapTest(unittest.TestCase):

    def test_ensure_layout_creates_dirs(self):
        with tempfile.TemporaryDirectory() as td:
            d = ensure_layout(Path(td))
            for key in ("data", "accounts", "plugins"):
                self.assertTrue(d[key].is_dir(), f"{key} 未创建")

    def test_seed_copies_into_empty_dir(self):
        with tempfile.TemporaryDirectory() as td:
            copied = seed_example_plugins(Path(td))
            self.assertTrue(copied, "空目录应播种示例插件")
            for name in copied:
                self.assertTrue((Path(td) / name).is_file())

    def test_seed_skips_when_user_plugins_exist(self):
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "my_plugin.py").write_text("# mine", encoding="utf-8")
            copied = seed_example_plugins(Path(td))
            self.assertEqual(copied, [], "已有用户插件时不应播种")
            self.assertFalse((Path(td) / "demo_hello.py").exists())

    def test_seed_does_not_overwrite_existing(self):
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "demo_hello.py"
            target.write_text("# 用户改过的", encoding="utf-8")
            # force 也不覆盖同名文件
            seed_example_plugins(Path(td), force=True)
            self.assertEqual(target.read_text(encoding="utf-8"), "# 用户改过的")

    def test_seed_only_py_files(self):
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "tpl"
            src.mkdir()
            (src / "a.py").write_text("x=1", encoding="utf-8")
            (src / "readme.txt").write_text("doc", encoding="utf-8")
            dst = Path(td) / "dst"
            copied = seed_example_plugins(dst, templates_dir=src)
            self.assertEqual(copied, ["a.py"])
            self.assertFalse((dst / "readme.txt").exists())

    def test_has_plugins_ignores_underscore_and_others(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            self.assertFalse(has_plugins(d))
            (d / "_helper.py").write_text("", encoding="utf-8")
            (d / "notes.txt").write_text("", encoding="utf-8")
            self.assertFalse(has_plugins(d), "下划线开头与非插件后缀不应算插件")
            (d / "real.py").write_text("", encoding="utf-8")
            self.assertTrue(has_plugins(d))

    def test_bootstrap_full(self):
        with tempfile.TemporaryDirectory() as td:
            info = bootstrap(Path(td))
            self.assertTrue(info["seeded"])
            self.assertTrue(Path(info["plugin_dir"]).is_dir())
            # 幂等：再跑一次不再播种
            info2 = bootstrap(Path(td))
            self.assertEqual(info2["seeded"], [])


class VersionTest(unittest.TestCase):

    def test_version_is_semver(self):
        parts = __version__.split(".")
        self.assertEqual(len(parts), 3)
        self.assertTrue(all(p.isdigit() for p in parts), __version__)

    def test_pyproject_matches_package(self):
        txt = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn(f'version = "{__version__}"', txt,
                      "pyproject 与 astercore.__version__ 不一致")


if __name__ == "__main__":
    unittest.main()


class EntryRoutingTest(unittest.TestCase):
    """主程序入口路由：双击/带启动器参数 → 启动器；CLI 参数 → 命令行模式"""

    def _f(self):
        from astercore.__main__ import is_launcher_invocation
        return is_launcher_invocation

    def test_no_args_is_launcher(self):
        self.assertTrue(self._f()([]))

    def test_launcher_flags_go_to_launcher(self):
        # 曾经的缺陷：带了参数就掉进 CLI 解析器，报 unrecognized arguments
        for argv in (["--yes"], ["--no-browser"], ["--port", "9000"],
                     ["--yes", "--no-browser", "--port", "9000"],
                     ["--shell"], ["--host", "127.0.0.1"]):
            self.assertTrue(self._f()(argv), f"{argv} 应进入启动器")

    def test_cli_flags_go_to_cli(self):
        for argv in (["--account", "10001", "--dry-run"],
                     ["--serve-accounts"], ["--backend", "null"],
                     ["--plugin-dir", "x"], ["-v"]):
            self.assertFalse(self._f()(argv), f"{argv} 应进入命令行模式")

    def test_cli_flag_forces_cli(self):
        self.assertFalse(self._f()(["--cli"]))
        self.assertFalse(self._f()(["--cli", "--port", "9000"]))
