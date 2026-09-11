# 栖星 AsterCore · 事件/消息双向转换（AsterCore ↔ OneBot v11 原始形态）
# 老插件是围绕 NapCat 原始 dict 写的：raw_message 里含 CQ 码、message 是段数组、
# 还依赖 sender/message_id/sub_type 等字段。这里保证两个方向都无损耗。

from __future__ import annotations

import re
from typing import Any

from astercore.core.models import Event, Segment, seg_text

# [CQ:type,key=value,key=value]  —— 值内不出现 ] 与 ,（与 OneBot 实现一致）
_CQ_RE = re.compile(r"\[CQ:([a-zA-Z_][\w]*)((?:,[^,\]]+)*)\]")
_CQ_KV_RE = re.compile(r",([^,=]+)=([^,]*)")


def _unescape(s: str) -> str:
    return (s.replace("&#44;", ",").replace("&#91;", "[")
             .replace("&#93;", "]").replace("&amp;", "&"))


def _escape(s: str) -> str:
    return (s.replace("&", "&amp;").replace(",", "&#44;")
             .replace("[", "&#91;").replace("]", "&#93;"))


def cq_to_segments(text: str) -> list[Segment]:
    """CQ 码字符串 → 消息段列表（普通文本段保留）"""
    out: list[Segment] = []
    pos = 0
    for m in _CQ_RE.finditer(text or ""):
        if m.start() > pos:
            piece = text[pos:m.start()]
            if piece:
                out.append(seg_text(piece))
        typ = m.group(1)
        data: dict[str, Any] = {}
        for k, v in _CQ_KV_RE.findall(m.group(2) or ""):
            data[k.strip()] = _unescape(v)
        out.append(Segment(type=typ, data=data))
        pos = m.end()
    if pos < len(text or ""):
        rest = text[pos:]
        if rest:
            out.append(seg_text(rest))
    return out


def segments_to_cq(segments: list[Any]) -> str:
    """消息段列表（Segment 或 OneBot dict）→ CQ 码字符串"""
    parts: list[str] = []
    for s in segments or []:
        typ, data = _seg_pair(s)
        if typ == "text":
            parts.append(str(data.get("text", "")))
            continue
        kv = "".join(f",{k}={_escape(str(v))}" for k, v in data.items())
        parts.append(f"[CQ:{typ}{kv}]")
    return "".join(parts)


def _seg_pair(seg: Any) -> tuple[str, dict]:
    if isinstance(seg, Segment):
        return seg.type, dict(seg.data)
    if isinstance(seg, dict):
        return str(seg.get("type", "text")), dict(seg.get("data") or {})
    return "text", {"text": str(seg)}


def segments_to_onebot_list(segments: list[Any]) -> list[dict]:
    """统一转成 OneBot 段数组形态（老插件/OneBot 原生都吃这个）"""
    out = []
    for s in segments or []:
        typ, data = _seg_pair(s)
        out.append({"type": typ, "data": data})
    return out


def normalize_message(message: Any) -> list[Segment]:
    """把老插件可能的三种消息形态统一成内核消息段列表。

    - str            → CQ 码解析（老插件大量使用 "[CQ:image,file=...]"）
    - list[dict]     → OneBot 段数组（原样映射）
    - list[Segment]  → 已规范
    """
    if message is None:
        return []
    if isinstance(message, str):
        return cq_to_segments(message)
    if isinstance(message, Segment):
        return [message]
    out: list[Segment] = []
    for s in message:
        typ, data = _seg_pair(s)
        out.append(Segment(type=typ, data=data))
    return out


def event_to_onebot(event: Event) -> dict[str, Any]:
    """AsterCore Event → OneBot v11 事件 dict（老插件契约）。

    优先使用后端保留的原始报文（零损耗：sender/message_id/sub_type 全在），
    没有原始报文时按标准字段重建（dry-run/其他后端场景）。
    """
    raw = None
    if isinstance(event.extra, dict):
        raw = event.extra.get("_onebot")
    if isinstance(raw, dict):
        d = dict(raw)
        # 补上多账号需要的字段（内核视角），其余保持 NapCat 原样
        d.setdefault("self_id", event.self_id)
        if event.account_id is not None:
            d["account_id"] = event.account_id
        return d

    segs = segments_to_onebot_list(event.segments)
    d = {
        "post_type": "message" if event.type == "message" else event.type,
        "message_type": event.message_type or ("group" if event.group_id else "private"),
        "time": event.time,
        "self_id": event.self_id,
        "user_id": event.user_id,
        "group_id": event.group_id,
        "raw_message": event.raw or segments_to_cq(event.segments),
        "message": segs,
        "message_id": event.extra.get("message_id") if isinstance(event.extra, dict) else None,
        "account_id": event.account_id,
    }
    # 老插件会读 sender.card / sender.nickname / sender.role
    sender = event.extra.get("sender") if isinstance(event.extra, dict) else None
    d["sender"] = dict(sender) if isinstance(sender, dict) else {
        "user_id": event.user_id, "nickname": "", "card": "", "role": "member",
    }
    if event.group_id:
        d["sub_type"] = "normal"
    return d


def event_text(event: Event, onebot: dict[str, Any] | None = None) -> str:
    """老插件常用 event['raw_message']；缺失时用段内容兜底"""
    if onebot and onebot.get("raw_message"):
        return str(onebot["raw_message"])
    if event.raw:
        return event.raw
    return event.text()
