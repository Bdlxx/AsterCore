# -*- coding: utf-8 -*-
"""可选组件层（NapCat 引导 / Lagrange 内置协议）单测

重点钉住三件事：
  ① 写配置**绝不覆盖用户其它键**（Linux 侧轮换 token 踩过）；
  ② 安装路径能吃"已下好的 zip"与 file://（离线可用、可测），并挡住 zip-slip；
  ③ 组件表本身别写错（下载源/可执行名/配置写入器齐备）。
"""
import json
import os
import socket
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from astercore import components as C  # noqa: E402


def _make_zip(path: Path, files: dict[str, bytes], *, prefix: str = "") -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in files.items():
            zf.writestr(prefix + name, data)
    return path


class PortTokenTest(unittest.TestCase):
    def test_free_port_is_bindable(self):
        port = C.free_port(0)
        self.assertGreater(port, 0)
        with socket.socket() as s:
            s.bind(("127.0.0.1", port))     # 能绑上才算真空闲

    def test_free_port_avoids_busy_port(self):
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            busy = s.getsockname()[1]
            got = C.free_port(busy)
            self.assertNotEqual(got, busy)

    def test_random_token(self):
        a, b = C.random_token(), C.random_token()
        self.assertGreaterEqual(len(a), 16)
        self.assertNotEqual(a, b)


