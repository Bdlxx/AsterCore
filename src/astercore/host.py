# 栖星 AsterCore · plugin-host 子进程
# 用法：python -m astercore.host --lib <DLL/SO路径> [--config <json>]
# 协议（stdin/stdout，JSON 一行一帧）：
#   主进程 → host: {"type":"init","config":{}} / {"type":"event","data":Event} /
#                  {"type":"action_result","id":N,"data":{...}} / {"type":"stop"}
#   host → 主进程: {"type":"ready"} / {"type":"action","id":N,"action":{...}} /
#                  {"type":"log","level":..,"msg":..} / {"type":"exited","code":N}
# 单线程命令循环：处理 event 时若插件调用 api->action，写 stdout 请求后
# 继续读 stdin 直到对应 action_result（中途到达的其它帧入 pending 队列）。

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from astercore.native import NativePlugin  # noqa: E402

# 全局：等待中的 action id 与结果（单线程内同步等待用）
_action_id = 0
_pending: list[dict] = []  # 等待 result 期间到达的其它帧


def _send(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _readline() -> dict | None:
    line = sys.stdin.readline()
    if not line:
        return None
    try:
        return json.loads(line)
    except json.JSONDecodeError:
        return None


class HostAction:
    """插件 api->action 的 host 实现：转发请求、同步等主进程回结果。

    经 NativePlugin._ApiImpl 转换后，回调签名是 (req: dict) -> dict。
    """

    def __init__(self) -> None:
        self.next_id = 0

    def __call__(self, req: dict) -> dict:
        global _pending
        self.next_id += 1
        fid = self.next_id
        _send({"type": "action", "id": fid, "action": req})
        # 同步等待 action_result
        while True:
            frame = _readline()
            if frame is None:
                return {"ok": False, "error": "host stdin 关闭"}
            if frame.get("type") == "action_result" and frame.get("id") == fid:
                return frame.get("data", {})
            if frame.get("type") == "stop":
                return {"ok": False, "error": "stopped"}
            _pending.append(frame)  # 其它帧入队稍后处理


def host_log(level: int, msg: str) -> None:
    _send({"type": "log", "level": level,
           "msg": (msg or "").replace("\n", " ")[:500]})


def main() -> int:
    ap = argparse.ArgumentParser(prog="astercore.host")
    ap.add_argument("--lib", required=True, help="原生插件库路径")
    ap.add_argument("--config", default="{}")
    args = ap.parse_args()

    action = HostAction()
    plugin = NativePlugin(args.lib, action_cb=action)

    def do_init(config: dict) -> None:
        plugin.init(config)

    def do_event(ev: dict) -> None:
        from astercore.core.models import Event, Segment
        segs = [Segment(s.get("type"), s.get("data", {}))
                for s in ev.get("segments", [])]
        event = Event(
            type=ev.get("type", "message"),
            account_id=ev.get("account_id", ""),
            platform=ev.get("platform", "native"),
            time=int(ev.get("time") or 0),
            user_id=ev.get("user_id"),
            group_id=ev.get("group_id"),
            self_id=ev.get("self_id"),
            message_type=ev.get("message_type"),
            raw=ev.get("raw", ""),
            segments=segs,
        )
        handled = plugin.handle_event(event)
        _send({"type": "handled", "handled": handled})

    # 初始化
    try:
        do_init(json.loads(args.config or "{}"))
    except Exception as e:
        _send({"type": "log", "level": 3, "msg": f"host 初始化失败: {e}"})
        return 2
    _send({"type": "ready"})

    # 命令循环
    while True:
        frame = _pending.pop(0) if _pending else _readline()
        if frame is None:
            break
        t = frame.get("type")
        if t == "init":
            do_init(frame.get("config", {}))
        elif t == "event":
            try:
                do_event(frame.get("data", {}))
            except Exception as e:
                _send({"type": "log", "level": 3, "msg": f"事件处理异常: {e}"})
        elif t == "action_result":
            pass  # 已被 action 等待消费；孤立结果忽略
        elif t == "stop":
            break
    plugin.unload()
    _send({"type": "exited", "code": 0})
    return 0


if __name__ == "__main__":
    sys.exit(main())
