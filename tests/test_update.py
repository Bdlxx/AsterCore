# 更新器内核测试：版本比较 / sha256 / zip 替换保数据 / 回滚
import hashlib
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from astercore.update import (ApplyResult, Manifest, Updater, is_newer,
                              parse_version, sha256_of)


class VersionTest(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(parse_version("0.2.0"), (0, 2, 0))
        self.assertEqual(parse_version("1.2.3-beta"), (1, 2, 3))
        self.assertEqual(parse_version("x"), (0,))

    def test_is_newer(self):
        self.assertTrue(is_newer("0.2.0", "0.1.9"))
        self.assertFalse(is_newer("0.1.0", "0.2.0"))
        self.assertFalse(is_newer("0.1.0", "0.1.0"))


class ShaTest(unittest.TestCase):
    def test_sha256(self):
        with tempfile.NamedTemporaryFile("wb", delete=False) as f:
            f.write(b"hello astercore")
            name = f.name
        expected = hashlib.sha256(b"hello astercore").hexdigest()
        self.assertEqual(sha256_of(name), expected)


class UpdaterTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.app = self.tmp / "app"
        (self.app / "data").mkdir(parents=True)
        (self.app / "plugins").mkdir()
        (self.app / "core").write_text("v1 core", encoding="utf-8")
        (self.app / "data" / "keepme.json").write_text("{}", encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _make_pkg(self, sha: bool = False) -> tuple[Path, Manifest]:
        """构造 v2 更新包：core 变化、plugins 新增、不含 data"""
        pkgdir = self.tmp / "pkg"
        pkgdir.mkdir(exist_ok=True)
        (pkgdir / "plugins").mkdir(exist_ok=True)
        (pkgdir / "core").write_text("v2 core", encoding="utf-8")
        (pkgdir / "plugins" / "newplug.py").write_text("pass", encoding="utf-8")
        zpath = self.tmp / "update.zip"
        with zipfile.ZipFile(zpath, "w") as z:
            for f in pkgdir.rglob("*"):
                if f.is_file():
                    z.write(f, f.relative_to(pkgdir).as_posix())
        m = Manifest(version="0.2.0")
        if sha:
            m.sha256 = sha256_of(zpath)
        return zpath, m

    def test_apply_keeps_data(self):
        z, m = self._make_pkg(sha=True)
        up = Updater(self.app)
        res = up.apply(z, m, keep_dirs=["data"])
        self.assertTrue(res.ok, res.error)
        self.assertEqual((self.app / "core").read_text(), "v2 core")
        self.assertTrue((self.app / "plugins" / "newplug.py").exists())
        # data 保留
        self.assertTrue((self.app / "data" / "keepme.json").exists())

    def test_bad_sha_rejected(self):
        z, m = self._make_pkg()
        m.sha256 = "0" * 64
        up = Updater(self.app)
        res = up.apply(z, m)
        self.assertFalse(res.ok)
        self.assertIn("sha256", res.error)

    def test_rollback(self):
        z, m = self._make_pkg()
        up = Updater(self.app)
        res = up.apply(z, m)
        self.assertTrue(res.ok)
        self.assertEqual((self.app / "core").read_text(), "v2 core")
        # 手动破坏后回滚（回到更新前的 v1）
        (self.app / "core").write_text("broken", encoding="utf-8")
        self.assertTrue(up.rollback(res.backup_dir))
        self.assertEqual((self.app / "core").read_text(), "v1 core")
        self.assertTrue((self.app / "data" / "keepme.json").exists())


if __name__ == "__main__":
    unittest.main()
