# -*- coding: utf-8 -*-
"""Python 插件 SDK（plugin-sdk/python/nap_sdk.py）测试

盯两件事：
  ① 契约正确：元数据 dunder / 生成的 handle / utils 调用 / 分群开关 / 配置三段；
  ② **在真宿主里能跑**：把它拷进 <实例>/plugins/ 后，被真实加载器识别成 legacy 插件、
     收到事件能发出 send_group 动作（这是"双端同一份插件代码"的底线）。
"""
import asyncio
import importlib.util
import subprocess
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCAFFOLD = ROOT / "plugin-sdk" / "python" / "new_plugin.py"
SDK_SRC = ROOT / "plugin-sdk" / "python" / "nap_sdk.py"
EXAMPLE_SRC = ROOT / "plugin-sdk" / "python" / "example_plugin.py"

from astercore.compat.utils_pkg import install as compat_install          # noqa: E402
from astercore.core.backend import Backend, BackendConfig, register_backend  # noqa: E402
from astercore.core.models import ActionResult, Event, seg_text           # noqa: E402
from astercore.core.runtime import AccountRuntime                         # noqa: E402


def _load_sdk():
    """把 SDK 当普通文件加载（不依赖 sys.path / plugins 包）"""
    spec = importlib.util.spec_from_file_location("nap_sdk_under_test", SDK_SRC)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["nap_sdk_under_test"] = mod
    spec.loader.exec_module(mod)
    return mod


DEMO_SRC = '''
try:
    from plugins import nap_sdk as sdk
except ImportError:
    import nap_sdk as sdk

sdk.set_meta(globals(), name_cn="SDK 演示", name_en="sdk_demo", version="0.1.0",
             desc="测试用", author="tests")

sdk.declare_config({
    "commands": {"sign1": ("签到", "每日签到"), "on": ("开启", "本群开启")},
    "settings": {"limit": ("上限", 2, "number"), "auto": ("自动", True, "bool")},
    "messages": {"hello": ("问候", "你好")},
}, scope=globals())

_N = {}


@sdk.rule(sdk.cmd("签到"))
def sign_in(event: dict) -> bool:
    if not sdk.enabled(event):
        return False
    gid = str(event.get("group_id"))
    _N[gid] = _N.get(gid, 0) + 1
    if _N[gid] > int(sdk.cfg("settings.limit", 2) or 2):
        return False
    sdk.reply(event, sdk.cfg("messages.hello", "你好"))
    return True


@sdk.rule(sdk.cmd("开启"))
def switch(event: dict) -> bool:
    sdk.toggle(event, True)
    sdk.reply(event, "ok")
    return True


sdk.help_rule("帮助")
'''


