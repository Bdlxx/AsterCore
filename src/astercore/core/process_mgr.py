# 栖星 AsterCore · 子进程托管（通用）
# 供 lagrange（内置协议直登）/ napcat（可选组件）等后端进程托管：
# 启动/守护/日志落盘/崩溃退避重启/退出清理。
# 进程二进制未打包时调用方负责检查可执行文件存在。

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger("astercore.proc")


@dataclass(slots=True)
class ProcSpec:
    """托管进程描述"""

    name: str                     # 如 lagrange-2551736206
    executable: str               # 可执行文件路径（不存在则 start 失败）
    args: list[str] = field(default_factory=list)
    cwd: str | Path | None = None
    env: dict[str, str] = field(default_factory=dict)
    log_file: str | Path | None = None   # stdout/stderr 落盘
    auto_restart: bool = True
    restart_attempts: int = 3
    start_delay: float = 0.0


class ProcessManager:
    """进程托管：start/stop/status/restart（退避）"""

    def __init__(self, spec: ProcSpec) -> None:
        self.spec = spec
        self.proc: subprocess.Popen | None = None
        self._lock = threading.Lock()
        self._stopping = False
        self._log_fh = None
        self.crash_count = 0
        self.last_error: str = ""

    # ---------- 生命周期 ----------
    def executable_exists(self) -> bool:
        exe = self.spec.executable
        if os.path.isabs(exe):
            return os.path.exists(exe)
        return shutil.which(exe) is not None

    def start(self) -> tuple[bool, str]:
        if self.is_alive():
            return True, "已在运行"
        if not self.executable_exists():
            return False, (f"未找到可执行文件: {self.spec.executable}（需先安装/下载，"
                           f"桌面版向导会引导）")
        self._stopping = False
        try:
            fh = None
            if self.spec.log_file:
                p = Path(self.spec.log_file)
                p.parent.mkdir(parents=True, exist_ok=True)
                fh = open(p, "a", encoding="utf-8")
            self._log_fh = fh
            self.proc = subprocess.Popen(
                [self.spec.executable, *self.spec.args],
                cwd=str(self.spec.cwd) if self.spec.cwd else None,
                env={**os.environ, **self.spec.env} if self.spec.env else None,
                stdout=fh, stderr=subprocess.STDOUT if fh else None,
            )
            log.info("进程 %s 已启动 pid=%s", self.spec.name, self.proc.pid)
            return True, f"已启动 pid={self.proc.pid}"
        except Exception as e:
            self.last_error = str(e)
            return False, f"启动失败: {e}"

    def is_alive(self) -> bool:
        p = self.proc
        return p is not None and p.poll() is None

    def stop(self) -> None:
        self._stopping = True
        p = self.proc
        if p is not None and p.poll() is None:
            p.terminate()
            try:
                p.wait(timeout=5)
            except Exception:
                p.kill()
        self.proc = None
        if self._log_fh:
            try:
                self._log_fh.close()
            except Exception:
                pass
            self._log_fh = None

    def restart(self) -> tuple[bool, str]:
        self.stop()
        time.sleep(0.5)
        return self.start()

    def wait(self, timeout: float = 0.0):
        """进程退出码（timeout=0 不等待）"""
        p = self.proc
        if p is None:
            return None
        try:
            return p.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            return None

    # ---------- 崩溃监控（可由外层线程定期调用/或回调） ----------
    def poll_crash(self) -> int | None:
        """若进程意外退出（非主动停），返回退出码并累计崩溃；否则 None"""
        p = self.proc
        if p is None or self._stopping:
            return None
        rc = p.poll()
        if rc is not None:
            self.crash_count += 1
            self.last_error = f"进程退出 code={rc}"
            self.proc = None
            log.warning("进程 %s 意外退出 code=%s（累计 %d）",
                        self.spec.name, rc, self.crash_count)
        return rc