class MergeWriteTest(unittest.TestCase):
    def test_deep_merge_keeps_unknown_keys(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "x.json"
            p.write_text(json.dumps({"keep": 1, "nested": {"a": 1, "b": 2}}), encoding="utf-8")
            C.merge_write_json(p, {"nested": {"b": 9}, "new": True})
            got = json.loads(p.read_text(encoding="utf-8"))
            self.assertEqual(got["keep"], 1)
            self.assertEqual(got["nested"], {"a": 1, "b": 9})
            self.assertTrue(got["new"])

    def test_read_json_tolerates_broken(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "broken.json"
            p.write_text("{ not json", encoding="utf-8")
            self.assertEqual(C.read_json(p), {})
            self.assertEqual(C.read_json(Path(td) / "missing.json"), {})


class NapCatConfigTest(unittest.TestCase):
    def test_writes_network_and_token(self):
        with tempfile.TemporaryDirectory() as td:
            p = C.write_napcat_config(td, "2551736206", 3001, 3000, "tok123")
            self.assertEqual(p, Path(td) / "components/napcat/config/onebot11_2551736206.json")
            d = json.loads(p.read_text(encoding="utf-8"))
            ws = d["network"]["websocketServers"][0]
            self.assertEqual((ws["host"], ws["port"], ws["token"]), ("127.0.0.1", 3001, "tok123"))
            self.assertTrue(ws["enable"])
            http = d["network"]["httpServers"][0]
            self.assertEqual((http["port"], http["token"]), (3000, "tok123"))

    def test_preserves_user_keys_in_existing_config(self):
        """用户手装的 NapCat 配置里别的东西必须原样保留"""
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "components/napcat/config/onebot11_123.json"
            p.parent.mkdir(parents=True)
            p.write_text(json.dumps({
                "musicSignServerUrl": "https://example.com/sign",
                "network": {
                    "websocketServers": [{"name": "my-ws", "debug": True, "port": 9999}],
                    "httpServers": [{"name": "my-http"}],
                    "httpSseServers": [{"name": "keepme"}],
                },
            }, ensure_ascii=False), encoding="utf-8")
            C.write_napcat_config(td, "123", 3101, 3100, "newtok")
            d = json.loads(p.read_text(encoding="utf-8"))
            self.assertEqual(d["musicSignServerUrl"], "https://example.com/sign")
            ws = d["network"]["websocketServers"][0]
            self.assertEqual(ws["name"], "my-ws")        # 名字保留
            self.assertFalse(ws["debug"])                  # 我们负责的键被更新
            self.assertEqual(ws["port"], 3101)
            self.assertEqual(ws["token"], "newtok")
            self.assertEqual(d["network"]["httpSseServers"], [{"name": "keepme"}])
            self.assertEqual(d["network"]["httpServers"][0]["name"], "my-http")


class LagrangeConfigTest(unittest.TestCase):
    def test_writes_forward_ws(self):
        with tempfile.TemporaryDirectory() as td:
            p = C.write_lagrange_config(td, 3001, "tok", http_port=3000)
            self.assertEqual(p, Path(td) / "components/lagrange/appsettings.json")
            d = json.loads(p.read_text(encoding="utf-8"))
            fw = [i for i in d["Implementations"] if i["Type"] == "ForwardWebSocket"]
            self.assertEqual(len(fw), 1)
            self.assertEqual((fw[0]["Host"], fw[0]["Port"], fw[0]["AccessToken"]),
                             ("127.0.0.1", 3001, "tok"))
            self.assertTrue(any(i["Type"] == "Http" for i in d["Implementations"]))
            self.assertIn("SignServerUrl", d)

    def test_keeps_extra_top_level_keys(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "components/lagrange/appsettings.json"
            p.parent.mkdir(parents=True)
            p.write_text(json.dumps({"MyCustom": {"x": 1}, "Account": {"Uin": 10001}}),
                         encoding="utf-8")
            C.write_lagrange_config(td, 3001, "t")
            d = json.loads(p.read_text(encoding="utf-8"))
            self.assertEqual(d["MyCustom"], {"x": 1})
            self.assertEqual(d["Account"]["Uin"], 10001)   # 深合并：已有的值保留


class InstallTest(unittest.TestCase):
    def test_install_from_local_zip_via_file_url(self):
        """离线路径：file:// 也要能装（引导里"我下好了 zip 给你"同一套代码）"""
        with tempfile.TemporaryDirectory() as td:
            z = _make_zip(Path(td) / "lag.zip",
                          {"Lagrange.OneBot.exe": b"MZ", "appsettings.json": b"{}"},
                          prefix="Lagrange/")
            url = Path(z).as_uri()
            ok, msg = C.provision(C.get_component("lagrange"), td, url=url)
            self.assertTrue(ok, msg)
            exe = C.find_executable(C.get_component("lagrange"), td)
            self.assertIsNotNone(exe)
            self.assertTrue(Path(td, "components/lagrange/Lagrange.OneBot.exe").is_file())
            if os.name != "nt":
                self.assertTrue(exe.stat().st_mode & 0o111, "应为可执行")

    def test_install_rejects_wrong_package(self):
        with tempfile.TemporaryDirectory() as td:
            z = _make_zip(Path(td) / "bad.zip", {"README.txt": b"nope"})
            ok, msg = C.install_from_zip(C.get_component("lagrange"), td, z)
            self.assertFalse(ok)
            self.assertIn("没找到可执行文件", msg)

    def test_install_blocks_zip_slip(self):
        with tempfile.TemporaryDirectory() as td:
            z = Path(td) / "evil.zip"
            with zipfile.ZipFile(z, "w") as zf:
                zf.writestr("../escape.txt", b"x")
            ok, msg = C.install_from_zip(C.get_component("lagrange"), td, z)
            self.assertFalse(ok)
            self.assertIn("越界", msg)
            self.assertFalse((Path(td).parent / "escape.txt").exists())

    def test_install_bad_zip(self):
        with tempfile.TemporaryDirectory() as td:
            z = Path(td) / "trunc.zip"
            z.write_bytes(b"PK\x03\x04broken")
            ok, msg = C.install_from_zip(C.get_component("lagrange"), td, z)
            self.assertFalse(ok)

    def test_flatten_single_root(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "c"
            (root / "inner").mkdir(parents=True)
            (root / "inner" / "Lagrange.OneBot.exe").write_bytes(b"MZ")
            C.flatten_single_root(root)
            self.assertTrue((root / "Lagrange.OneBot.exe").is_file())

    def test_find_executable_in_nested_dir(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td) / "components/lagrange/pkg"
            d.mkdir(parents=True)
            (d / "Lagrange.OneBot.exe").write_bytes(b"MZ")
            self.assertIsNotNone(C.find_executable(C.get_component("lagrange"), td))

    def test_status_reports_installed(self):
        with tempfile.TemporaryDirectory() as td:
            st = C.status(C.get_component("lagrange"), td)
            self.assertFalse(st["installed"])
            d = Path(td) / "components/lagrange"
            d.mkdir(parents=True)
            (d / "Lagrange.OneBot.exe").write_bytes(b"MZ")
            st = C.status(C.get_component("lagrange"), td, running=True)
            self.assertTrue(st["installed"])
            self.assertTrue(st["running"])
            self.assertIn("Lagrange.OneBot.exe", st["executable"])


class DiscoveryTest(unittest.TestCase):
    def test_discover_finds_component(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td) / "components/napcat"
            d.mkdir(parents=True)
            (d / "NapCatWinBootMain.exe").write_bytes(b"MZ")
            found = C.discover_local(td)
            self.assertTrue(any(f["kind"] == "napcat" and f["how"] == "component" for f in found))

    def test_discover_empty_is_safe(self):
        with tempfile.TemporaryDirectory() as td:
            # 端口探测会走网络（本机无服务 → 全部失败），不该抛异常
            self.assertIsInstance(C.discover_local(td), list)

    def test_probe_endpoint_fails_gracefully(self):
        r = C.probe_endpoint("http://127.0.0.1:1", timeout=0.5)
        self.assertFalse(r["ok"])
        self.assertIn("连接失败", r["msg"])


class ComponentTableTest(unittest.TestCase):
    def test_table_is_complete(self):
        self.assertEqual(set(C.COMPONENTS), {"napcat", "lagrange"})
        for key, cmp in C.COMPONENTS.items():
            with self.subTest(key=key):
                self.assertTrue(cmp.exe_candidates, "必须给可执行文件候选名")
                self.assertTrue(cmp.download_urls, "必须给下载源（不捆绑也不等于不下载）")
                self.assertIsNotNone(cmp.config_writer, "必须能写配置")
                self.assertTrue(cmp.label and cmp.note)

    def test_lagrange_uses_selfcontained_win64(self):
        urls = C.get_component("lagrange").download_urls[0]
        self.assertIn("win-x64", urls)
        self.assertIn("SelfContained", urls, "自带运行时，用户才不用装 .NET")

    def test_unknown_component(self):
        with self.assertRaises(KeyError):
            C.get_component("nope")


if __name__ == "__main__":
    unittest.main()


class BundledComponentTest(unittest.TestCase):
    """内置协议必须**软件自带**：用户目录没装时，应能在自带目录里找到可执行文件"""

    def setUp(self):
        from astercore import components as C
        self.C = C
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name) / "data"
        self.base.mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    def test_bundled_dir_none_when_absent(self):
        # 源码树里没有 pack/components/lagrange 时也不能炸（CI/开发机都可能没有）
        d = self.C.bundled_dir("definitely-not-a-component")
        self.assertIsNone(d)

    def test_find_executable_falls_back_to_bundled(self):
        comp = self.C.get_component("lagrange")
        bundled = Path(self.tmp.name) / "bundled" / "lagrange"
        bundled.mkdir(parents=True)
        exe = bundled / comp.exe_candidates[0]
        exe.write_bytes(b"MZ")                      # 假的 exe，存在即可
        real = self.C.bundled_dir
        try:
            self.C.bundled_dir = lambda key: bundled if key == "lagrange" else None
            self.assertTrue(self.C.is_installed(comp, self.base),
                            "用户目录里没有，但自带里有 → 算已安装（无需下载）")
            self.assertIsNotNone(self.C.find_executable(comp, self.base))
        finally:
            self.C.bundled_dir = real

    def test_user_dir_wins_over_bundled(self):
        comp = self.C.get_component("lagrange")
        user = self.C.component_dir(self.base, "lagrange")
        user.mkdir(parents=True)
        (user / comp.exe_candidates[0]).write_bytes(b"MZ-user")
        bundled = Path(self.tmp.name) / "bundled" / "lagrange"
        bundled.mkdir(parents=True)
        (bundled / comp.exe_candidates[0]).write_bytes(b"MZ-bundled")
        real = self.C.bundled_dir
        try:
            self.C.bundled_dir = lambda key: bundled if key == "lagrange" else None
            got = self.C.find_executable(comp, self.base)
            self.assertIsNotNone(got)
            self.assertIn("MZ-user", got.read_bytes().decode("latin-1"))
        finally:
            self.C.bundled_dir = real

    def test_deep_nested_layout_is_found(self):
        """自带包真实结构很深（内置协议在 5 层目录里）—— 必须能找到"""
        comp = self.C.get_component("lagrange")
        deep = (Path(self.tmp.name) / "bundled" / "lagrange" / "Lagrange.OneBot"
                / "bin" / "Release" / "net9.0" / "win-x64" / "publish")
        deep.mkdir(parents=True)
        exe = deep / comp.exe_candidates[0]
        exe.write_bytes(b"MZ-deep")
        real = self.C.bundled_dir
        try:
            self.C.bundled_dir = lambda key: (Path(self.tmp.name) / "bundled" / key)
            got = self.C.find_executable(comp, self.base)
            self.assertIsNotNone(got, "深层目录里也要能找到")
            self.assertEqual(got.name, comp.exe_candidates[0])
        finally:
            self.C.bundled_dir = real