class SdkUnitTest(unittest.TestCase):
    """离线单测：不起宿主"""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.dir = Path(cls.tmp.name)
        shutil.copy(SDK_SRC, cls.dir / "nap_sdk.py")
        (cls.dir / "demo_plugin.py").write_text(DEMO_SRC, encoding="utf-8")
        # 前一个（宿主）测试会留下 `plugins` 命名空间与真 utils：单测要的是**离线**环境
        # （SDK 只在缺 utils 时装替身；有真 utils 时 enabled/toggle 需要账号上下文，离线跑不通）
        for name in list(sys.modules):
            if name == "plugins" or name == "utils" or name.startswith("utils."):
                sys.modules.pop(name, None)
        for name in ("nap_sdk", "demo_plugin"):
            sys.modules.pop(name, None)
        sys.path.insert(0, str(cls.dir))
        cls.sdk = importlib.import_module("nap_sdk")
        spec = importlib.util.spec_from_file_location("demo_plugin", cls.dir / "demo_plugin.py")
        cls.mod = importlib.util.module_from_spec(spec)
        sys.modules["demo_plugin"] = cls.mod
        spec.loader.exec_module(cls.mod)

    @classmethod
    def tearDownClass(cls):
        sys.path.remove(str(cls.dir))
        # 把离线替身撤掉：真 utils 由 compat.install() 按需重建（幂等）
        for name in list(sys.modules):
            if name == "utils" or name.startswith("utils."):
                sys.modules.pop(name, None)
        cls.tmp.cleanup()
        for name in ("nap_sdk", "demo_plugin"):
            sys.modules.pop(name, None)

    def test_meta_dunders_written(self):
        for f, v in (("__plugin_name_cn__", "SDK 演示"), ("__plugin_name_en__", "sdk_demo"),
                     ("__plugin_version__", "0.1.0"), ("__plugin_desc__", "测试用"),
                     ("__plugin_author__", "tests")):
            self.assertEqual(getattr(self.mod, f), v, f"{f} 要写进模块")

    def test_handle_generated_and_semantics(self):
        """没手写 handle 时由规则生成；返回 True=已处理、False=放行"""
        self.assertTrue(callable(getattr(self.mod, "handle")))
        self.sdk.dev(vars(self.mod), raw_message="无关内容", verbose=False)
        self.assertEqual(self.sdk.dev(vars(self.mod), raw_message="无关", verbose=False), [])
        # 群默认关闭（与 Linux 语义一致）→ 先发"开启"
        self.sdk.dev(vars(self.mod), raw_message="开启", verbose=False)
        got = self.sdk.dev(vars(self.mod), raw_message="签到", verbose=False)
        self.assertEqual([g["message"] for g in got], ["你好"])

    def test_config_schema_and_values(self):
        schema = self.mod.__config_schema__
        self.assertEqual(schema["limit"]["type"], "number")
        self.assertEqual(schema["auto"]["type"], "bool")
        self.assertEqual(schema["hello"]["type"], "textarea")
        self.assertEqual(schema["sign1"]["label"], "签到")
        self.assertEqual(schema["limit"]["group"], "设置")
        # 默认值兜底 + 落盘读回
        self.assertEqual(self.sdk.cfg("settings.limit", scope=vars(self.mod)), 2)
        p = self.sdk.set_cfg("settings.limit", 9, name="sdk_demo", scope=vars(self.mod),
                             path=self.dir / "sdk_demo_config.json")
        self.assertEqual(json.loads(p.read_text(encoding="utf-8"))["settings"]["limit"], 9)
        self.assertEqual(self.sdk.cfg("settings.limit", path=p, scope=vars(self.mod)), 9)

    def test_toggle_semantics(self):
        ev_group = self.sdk.make_event("x", group_id=777)
        self.assertFalse(self.sdk.enabled(ev_group, name="sdk_demo"), "群默认关")
        self.assertTrue(self.sdk.toggle(ev_group, True, name="sdk_demo"))
        self.assertTrue(self.sdk.enabled(ev_group, name="sdk_demo"))
        self.assertTrue(self.sdk.enabled(self.sdk.make_event("x", group_id=None), name="sdk_demo"),
                        "私聊永远开")

    def test_help_rule_replies_table(self):
        got = self.sdk.dev(vars(self.mod), raw_message="帮助", verbose=False)
        self.assertTrue(got, "help_rule 要能回命令表")
        self.assertIn("SDK 演示", str(got[0]["message"]))

    def test_check_flags_nonportable_source(self):
        bad = self.dir / "bad_plugin.py"
        bad.write_text(
            "import astercore.core.plugin\n"
            "class P(Plugin):\n"
            "    def h(self):\n"
            "        self.ctx.send_group(1, 'x')\n"
            "        open('data/x_config.json')\n", encoding="utf-8")
        spec = importlib.util.spec_from_file_location("bad_plugin", bad)
        mod = importlib.util.module_from_spec(spec)
        sys.modules["bad_plugin"] = mod
        try:
            spec.loader.exec_module(mod)
        except Exception:
            mod.__file__ = str(bad)
        problems = self.sdk.check(vars(mod))
        joined = " | ".join(problems)
        self.assertIn("astercore", joined, f"要认出 astercore 内部依赖：{problems}")
        self.assertIn("ctx", joined, f"要认出类 ctx：{problems}")
        sys.modules.pop("bad_plugin", None)


@register_backend
class _SpyBackend(Backend):
    """记录动作的空后端"""

    name = "sdkspy"

    def __init__(self, cfg, account_id=None):
        super().__init__(cfg, account_id=account_id)
        self.calls = []

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    async def check(self) -> ActionResult:
        return ActionResult.success({})

    async def action(self, action: str, params: dict) -> ActionResult:
        self.calls.append((action, params))
        return ActionResult.success({"message_id": 1})


