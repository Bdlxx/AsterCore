# 假 OneBot v11 服务端（联调用，无线上风险）
# 模拟 NapCat 正向 WebSocket：
#   - 接受 WS 连接
#   - 收到 API 调用（action/params/echo）→ 打印并回 {"status":"ok",...}
#   - 连接建立后推送一条模拟群消息（文本"你好"，触发 demo 插件）
# 用法：python tools/fake_onebot.py --port 9901 --self 740979632

from __future__ import annotations

import argparse
import asyncio
import json

from aiohttp import web


class FakeOneBot:
    def __init__(self, self_id: str) -> None:
        self.self_id = self_id
        self.seq = 0

    async def ws_handler(self, request: web.Request):
        ws = web.WebSocketResponse(heartbeat=10.0)
        await ws.prepare(request)
        print("[fake-onebot] 客户端已连接", flush=True)

        # 推送一条模拟群消息
        await ws.send_str(json.dumps({
            "post_type": "message",
            "message_type": "group",
            "group_id": 987654321,
            "user_id": 555000111,
            "self_id": int(self.self_id),
            "time": 1700000000,
            "raw_message": "你好",
            "message": [{"type": "text", "data": {"text": "你好"}}],
        }, ensure_ascii=False))

        async for msg in ws:
            print(f"[fake-onebot] 收到帧 type={msg.type} data={getattr(msg, 'data', None)!r}", flush=True)
            if msg.type == web.WSMsgType.ERROR:
                print(f"[fake-onebot] WS 错误: {msg.data}", flush=True)
                continue
            if msg.type != web.WSMsgType.TEXT:
                continue
            try:
                req = json.loads(msg.data)
                action = req.get("action")
                params = req.get("params", {})
                echo = req.get("echo")
                print(f"[fake-onebot] API: {action} params={json.dumps(params, ensure_ascii=False)[:120]}", flush=True)
                await ws.send_str(json.dumps({
                    "status": "ok", "retcode": 0,
                    "data": {"fake": True, "action": action},
                    "echo": echo,
                }))
            except Exception as e:
                print(f"[fake-onebot] 处理异常: {type(e).__name__}: {e}", flush=True)
                import traceback; traceback.print_exc()
        print("[fake-onebot] 客户端断开")
        return ws

    def app(self) -> web.Application:
        a = web.Application()
        a.router.add_get("/", self.ws_handler)
        a.router.add_get("/ws", self.ws_handler)
        return a


def main() -> None:
    ap = argparse.ArgumentParser(description="假 OneBot WS 服务端（联调）")
    ap.add_argument("--port", type=int, default=9901)
    ap.add_argument("--self", default="740979632", help="机器人 self_id")
    args = ap.parse_args()
    print(f"[fake-onebot] 监听 ws://127.0.0.1:{args.port}（self_id={args.self}）", flush=True)
    web.run_app(FakeOneBot(args.self).app(), host="127.0.0.1",
                port=args.port, print=None)


if __name__ == "__main__":
    main()
