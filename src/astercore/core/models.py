# 栖星 AsterCore · 统一事件 / 动作模型
# 内核与后端、内核与插件之间只交换这些 JSON 兼容结构。

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# ---------------- 事件 ----------------

# 事件类型
EVT_MESSAGE = "message"
EVT_GROUP_INCREASE = "group_increase"
EVT_GROUP_DECREASE = "group_decrease"
EVT_NOTICE = "notice"
EVT_REQUEST = "request"
EVT_CRON = "cron"  # 预留：定时器事件（由调度器注入）

# 消息段类型（OneBot v11 子集，与插件 SDK 对齐）
SEG_TEXT = "text"
SEG_IMAGE = "image"
SEG_VIDEO = "video"
SEG_RECORD = "record"
SEG_FILE = "file"
SEG_AT = "at"
SEG_REPLY = "reply"
SEG_FORWARD = "forward"


@dataclass(slots=True)
class Segment:
    """消息段：type + data（与 OneBot 兼容的字典结构）"""

    type: str
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"type": self.type, "data": self.data}


@dataclass(slots=True)
class Event:
    """标准事件（后端翻译后的统一形态）。

    核心字段与《ABI 草案》§5 一致：
    - account_id: 来源账号（多账号区分）
    - platform:   后端标识（napcat / lagrange / …）
    - segments:   消息段列表（message 事件）
    """

    type: str
    account_id: int | str
    platform: str
    time: int
    user_id: int | str | None = None
    group_id: int | str | None = None
    self_id: int | str | None = None
    message_type: str | None = None  # group / private
    raw: str = ""
    segments: list[Segment] = field(default_factory=list)
    reply_token: str | None = None  # 精准回复用（一次性）
    extra: dict[str, Any] = field(default_factory=dict)

    # ---- 便捷 ----
    def text(self) -> str:
        """拼接纯文本（text 段内容）"""
        return "".join(s.data.get("text", "") for s in self.segments if s.type == SEG_TEXT)

    def is_group(self) -> bool:
        return self.message_type == "group"

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "account_id": self.account_id,
            "platform": self.platform,
            "time": self.time,
            "user_id": self.user_id,
            "group_id": self.group_id,
            "self_id": self.self_id,
            "message_type": self.message_type,
            "raw": self.raw,
            "segments": [s.to_dict() for s in self.segments],
            "reply_token": self.reply_token,
        }


# ---------------- 动作 ----------------

ACT_SEND_MESSAGE = "send_message"
ACT_SEND_PRIVATE = "send_private"
ACT_SEND_GROUP = "send_group"
ACT_RECALL = "recall_message"
ACT_SET_GROUP_BAN = "set_group_ban"
ACT_SET_GROUP_CARD = "set_group_card"
ACT_GET_GROUP_MEMBERS = "get_group_members"
ACT_GET_GROUP_INFO = "get_group_info"
ACT_GET_STRANGER = "get_stranger_info"
ACT_UPLOAD_FILE = "upload_file"


@dataclass(slots=True)
class ActionResult:
    """动作结果（统一返回给调用方/插件）"""

    ok: bool
    data: Any = None
    error: str = ""

    @classmethod
    def success(cls, data: Any = None) -> "ActionResult":
        return cls(ok=True, data=data)

    @classmethod
    def fail(cls, error: str) -> "ActionResult":
        return cls(ok=False, error=error)

    def to_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "data": self.data, "error": self.error}


def seg_text(content: str) -> Segment:
    return Segment(SEG_TEXT, {"text": content})


def seg_at(qq: int | str) -> Segment:
    return Segment(SEG_AT, {"qq": str(qq)})


def seg_image(file: str) -> Segment:
    return Segment(SEG_IMAGE, {"file": file})


def segs_to_list(segs: list[Segment]) -> list[dict]:
    return [s.to_dict() for s in segs]
