# 栖星 AsterCore · 统一 Action → OneBot v11 动作映射
# 说明：message 参数在 OneBot 中允许段数组（text/image/video/...）或纯文本。

from __future__ import annotations

from typing import Any

# 统一 action 名 → (onebot action, params 转换)
# 返回 None 表示不支持


def map_action(action: str, params: dict[str, Any]) -> dict[str, Any] | None:
    m = {
        "send_private": ("send_private_msg", ["user_id", "message"]),
        "send_group": ("send_group_msg", ["group_id", "message"]),
        "send_message": ("send_msg", ["message_type", "user_id", "group_id", "message"]),
        "recall_message": ("delete_msg", ["message_id"]),
        "set_group_ban": ("set_group_ban", ["group_id", "user_id", "duration"]),
        "set_group_card": ("set_group_card", ["group_id", "user_id", "card"]),
        "get_group_members": ("get_group_member_list", ["group_id"]),
        "get_group_info": ("get_group_info", ["group_id"]),
        "get_stranger_info": ("get_stranger_info", ["user_id"]),
        "get_group_list": ("get_group_list", []),
        "get_friend_list": ("get_friend_list", []),
        "get_login_info": ("get_login_info", []),
        "upload_file": ("upload_group_file" if params.get("target", {}).get("type") == "group"
                        else "upload_private_file", None),
    }
    if action not in m:
        return None
    ob_action, keys = m[action]
    out_params: dict[str, Any] = {}
    if action == "upload_file":
        target = params.get("target", {})
        if target.get("type") == "group":
            out_params["group_id"] = target.get("id")
        else:
            out_params["user_id"] = target.get("id")
        out_params["file"] = params.get("file")
        out_params["name"] = params.get("name", "file")
        return {"action": ob_action, "params": out_params, "wait": True}
    for k in keys:
        if k in params and params[k] is not None:
            out_params[k] = params[k]
    # message 段数组直接透传（OneBot 原生支持段数组）
    if "message" in params and "message" not in out_params:
        out_params["message"] = params["message"]
    return {"action": ob_action, "params": out_params, "wait": True}
