# 栖星 AsterCore · Linux 版兼容层
"""让现有 Linux 版插件（`handle(event: dict) -> bool` + `utils.*`）零改动运行在 AsterCore。

对齐依据（《Windows版开发计划书》）：
  §4  「与现 Linux 多实例架构（instances/<QQ>）逻辑一致，迁移成本低」
  §5.2「保持现有 SDK 接口不变」
  §10 「data 结构保持兼容，文档说明迁移步骤」

组成：
  onebot.py  事件/消息双向转换（AsterCore Event ↔ OneBot v11 原始 dict、CQ 码）
  context.py 当前账号上下文（供 utils 垫片在多账号下定位）
  utils_pkg.py  sys.modules 里注入 `utils` 包（api/config/log/plugin_toggle/...）
  legacy.py   老插件识别与适配（同步 handle 跑在线程池，bool→内核语义）
"""

from astercore.compat.legacy import (is_legacy_module, make_legacy_adapter,
                                     legacy_meta)

__all__ = ["is_legacy_module", "make_legacy_adapter", "legacy_meta"]
