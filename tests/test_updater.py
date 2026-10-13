# -*- coding: utf-8 -*-
"""应用内更新器（计划书 §369）：版本比较 / 检查 / 下载哈希 / 暂存 / 回滚

全部离线可测：网络层是可替换的模块级函数，下载用 file:// URL 验真实路径。
"""
import hashlib
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from astercore import updater as U  # noqa: E402


class VersionTest(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(U.parse_version("v0.6.9"), (0, 6, 9))
        self.assertEqual(U.parse_version("0.6.9-beta1"), (0, 6, 9, 1))
        self.assertEqual(U.parse_version(None), (0,))

    def test_is_newer(self):
        self.assertTrue(U.is_newer("v0.6.10", "0.6.9"), "0.6.10 > 0.6.9（按数字比，不按字符串）")
        self.assertTrue(U.is_newer("0.7.0", "0.6.9"))
        self.assertFalse(U.is_newer("0.6.9", "0.6.9"))
        self.assertFalse(U.is_newer("0.6.8", "0.6.9"))
        self.assertTrue(U.is_newer("1.0", "0.99.99"))


class CheckTest(unittest.TestCase):
    def setUp(self):
        self._json, self._text = U.http_json, U.http_text
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        U.http_json, U.http_text = self._json, self._text
        self.tmp.cleanup()

    def _stub(self, tag="v9.9.9", with_checksum="a" * 64):
        rel = {"tag_name": tag, "body": "更新内容", "html_url": "https://example/r",
               "assets": [{"name": "AsterCore-win64-9.9.9.zip", "size": 123,
                           "browser_download_url": "https://example/a.zip"}]}
        U.http_json = lambda url, timeout=20: rel
        if with_checksum is None:
            def boom(url, timeout=20):
                raise OSError("404")
            U.http_text = boom
        else:
            U.http_text = lambda url, timeout=20: f"{with_checksum}  AsterCore.zip"

    def test_available_with_checksum(self):
        self._stub()
        info = U.check("0.6.9")
        self.assertTrue(info["available"])
        self.assertEqual(info["version"], "9.9.9")
        self.assertEqual(info["asset_name"], "AsterCore-win64-9.9.9.zip")
        self.assertEqual(info["sha256"], "a" * 64, "要从 <zip>.sha256 预取校验值")

    def test_same_version_not_available(self):
        self._stub(tag="v0.6.9")
        info = U.check("0.6.9")
        self.assertFalse(info["available"])

    def test_missing_checksum_is_tolerated(self):
        self._stub(with_checksum=None)
        info = U.check("0.1.0")
        self.assertTrue(info["available"])
        self.assertIsNone(info["sha256"], "没附校验文件要如实为 None，不能假装校验过")

    def test_network_error_does_not_raise(self):
        def boom(url, timeout=20):
            raise OSError("no network")
        U.http_json = boom
        info = U.check("0.6.9")
        self.assertFalse(info["available"])
        self.assertIn("no network", info["error"])


class DownloadTest(unittest.TestCase):
    def test_download_and_sha256(self):
        with tempfile.TemporaryDirectory() as d:
            src = Path(d) / "src.bin"
            src.write_bytes(b"x" * 100000)
            seen = []
            digest, size = U.http_download(src.as_uri(), Path(d) / "out.bin",
                                           on_progress=lambda a, b: seen.append((a, b)))
            self.assertEqual(size, 100000)
            self.assertEqual(digest, hashlib.sha256(src.read_bytes()).hexdigest())
            self.assertTrue(seen, "要回调进度")
            self.assertEqual(U.sha256_file(Path(d) / "out.bin"), digest)


class StageTest(unittest.TestCase):
    def _make_zip(self, path, nested=True, exe=True):
        with zipfile.ZipFile(path, "w") as z:
            root = "AsterCore/" if nested else ""
            if exe:
                z.writestr(root + "astercore.exe", b"MZ")
            z.writestr(root + "_internal/data.txt", b"hello")
        return path

    def test_stage_flattens_and_finds_exe(self):
        with tempfile.TemporaryDirectory() as d:
            install = Path(d) / "app"
            install.mkdir()
            z = self._make_zip(Path(d) / "new.zip")
            meta = U.stage(z, install)
            new = Path(meta["staged_dir"])
            self.assertTrue((new / U.EXE_NAME).is_file(), "多一层根目录要拍平")
            self.assertTrue((new / "_internal" / "data.txt").is_file())
            self.assertTrue(meta["has_exe"])
            self.assertTrue((Path(d) / "app_new.json").is_file(), "要写暂存标记")
            self.assertEqual(meta["install_dir"], str(install.resolve()))

    def test_stage_rejects_bad_package(self):
        with tempfile.TemporaryDirectory() as d:
            install = Path(d) / "app"
            install.mkdir()
            z = Path(d) / "bad.zip"
            with zipfile.ZipFile(z, "w") as zf:
                zf.writestr("readme.txt", b"no exe")
            with self.assertRaises(ValueError):
                U.stage(z, install)

    def test_stage_rejects_zip_slip(self):
        with tempfile.TemporaryDirectory() as d:
            install = Path(d) / "app"
            install.mkdir()
            z = Path(d) / "evil.zip"
            with zipfile.ZipFile(z, "w") as zf:
                zf.writestr("../evil.txt", b"pwn")
            with self.assertRaises(ValueError):
                U.stage(z, install)


class HandoffTest(unittest.TestCase):
    def test_handoff_script_content(self):
        with tempfile.TemporaryDirectory() as d:
            install = Path(d) / "app"
            install.mkdir()
            p = U.write_handoff(install)
            t = p.read_text(encoding="utf-8")
            for marker in ("tasklist", "app_new", "app_old", U.EXE_NAME, "start \"\""):
                self.assertIn(marker, t)

    def test_rollback_when_boot_not_ok(self):
        with tempfile.TemporaryDirectory() as d:
            install = Path(d) / "app"
            install.mkdir()
            (install / "newfile.txt").write_text("new")
            old = Path(d) / "app_old"
            old.mkdir()
            (old / U.EXE_NAME).write_text("old")
            self.assertEqual(U.rollback_if_needed(install), "rolled-back")
            self.assertTrue((install / U.EXE_NAME).read_text() == "old", "旧版要还原回来")

    def test_no_rollback_when_boot_ok(self):
        with tempfile.TemporaryDirectory() as d:
            install = Path(d) / "app"
            install.mkdir()
            Path(d) / "app_old"
            (Path(d) / "app_old").mkdir()
            U.mark_boot_ok(install)
            self.assertEqual(U.rollback_if_needed(install), "", "已确认能启动就不能回滚")

    def test_cleanup_old(self):
        with tempfile.TemporaryDirectory() as d:
            install = Path(d) / "app"
            install.mkdir()
            for s in ("_old", "_broken"):
                (Path(d) / ("app" + s)).mkdir()
            U.cleanup_old(install)
            self.assertFalse((Path(d) / "app_old").exists())
            self.assertFalse((Path(d) / "app_broken").exists())


class FlowTest(unittest.TestCase):
    def setUp(self):
        self._prev = (U.http_json, U.http_text)

    def tearDown(self):
        U.http_json, U.http_text = self._prev

    def test_flow_end_to_end_offline(self):
        """检查→下载→校验→暂存 全流程（下载用 file:// 本地 zip 冒充 Release 资产）"""
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            payload = d / "asset.zip"
            with zipfile.ZipFile(payload, "w") as z:
                z.writestr("astercore.exe", b"MZ")
                z.writestr("_internal/x.txt", b"y")
            good = hashlib.sha256(payload.read_bytes()).hexdigest()
            U.http_json = lambda url, timeout=20: {
                "tag_name": "v99.0.0", "body": "", "html_url": "https://example",
                "assets": [{"name": "AsterCore-win64-99.0.0.zip", "size": payload.stat().st_size,
                            "browser_download_url": payload.as_uri()}]}
            U.http_text = lambda url, timeout=20: good
            install = d / "app"
            install.mkdir()
            prev = (U.http_json, U.http_text)
            try:
                out = U.update_flow("0.6.9", d / "dl", install)
            finally:
                U.http_json, U.http_text = prev
            self.assertTrue(out["ok"], out)
            self.assertTrue(out["verified"], "有校验文件时必须真校验")
            self.assertTrue(Path(out["staged"]).is_dir())
            self.assertTrue(Path(out["script"]).is_file())

    def test_flow_detects_bad_checksum(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            payload = d / "asset.zip"
            with zipfile.ZipFile(payload, "w") as z:
                z.writestr("astercore.exe", b"MZ")
            U.http_json = lambda url, timeout=20: {
                "tag_name": "v99.0.0", "body": "", "html_url": "",
                "assets": [{"name": "AsterCore-win64-99.0.0.zip", "size": 1,
                            "browser_download_url": payload.as_uri()}]}
            U.http_text = lambda url, timeout=20: "b" * 64      # 故意错的校验值
            install = d / "app"
            install.mkdir()
            out = U.update_flow("0.6.9", d / "dl", install)
            self.assertFalse(out["ok"])
            self.assertIn("sha256 不匹配", out["error"])


if __name__ == "__main__":
    unittest.main()
