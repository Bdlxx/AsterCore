# 栖星 AsterCore · Backend 适配层接口
# 内核只认统一 Event/Action；每种接入（NapCat OneBot v11、Lagrange、未来第三方）
# 实现一个 Backend，负责：翻译事件 → 统一 Event，执行统一 Action → 后端 API。

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from .models import ActionResult, Event

# 内核 → 后端 的连接参数（引导/配置页三件套，见计划书 §4.1）
@dataclass
class BackendConfig:
    ws_url: str = "ws://127.0.0.1:3001"
    http_url: str = "http://127.0.0.1:3000"
    access_token: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self):
        return {"ws_url": self.ws_url, "http_url": self.http_url,
                "access_token": self.access_token, "extra": self.extra}


class Backend(ABC):
    """后端抽象。

    生命周期：configure() → start()（拉起连接） → 事件回调 → stop()
    事件回调 on_event: Callable[[Event], None] 由内核（账号运行时）注入。
    """

    name: str = "base"

    def __init__(self, cfg: BackendConfig, account_id: int | str | None = None) -> None:
        self.cfg = cfg
        self.account_id = account_id
        self.on_event: Callable[[Event], None] | None = None
        self._running = False

    # ---- 生命周期 ----
    @abstractmethod
    async def start(self) -> None:
        """建立连接并开始接收事件（实现方负责重连）"""

    async def stop(self) -> None:
        self._running = False

    @property
    def running(self) -> bool:
        return self._running

    # ---- 动作（内核/插件 → 后端） ----
    @abstractmethod
    async def action(self, action: str, params: dict[str, Any]) -> ActionResult:
        """执行统一动作；实现方翻译成后端 API。"""

    # ---- 状态/探测 ----
    @abstractmethod
    async def check(self) -> ActionResult:
        """连通性/登录态探测（引导页『测试连接』用）"""

    # ---- 工具 ----
    def _emit(self, ev: Event) -> None:
        """把事件投递给内核回调。回调可能是 async（runtime._on_event），
        而本方法可能在 WS 线程被调用 → 用 run_coroutine_threadsafe 投到主循环。"""
        if not self.on_event:
            return
        import asyncio
        ret = self.on_event(ev)
        if asyncio.iscoroutine(ret):
            loop = getattr(self, "_loop", None) or self._main_loop
            if loop is None or not loop.is_running():
                # 兜底：在当前运行 loop 中调度（单测等场景）
                try:
                    asyncio.get_running_loop().create_task(ret)
                except RuntimeError:
                    pass
                return
            try:
                asyncio.run_coroutine_threadsafe(ret, loop)
            except Exception:
                pass
            return
        # 同步回调直接调用
        ret() if callable(ret) else None

    # 供子类设置主事件循环（start 时记录）
    @property
    def _main_loop(self):
        import asyncio
        try:
            return asyncio.get_running_loop()
        except RuntimeError:
            return None


class BackendRegistry:
    """后端注册表：按名称创建实例（backend-xxx 可插拔）"""

    def __init__(self) -> None:
        self._classes: dict[str, type[Backend]] = {}

    def register(self, cls: type[Backend], name: str | None = None) -> None:
        """注册后端；name 缺省用 cls.name。同一类可注册多个名字（如 lagrange 复用 onebot）"""
        self._classes[name or cls.name] = cls

    def create(self, name: str, cfg: BackendConfig,
               account_id: int | str | None = None) -> Backend:
        if name not in self._classes:
            raise KeyError(f"未知后端: {name}（已注册: {list(self._classes)}）")
        return self._classes[name](cfg, account_id)

    def names(self) -> list[str]:
        return sorted(self._classes)


_registry = BackendRegistry()


def register_backend(cls: type[Backend], name: str | None = None) -> type[Backend]:
    _registry.register(cls, name=name)
    return cls


def get_registry() -> BackendRegistry:
    return _registry
