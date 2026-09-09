# 栖星 AsterCore · 原生插件 host 代理（主进程侧）
# 负责 spawn plugin-host 子进程、维护 JSON 行协议、崩溃检测与重启位。
# 对上层提供与 NativePlugin 相近的接口（init/handle_event/unload/meta）。

from __future__ import annotations

import json
import logging
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any, Callable

log = logging.getLogger("astercore.nativehost")


class HostCrashError(RuntimeError):
    pass


class NativeHostProxy:
    """子进程隔离的原生插件宿主代理。

    action_cb: fn(req: dict) -> dict —— 主进程执行统一动作并返回结果
    lib_path:  原生插件库（.so/.dll）
    """

    def __init__(self, lib_path: str | Path, action_cb: Callable[[dict], dict],
                 host_args: list[str] | None = None) -> None:
        self.lib_path = Path(lib_path)
        self._action_cb = action_cb
        self._host_args = host_args or []
        self._proc: subprocess.Popen | None = None
        self._lock = threading.Lock()
        self._pending_results: dict[int, dict] = {}
        self._action_seq = 0
        self._reader: threading.Thread | None = None
        self._stopping = False
        self._ready = False
        self._handled_flag: bool | None = None
        self.exit_code: int | None = None

    # ---------- 生命周期 ----------
    def start(self) -> None:
        with self._lock:
            if self._proc is not None:
                return
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
        with self._lock:
            self._proc = None

    # ---------- 与 NativePlugin 同构接口 ----------
    def init(self, config: dict[str, Any]) -> None:
        """start + init + 等 ready"""
        self.start()
        self._send({"type": "init", "config": config or {}})
        self.wait_ready()

    def handle_event(self, ev: dict) -> bool:
        """发事件给 host，等 handled 帧"""
        if self.exit_code is not None:
            raise HostCrashError(f"host 已退出: {self.exit_code}")
        with self._lock:
            if not self._ready:
                raise HostCrashError("host 未就绪")
        self._send({"type": "event", "data": ev})
        # 等待 handled：由 reader 线程设置（简化：轮询标记）
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
            log.warning("plugin-host 退出 code=%s", rc)

    def _on_frame(self, frame: dict) -> None:
        t = frame.get("type")
        if t == "ready":
            with self._lock:
                self._ready = True
        elif t == "log":
            lv = {0: 10, 1: 20, 2: 30, 3: 40}.get(frame.get("level"), 20)
            log.log(lv, "[host] %s", frame.get("msg", ""))
        elif t == "action":
            # 插件请求动作：主进程执行并回 action_result
            aid = frame.get("id")
            try:
                res = self._action_cb(frame.get("action", {}))
            except Exception as e:
                res = {"ok": False, "error": str(e)}
            self._send({"type": "action_result", "id": aid, "data": res})
        elif t == "handled":
            with self._lock:
                self._handled_flag = bool(frame.get("handled"))
        elif t == "exited":
            self.exit_code = frame.get("code")
