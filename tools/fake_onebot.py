# 栖星 AsterCore · fake OneBot 服务端（动作回执断言模式）
# 用法：
#   python tools/fake_onebot.py --port 9901               # 基本模式（打印 API）
#   python tools/fake_onebot.py --port 9901 --expect send_group_msg,send_private_msg
#      --expect 逗号分隔动作列表：仅当收到列表内动作时才回 ok；其它回 err
#      --no-push：不推送模拟消息（纯动作断言用）
#      （用于"后端动作全映射"端到端断言）

from __future__ import annotations

import argparse
import json

from aiohttp import web


class FakeOneBot:
    def __init__(self, self_id: str, expect: list[str] | None = None,
                 push: bool = True) -> None:
        self.self_id = self_id
        self.expect = expect          # 期望收到的动作（None=全部回 ok）
        self.received: list[str] = []  # 收到的动作名（供主进程查询）
        self.push = push

    async def ws_handler(self, request: web.Request):
        ws = web.WebSocketResponse(heartbeat=10.0)
        await ws.prepare(request)
        print("[fake-onebot] 客户端已连接", flush=True)
        if self.push:
            await ws.send_str(json.dumps({
                "post_type": "message", "message_type": "group",
                "group_id": 987654321, "user_id": 555000111,
                "self_id": int(self.self_id), "time": 1700000000,
                "raw_message": "你好",
                "message": [{"type": "text", "data": {"text": "你好"}}],
            }, ensure_ascii=False))
        async for msg in ws:
            if msg.type == web.WSMsgType.ERROR:
                print(f"[fake-onebot] WS 错误: {msg.data}", flush=True)
                continue
            if msg.type != web.WSMsgType.TEXT:
                continue
            try:
                req = json.loads(msg.data)
            except Exception:
                continue
            action = req.get("action")
            params = req.get("params", {})
            echo = req.get("echo")
            self.received.append(action)
            print(f"[fake-onebot] API: {action} "
                  f"{json.dumps(params, ensure_ascii=False)[:150]}", flush=True)
            ok = self.expect is None or action in self.expect
            data = {"fake": True, "action": action, "params": params}
            # 群列表 / 群成员假数据（供面板调试台演示）
            if action == "get_group_list":
                data = [{"group_id": 987654321, "group_name": "假群·测试A",
                         "member_count": 10},
                        {"group_id": 555111222, "group_name": "假群·测试B",
                         "member_count": 3}]
            elif action == "get_group_member_list":
                data = [{"user_id": 555000111, "nickname": "群友甲",
                         "card": "甲"},
                        {"user_id": 10001, "nickname": "群友乙"}]
            elif action == "get_login_info":
                data = {"user_id": int(self.self_id), "nickname": "假Bot"}
            await ws.send_str(json.dumps({
                "status": "ok" if ok else "failed",
                "retcode": 0 if ok else -1,
                "data": data,
                "message": "" if ok else f"unexpected action {action}",
                "echo": echo,
            }))
        print("[fake-onebot] 客户端断开", flush=True)
        return ws

    def app(self) -> web.Application:
        a = web.Application()
        a.router.add_get("/", self.ws_handler)
        a.router.add_get("/ws", self.ws_handler)
        return a


def main() -> None:
    ap = argparse.ArgumentParser(description="假 OneBot WS 服务端（联调/断言）")
    ap.add_argument("--port", type=int, default=9901)
    ap.add_argument("--self", default="740979632")
    ap.add_argument("--expect", default=None, help="逗号分隔：仅这些动作回 ok")
    ap.add_argument("--no-push", action="store_true", help="不推送模拟消息")
    args = ap.parse_args()
    expect = [a.strip() for a in args.expect.split(",")] if args.expect else None
    print(f"[fake-onebot] 监听 ws://127.0.0.1:{args.port} expect={expect or 'all'}",
          flush=True)
    web.run_app(FakeOneBot(args.self, expect=expect, push=not args.no_push).app(),
                host="127.0.0.1", port=args.port, print=None)


if __name__ == "__main__":
    main()
