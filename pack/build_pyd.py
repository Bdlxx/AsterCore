# 栖星 AsterCore · Python 插件发布构建（.py → .pyd 防改）
# 用法：
#   python pack/build_pyd.py --plugin src/xxx.py [--out dist/plugins]
#   python pack/build_pyd.py --dir plugins/          # 批量
# 产物：xxx.pyd（CPython x64 原生扩展；Loader 按 .pyd 优先于 .py，用户无法直接改源码）
# 注意：.pyd 绑定 Python 版本与架构，需与主程序一致（默认 CPython 3.12 x64）。

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path


def ensure_cython() -> None:
    try:
        import Cython  # noqa: F401
    except ImportError:
        print("[build] 安装 Cython…")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "cython"])


def build_one(src: Path, out_dir: Path) -> Path | None:
    """编译单个插件 .py → .pyd（.so）。源文件不进入输出目录。"""
    src = Path(src)
    out_dir.mkdir(parents=True, exist_ok=True)
    tmp_dir = out_dir / ".cytmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    try:
        c_file = tmp_dir / f"{src.stem}.c"
        subprocess.check_call([
            sys.executable, "-m", "cython", "-3",
            str(src), "-o", str(c_file),
        ])
        from setuptools import Extension, setup
        ext = Extension(src.stem, [str(c_file)])
        setup(name=src.stem, ext_modules=[ext],
              script_args=[
                  "build_ext", "--inplace",
                  "--build-lib", str(out_dir),
                  "--build-temp", str(tmp_dir),
              ])
        # 清理中间文件
        for p in list(out_dir.glob(f"{src.stem}*.o")) + \
                list(out_dir.glob(f"{src.stem}*.c")) + \
                list(out_dir.glob("*.pyd.manifest")):
            p.unlink(missing_ok=True)
        hits = list(out_dir.glob(f"{src.stem}*.pyd")) + \
               list(out_dir.glob(f"{src.stem}*.so"))
        if not hits:
            return None
        # 重命名去除平台/ABI 标签 → 插件目录里就叫 <name>.pyd / <name>.so
        # （我们的 Loader 用 spec_from_file_location 显式加载，不需要 ABI 文件名）
        target = out_dir / f"{src.stem}.pyd" if sys.platform == "win32" \
                 else out_dir / f"{src.stem}.so"
        target.unlink(missing_ok=True)
        hits[0].rename(target)
        return target
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def build_dir(plugins_dir: Path, out: Path) -> list[Path]:
    built = []
    for src in sorted(Path(plugins_dir).glob("*.py")):
        if src.name.startswith("_") or src.name == "__init__.py":
            continue
        r = build_one(src, out)
        if r:
            built.append(r)
            print(f"[build] {src.name} -> {r.name}")
    return built


def main() -> None:
    ap = argparse.ArgumentParser(description="Python 插件 .py -> .pyd 发布构建")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--plugin", help="单个插件 .py")
    g.add_argument("--dir", help="插件目录（批量）")
    ap.add_argument("--out", default="dist/plugins", help="输出目录")
    args = ap.parse_args()
    ensure_cython()
    out = Path(args.out)
    if args.plugin:
        r = build_one(Path(args.plugin), out)
        if r is None:
            print("[build] 失败")
            sys.exit(1)
        print(f"[build] 产物: {r}")
    else:
        rs = build_dir(Path(args.dir), out)
        print(f"[build] 完成 {len(rs)} 个；输出: {out.resolve()}")


if __name__ == "__main__":
    main()
