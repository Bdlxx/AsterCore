# 栖星 AsterCore · 原生插件 host 代理（主进程侧）
# 负责 spawn plugin-host 子进程、维护 JSON 行协议、崩溃检测与重启位。
# 对上层提供与 NativePlugin 相近的接口（init/handle_event/unload/meta）。

from __future__ import annotations

import asyncio
import json
import logging
import subprocess
import sys
import time
import threading
from pathlib import Path
from typing import Any, Callable

log = logging.getLogger("astercore.nativehost")


class HostCrashError(RuntimeError):
    pass


def _as_dict(res) -> dict:
    if hasattr(res, "to_dict"):
        return res.to_dict()
    if isinstance(res, dict):
        return res
    return {"ok": bool(res)}


class NativeHostProxy:
    """子进程隔离的原生插件宿主代理。

    action_cb: fn(req: dict) -> dict —— 主进程执行统一动作并返回结果
    lib_path:  原生插件库（.so/.dll）
    """

    def __init__(self, lib_path: str | Path, action_cb,
                 host_args: list[str] | None = None,
                 loop: Any | None = None,
                 auto_restart: bool = True,
                 restart_attempts: int = 3) -> None:
        """action_cb 可为同步函数或 async（awaitable）。

        auto_restart: host 意外退出时指数退避自动重启（0.5s/1s/1.5s…），
        达到 restart_attempts 仍失败则标记 _crashed（上层可禁用插件）。"""
        self.lib_path = Path(lib_path)
        self._action_cb = action_cb
        self._host_args = host_args or []
        self._loop = loop
        self._send_lock = threading.Lock()
        self._auto_restart = auto_restart
        self._restart_attempts = max(1, restart_attempts)
        self._init_config: dict[str, Any] = {}
        self.crash_count = 0
        self._crashed = False
        self._proc: subprocess.Popen | None = None
        self._lock = threading.Lock()
        self._pending_results: dict[int, dict] = {}
        self._action_seq = 0
        self._reader: threading.Thread | None = None
        self._stopping = False
        self._ready = False
        self._handled_flag: bool | None = None
        self._handled_future: Any | None = None
        self.exit_code: int | None = None

    # ---------- 生命周期 ----------
    def start(self) -> None:
        with self._lock:
            if self._proc is not None:
                return
            # 新进程启动前清空旧状态（否则 wait_ready 会误用上次崩溃的 exit_code）
            self.exit_code = None
            self._ready = False
            self._crashed = False
            cmd = [sys.executable, "-m", "astercore.host",
                   "--lib", str(self.lib_path), *self._host_args]
            self._proc = subprocess.Popen(
                cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=None, text=True, encoding="utf-8", bufsize=1,
            )
            self._reader = threading.Thread(target=self._read_loop,
                                            daemon=True,
                                            name=f"host-{self.lib_path.name}")
            self._reader.start()

    def wait_ready(self, timeout: float = 10.0) -> bool:
        """等 host 报 ready（init 完成）"""
        deadline = __import__("time").time() + timeout
        while __import__("time").time() < deadline:
            if self.exit_code is not None:
                raise HostCrashError(f"plugin-host 提前退出: {self.exit_code}")
            with self._lock:
                if self._ready:
                    return True
            __import__("time").sleep(0.05)
        raise HostCrashError("plugin-host ready 超时")

    def _restart_worker(self) -> None:
        """指数退避重启 host；成功则继续服务，达上限标记崩溃"""
        for i in range(self._restart_attempts):
            if self._stopping:
                return
            delay = 0.5 * (i + 1)
            log.info("host %s %s后重启 (第%d/%d次)", self.lib_path.name,
                     delay, i + 1, self._restart_attempts)
            time.sleep(delay)
            if self._stopping:
                return
            with self._lock:
                self._proc = None
                self._ready = False
            try:
                self._start_and_init(dict(self._init_config))
            except Exception as e:
                log.warning("host 重启失败 (%s)", e)
                continue
            self.crash_count += 1
            log.info("host %s 已自动重启（累计崩溃 %d 次）",
                     self.lib_path.name, self.crash_count)
            return
        log.error("host %s 连续 %d 次重启失败，标记崩溃（可由上层禁用）",
                  self.lib_path.name, self._restart_attempts)
        self._crashed = True

    def stop(self) -> None:
        self._stopping = True
        proc = self._proc
        if proc is not None:
            try:
                self._send({"type": "stop"})
            except Exception:
                pass
            try:
                proc.wait(timeout=3)
            except Exception:
                proc.kill()
        # 关闭管道避免资源泄漏（ResourceWarning）
        try:
            if proc.stdin:
                proc.stdin.close()
        except Exception:
            pass
        try:
            if proc.stdout:
                proc.stdout.close()
        except Exception:
            pass
        with self._lock:
            self._proc = None

    # ---------- 与 NativePlugin 同构接口 ----------
    def init(self, config: dict[str, Any]) -> None:
        """start + init + 等 ready"""
        self._init_config = config or {}
        self._start_and_init(self._init_config)

    def _start_and_init(self, cfg: dict[str, Any]) -> None:
        self.start()
        self._send({"type": "init", "config": cfg})
        try:
            self.wait_ready()
        except HostCrashError:
            # 清理半启动状态（供重启重试）
            with self._lock:
                self._proc = None
                self._ready = False
            raise

    async def handle_event_async(self, ev: dict) -> bool:
        """发事件给 host，异步等待 handled 帧（不阻塞事件循环）。"""
        if self.exit_code is not None:
            raise HostCrashError(f"host 已退出: {self.exit_code}")
        with self._lock:
            if not self._ready:
                raise HostCrashError("host 未就绪")
        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        self._handled_future = fut
        self._send({"type": "event", "data": ev})
        try:
            return await asyncio.wait_for(fut, timeout=15)
        except asyncio.TimeoutError:
            raise HostCrashError("host 事件处理超时") from None
        finally:
            if self._handled_future is fut:
                self._handled_future = None

    def handle_event(self, ev: dict) -> bool:
        """同步等待版（仅供非事件循环线程调用；事件循环线程请用 handle_event_async）"""
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            raise RuntimeError("事件循环线程请使用 handle_event_async")
        if self.exit_code is not None:
            raise HostCrashError(f"host 已退出: {self.exit_code}")
        with self._lock:
            if not self._ready:
                raise HostCrashError("host 未就绪")
        self._send({"type": "event", "data": ev})
        deadline = __import__("time").time() + 15
        while __import__("time").time() < deadline:
            with self._lock:
                if self._handled_flag is not None:
                    h = self._handled_flag
                    self._handled_flag = None
                    return h
            __import__("time").sleep(0.01)
        raise HostCrashError("host 事件处理超时")

    # ---------- 内部 ----------
    def _send(self, obj: dict) -> None:
        proc = self._proc
        if proc is None or proc.stdin is None:
            raise HostCrashError("host 未启动")
        try:
            with self._send_lock:
                proc.stdin.write(json.dumps(obj, ensure_ascii=False) + "\n")
                proc.stdin.flush()
        except (BrokenPipeError, OSError) as e:
            raise HostCrashError(f"host 管道断开: {e}") from e

    def _read_loop(self) -> None:
        proc = self._proc
        if proc is None or proc.stdout is None:
            return
        try:
            for line in proc.stdout:
                try:
                    frame = json.loads(line)
                except json.JSONDecodeError:
                    continue
                self._on_frame(frame)
        except Exception as e:
            log.debug("host 读循环结束: %s", e)
        finally:
            rc = proc.poll()
            self.exit_code = rc
            self._crashed = False
            log.warning("plugin-host 退出 code=%s", rc)
            if not self._stopping and self._auto_restart:
                threading.Thread(target=self._restart_worker, daemon=True,
                                 name=f"host-restart-{self.lib_path.name}").start()

    def _on_frame(self, frame: dict) -> None:
        t = frame.get("type")
        if t == "ready":
            with self._lock:
                self._ready = True
        elif t == "log":
            lv = {0: 10, 1: 20, 2: 30, 3: 40}.get(frame.get("level"), 20)
            log.log(lv, "[host] %s", frame.get("msg", ""))
        elif t == "action":
            # 插件请求动作：异步执行（不阻塞读线程，避免与 host 互相等待），完成回调回包
            aid = frame.get("id")
            req = frame.get("action", {})
            try:
                ret = self._action_cb(req)
                if asyncio.iscoroutine(ret):
                    loop = self._loop
                    if loop is None or not loop.is_running():
                        loop = asyncio.new_event_loop()
                        asyncio.set_event_loop(loop)
                    def _done(fut):
                        try:
                            res = fut.result()
                        except Exception as e:
                            res = {"ok": False, "error": str(e)}
                        self._send({"type": "action_result", "id": aid,
                                    "data": _as_dict(res)})
                    asyncio.run_coroutine_threadsafe(ret, loop).add_done_callback(_done)
                else:
                    self._send({"type": "action_result", "id": aid,
                                "data": _as_dict(ret)})
            except Exception as e:
                self._send({"type": "action_result", "id": aid,
                            "data": {"ok": False, "error": str(e)}})
        elif t == "handled":
            h = bool(frame.get("handled"))
            fut = self._handled_future
            if fut is not None and not fut.done():
                self._loop.call_soon_threadsafe(fut.set_result, h)
            with self._lock:
                self._handled_flag = h
        elif t == "exited":
            self.exit_code = frame.get("code")
