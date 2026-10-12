# -*- coding: utf-8 -*-
"""内置协议直登（Lagrange）后端：注册表接线 + 托管生命周期

真机/CI 上不该真去下 33MB 的组件，所以这里用**假组件**（一个小脚本，起个 TCP 当 WS 端口），
验证的是"我们这一层的顺序与参数"：写对配置 → 拉起 → 等端口 → 停掉。
"""
import asyncio
import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from astercore import components as C  # noqa: E402
from astercore.core.backend import BackendConfig, get_registry  # noqa: E402

skip_on_windows = unittest.skipIf(os.name == "nt", "假组件用 shebang 脚本，仅 POSIX 可跑")


def _fake_component_script(path: Path, port: int) -> None:
    """假 Lagrange.OneBot：监听端口（等价于"WS 起来了"），然后挂着直到被杀"""
    path.write_text(
        "#!/usr/bin/env python3\n"
        "import socket, time\n"
        "s = socket.socket()\n"
        "s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)\n"
        f"s.bind(('127.0.0.1', {port}))\n"
        "s.listen(5)\n"
        "time.sleep(120)\n",
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


class RegistryTest(unittest.TestCase):
    def test_lagrange_points_to_its_own_backend(self):
        import astercore.backends.lagrange  # noqa: F401
        import astercore.backends.onebot    # noqa: F401  （导入顺序：后注册的赢）
        cls = getattr(get_registry(), "_classes").get("lagrange")
        self.assertIsNotNone(cls, "lagrange 没注册")
        self.assertEqual(cls.__name__, "LagrangeBackend",
                         "lagrange 必须指向带托管的实现，而不是 OneBot 的别名")
        inst = get_registry().create(
            "lagrange", BackendConfig(ws_url="ws://127.0.0.1:3001"), account_id="1")
        self.assertEqual(inst.name, "lagrange")
        self.assertTrue(hasattr(inst, "prepare"), "账号运行时要在连接前调 prepare()")
        self.assertTrue(hasattr(inst, "component_status"))


class PrepareTest(unittest.TestCase):
    def test_mode_requires_risk_ack(self):
        """非官方协议必须弹风险确认（计划书 §3 步骤 2 的硬要求）"""
        from astercore.app import BACKEND_MODES
        self.assertTrue(BACKEND_MODES["lagrange"]["needs_ack"])
        self.assertLess(BACKEND_MODES["lagrange"]["risk"], BACKEND_MODES["napcat"]["risk"])

    def test_prepare_without_component_tells_user_where_to_click(self):
        import astercore.backends.lagrange as LB
        with tempfile.TemporaryDirectory() as td:
            b = LB.LagrangeBackend(BackendConfig())
            b.base_dir = td
            with self.assertRaises(RuntimeError) as ctx:
                asyncio.run(b.prepare())
            msg = str(ctx.exception)
            self.assertIn("连接引导", msg)
            self.assertIn("lagrange", msg)

    @skip_on_windows
    def test_prepare_spawns_writes_config_and_stop_kills(self):
        import astercore.backends.lagrange as LB
        with tempfile.TemporaryDirectory() as td:
            port = C.free_port(0)
            comp_dir = Path(td) / "components/lagrange"
            comp_dir.mkdir(parents=True)
            _fake_component_script(comp_dir / "Lagrange.OneBot.exe", port)

            cfg = BackendConfig(ws_url=f"ws://127.0.0.1:{port}",
                                http_url=f"http://127.0.0.1:{port}",
                                access_token="tk-abc")
            b = LB.LagrangeBackend(cfg, account_id="12345")
            b.base_dir = td
            b.ws_wait = 6.0
            asyncio.run(b.prepare())

            # ① 配置按账号参数写对（端口/token 必须与 backend.json 一致，否则连上也发不了动作）
            app_cfg = json.loads((comp_dir / "appsettings.json").read_text(encoding="utf-8"))
            fw = [i for i in app_cfg["Implementations"] if i["Type"] == "ForwardWebSocket"][0]
            self.assertEqual((fw["Host"], fw["Port"], fw["AccessToken"]),
                             ("127.0.0.1", port, "tk-abc"))
            self.assertEqual(app_cfg["Account"]["Uin"], 12345)

            # ② 子进程真起来了（端口可连 + 托管状态在跑）
            st = b.component_status()
            self.assertTrue(st["installed"])
            self.assertTrue(st["running"], st)
            self.assertTrue(C.wait_port(port, timeout=2.0))

            # ③ stop 之后进程收干净（否则下次启动端口被占）
            asyncio.run(b.stop())
            self.assertFalse(b.component_status()["running"])
            self.assertFalse(C.wait_port(port, timeout=1.0), "停掉后端口不该还在监听")

    @skip_on_windows
    def test_prepare_twice_does_not_leak_processes(self):
        """重复 prepare（面板点两次启动）不能拉出两个进程抢同一端口"""
        import astercore.backends.lagrange as LB
        with tempfile.TemporaryDirectory() as td:
            port = C.free_port(0)
            comp_dir = Path(td) / "components/lagrange"
            comp_dir.mkdir(parents=True)
            _fake_component_script(comp_dir / "Lagrange.OneBot.exe", port)
            cfg = BackendConfig(ws_url=f"ws://127.0.0.1:{port}", access_token="")
            b = LB.LagrangeBackend(cfg, account_id="1")
            b.base_dir = td
            b.ws_wait = 6.0
            asyncio.run(b.prepare())
            pid1 = C.host_status(C.get_component("lagrange"), td).get("pid")
            asyncio.run(b.prepare())
            pid2 = C.host_status(C.get_component("lagrange"), td).get("pid")
            self.assertTrue(pid1 and pid2)
            self.assertNotEqual(pid1, pid2, "第二次 prepare 应当先收掉旧进程再拉新的")
            asyncio.run(b.stop())
            self.assertFalse(C.host_status(C.get_component("lagrange"), td)["running"])


if __name__ == "__main__":
    unittest.main()