class SdkInHostTest(unittest.TestCase):
    """集成：把 SDK + 插件放进 <实例>/plugins/，用真实加载器跑一遍"""

    def setUp(self):
        compat_install()
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.instance = base / "accounts" / "10001"
        (self.instance / "plugins").mkdir(parents=True)
        (self.instance / "data").mkdir(parents=True)
        (self.instance / "config.json").write_text(json.dumps({
            "BOT_NAME": "测试星", "BOT_QQ": 10001, "MASTER_QQ": [1],
        }, ensure_ascii=False), encoding="utf-8")
        shutil.copy(SDK_SRC, self.instance / "plugins" / "nap_sdk.py")
        (self.instance / "plugins" / "sdk_demo.py").write_text(DEMO_SRC, encoding="utf-8")
        self.data_dir = base / "data" / "10001"

    def tearDown(self):
        self.tmp.cleanup()
        for name in ("demo_plugin", "nap_sdk", "plugins", "nap_sdk_under_test"):
            sys.modules.pop(name, None)

    async def _run(self, raw: str):
        rt = AccountRuntime(account_id=10001, data_dir=self.data_dir,
                            backend=_SpyBackend(BackendConfig()), plugin_dir=None,
                            instance_dir=self.instance)
        await rt.start()
        names = [p["name"] for p in rt.list_plugins()]
        self.assertIn("sdk_demo", names, f"SDK 插件未被加载: {names}")
        lp = rt.plugin_loader.loaded["sdk_demo"]
        self.assertEqual(lp.kind, "legacy", "模块级 handle + utils → 应走 legacy 通道")
        ev = Event(type="message", account_id=10001, platform="test", time=0,
                   user_id=555, group_id=987654321, self_id=10001,
                   message_type="group", raw=raw, segments=[seg_text(raw)])
        result = await rt.bus.dispatch(ev)
        calls = list(rt.backend.calls)
        await rt.stop()
        return result, calls

    def test_loads_and_handles_through_host(self):
        # 群默认关 → 先"开启"，再"签到"
        asyncio.run(self._run("开启"))
        result, calls = asyncio.run(self._run("签到"))
        self.assertEqual(result, "handled", "SDK 插件应处理该消息")
        self.assertTrue(calls, "应通过 utils.api 发出动作")
        action, params = calls[0]
        self.assertEqual(action, "send_group")
        self.assertEqual(params["message"][0]["data"]["text"], "你好")

    def test_unmatched_passes_through(self):
        result, calls = asyncio.run(self._run("无关内容"))
        self.assertEqual(result, "passed")
        self.assertFalse(calls)


if __name__ == "__main__":
    unittest.main()

class ScaffoldTest(unittest.TestCase):
    """脚手架生成的插件必须**开箱可用**：能落地、能离线自测、契约检查通过"""

    def test_generated_plugin_is_usable(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "plugins"
            r = subprocess.run([sys.executable, str(SCAFFOLD), "签到示例",
                                "--name-en", "sign_demo", "--dir", str(out)],
                               capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertTrue((out / "sign_demo.py").is_file(), r.stdout)
            self.assertTrue((out / "nap_sdk.py").is_file(), "SDK 要一起拷过去，否则插件 import 失败")
            # 生成物的离线自测：跑得起来 + check 通过（退出码 0 就是"检查为空"）
            r2 = subprocess.run([sys.executable, str(out / "sign_demo.py")],
                                capture_output=True, text=True, cwd=str(out))
            self.assertEqual(r2.returncode, 0, r2.stdout + r2.stderr)
            self.assertIn("通过", r2.stdout, r2.stdout)

    def test_refuses_overwrite_without_force(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td)
            args = [sys.executable, str(SCAFFOLD), "X", "--name-en", "xx", "--dir", str(out)]
            self.assertEqual(subprocess.run(args, capture_output=True, text=True).returncode, 0)
            again = subprocess.run(args, capture_output=True, text=True)
            self.assertEqual(again.returncode, 1, "同名文件不该被静默覆盖")
            self.assertIn("--force", again.stdout)
