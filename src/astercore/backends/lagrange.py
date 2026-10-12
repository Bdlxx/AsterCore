# 栖星 AsterCore · 内置协议直登（Lagrange）后端
#
# 计划书 Phase 4「内置协议（Lagrange · 可选实验后端）」。
# 协议侧**复用 OneBot v11**（Lagrange.OneBot 暴露同款 WS/HTTP 接口，事件/动作完全一致），
# 差别只在**托管**：内核自己把 Lagrange.OneBot 子进程拉起来、写配置、收进程 ——
# 用户不用装 QQ、不用装 NapCat、不用装 .NET（用 SelfContained 包）。
#
# 生命周期：
#   prepare()  组件在不在 → 写 appsettings.json（端口/token 用本账号 backend.json 的）
#              → 重置并拉起子进程 → 等 WS 端口（扫不扫码都能连，只是没登录）
#   start()    继承 OneBot 的 WS 连接（自动重连）
#   stop()     WS 断开 + 子进程收掉
#
# 风险提示（计划书 §3 明确要求）：非官方协议，存在限制登录/封号风险，建议小号；
# 向导里选这个模式必须勾"我已知晓风险"（app.BACKEND_MODES 里 needs_ack=True）。

from __future__ import annotations

import asyncio
import logging

from .. import components
from ..core.backend import BackendConfig, register_backend
from .onebot import OneBotV11Backend

log = logging.getLogger("astercore.backend.lagrange")


@register_backend
class LagrangeBackend(OneBotV11Backend):
    """内置协议直登后端（自托管 Lagrange.OneBot 子进程 + OneBot v11 协议）"""

    name = "lagrange"
    ws_wait = 20.0     # 等子进程监听 WS 的最长秒数（首启要初始化/取签名，给宽一点）

    def __init__(self, cfg: BackendConfig, account_id: int | str | None = None) -> None:
        super().__init__(cfg, account_id)
        from ..paths import app_base_dir
        self.base_dir = app_base_dir()
        self._host = None

    # ---------------- 连接前准备 ----------------
    async def prepare(self) -> None:
        comp = components.get_component("lagrange")
        comp_dir = components.component_dir(self.base_dir, "lagrange")
        if not components.is_installed(comp, self.base_dir):
            raise RuntimeError(
                "内置协议组件未安装：请在面板『连接引导』里点『下载并安装』，"
                f"或手动把 Lagrange.OneBot 解压到 {comp_dir}"
            )

        ws_port = components.port_of(self.cfg.ws_url, 3001)
        http_port = components.port_of(self.cfg.http_url, 3000)
        # 端口/token 与账号 backend.json 完全一致，否则连上也没法发动作
        path = components.write_lagrange_config(
            self.base_dir, ws_port, self.cfg.access_token or "",
            http_port=http_port, uin=self.account_id,
        )
        log.info("内置协议配置已写入: %s（WS %s）", path, self.cfg.ws_url)

        components.host_reset(comp, self.base_dir)      # 配置变了就重拉，别让旧进程挂在旧端口
        self._host = components.host_for(comp, self.base_dir)
        ok, msg = self._host.start()
        if not ok:
            raise RuntimeError(f"内置协议进程启动失败: {msg}")

        ready = await asyncio.to_thread(components.wait_port, ws_port, self.ws_wait)
        if not ready:
            log.warning("内置协议进程已启动，但 %ss 内没监听 %s（可能还在等扫码）；"
                        "WS 客户端会自动重连", self.ws_wait, ws_port)

    # ---------------- 收尾 ----------------
    async def stop(self) -> None:
        await super().stop()
        try:
            components.host_stop(components.get_component("lagrange"), self.base_dir)
        except Exception:                              # noqa: BLE001 收尾别抛
            log.exception("内置协议子进程收尾失败")

    # ---------------- 面板/引导用 ----------------
    def component_status(self) -> dict:
        comp = components.get_component("lagrange")
        st = components.host_status(comp, self.base_dir)
        st["log_tail"] = components.tail_log(self.base_dir, "lagrange", limit=20)
        return st

    def qr_code(self) -> dict:
        return components.qr_code(self.base_dir, "lagrange")
