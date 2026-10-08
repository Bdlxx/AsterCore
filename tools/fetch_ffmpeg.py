#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""下载 Windows 版 ffmpeg 到 tools/ffmpeg/bin/（供随包分发）

为什么不让仓库直接带二进制：~80–100MB 的 exe 不该进 git；构建前落地更干净。
CI（.github/workflows/windows-build.yml）与开发机共用本脚本，只依赖标准库。

`bootstrap.find_ffmpeg()` 的查找顺序里第 2 条就是
`<exe 同级>/tools/ffmpeg/bin/ffmpeg.exe`，所以放到这里就够了，插件一行都不用改。

用法：
    python tools/fetch_ffmpeg.py                 # 下到 <repo>/tools/ffmpeg/bin
    python tools/fetch_ffmpeg.py --force         # 强制重下
    python tools/fetch_ffmpeg.py --dir D:\\x      # 换目标目录
"""
from __future__ import annotations

import argparse
import os
import sys
import urllib.request
import zipfile
from pathlib import Path


def _force_utf8_stdio() -> None:
    """把本进程 stdout/stderr 转成 UTF-8 + errors=replace。

    必须做：CI 的 Windows runner 控制台是 **cp1252**，本脚本会打印中文 →
    第一次 print 就 UnicodeEncodeError 把构建打死（2026-10-08 真实踩过，
    v0.3.0 第一次 CI 就挂在这一步）。项目里 `bootstrap.force_utf8_stdio()`
    对插件做的是同一件事。
    """
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")   # type: ignore[attr-defined]
        except Exception:                                        # noqa: BLE001
            pass


_force_utf8_stdio()

MIRRORS = [
    ("gyan.dev · release-essentials（稳定版）",
     "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"),
    ("BtbN · GitHub latest win64 gpl（备用）",
     "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/"
     "ffmpeg-master-latest-win64-gpl.zip"),
]
WANT = ("ffmpeg.exe",)      # 插件只用 ffmpeg（`video_parser_core/utils.py` 全是裸命令名
                            # "ffmpeg"），ffprobe 不用 —— 别多塞 105MB 进包


def _download(url: str, dest: Path) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": "astercore-fetch-ffmpeg/1"})
    with urllib.request.urlopen(req, timeout=180) as r, dest.open("wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        got = 0
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
            got += len(chunk)
            if total:
                print(f"\r  下载 {got / 1048576:6.1f}/{total / 1048576:.1f} MB", end="", flush=True)
    print()


def main() -> int:
    ap = argparse.ArgumentParser(description="下载 Windows 版 ffmpeg 到 tools/ffmpeg/bin/")
    ap.add_argument("--dir", default=None, help="目标目录（默认 <repo>/tools/ffmpeg/bin）")
    ap.add_argument("--force", action="store_true", help="已存在也重下")
    args = ap.parse_args()

    base = Path(__file__).resolve().parent.parent
    out = Path(args.dir).expanduser() if args.dir else base / "tools" / "ffmpeg" / "bin"
    out.mkdir(parents=True, exist_ok=True)

    if all((out / w).is_file() for w in WANT) and not args.force:
        print(f"已存在，跳过：{out}（要重下加 --force）")
        return 0

    tmp = out.parent / "_download.zip"
    ok = False
    for label, url in MIRRORS:
        try:
            print(f"尝试镜像：{label}\n  {url}")
            _download(url, tmp)
            with zipfile.ZipFile(tmp) as z:
                names = [n.replace("\\", "/") for n in z.namelist()]
                for want in WANT:
                    hit = next((n for n in names if n.endswith("/bin/" + want)), None) \
                        or next((n for n in names if n.endswith("/" + want)), None)
                    if hit is None:
                        raise RuntimeError(f"压缩包里找不到 {want}")
                    (out / want).write_bytes(z.read(hit))
                    print(f"  解出 {want}（{(out / want).stat().st_size / 1048576:.1f} MB）")
            ok = True
            break
        except Exception as e:                      # noqa: BLE001 — 换下一个镜像
            print(f"  失败：{type(e).__name__}: {e}")
        finally:
            tmp.unlink(missing_ok=True)

    if not ok:
        print("所有镜像都失败。可以手工下载 ffmpeg.exe（Windows 版）放到：" + str(out))
        return 1

    for w in WANT:                                  # 必须真是 Windows PE（MZ），别把错误页当成功
        p = out / w
        if not p.is_file() or p.open("rb").read(2) != b"MZ":
            print(f"校验失败：{p} 不是 Windows PE 文件")
            return 1
    print(f"完成：{out}  →  打包时会被放进 <exe 同级>/tools/ffmpeg/bin/")
    print(f"提示：构建机上再加 PATH 也行，但随包分发用户无需任何额外安装。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
