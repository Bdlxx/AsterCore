# 栖星 AsterCore · 可选组件（NapCat / Lagrange）定位·下载·配置·托管
#
# 这一层同时服务两件事：
#   ① 计划书 §3.1「NapCat 连接引导」——引导下载 → 自动写 onebot11 配置（127.0.0.1 + 自动端口
#      + 自动 token）→ 扫码登录 → 内核自动连接，用户零手动配置；
#   ② 计划书 Phase 4「内置协议（Lagrange · 可选实验后端）」——免装客户端，自拉子进程，
#      协议侧复用一个 OneBot v11 后端（见 backends/lagrange.py）。
#
# 设计要点（照计划书的红线）：
#   - **不捆绑**：都是"用户点一下 → 下载到 <base>/components/<key>/"，仓库与安装包不带二进制。
#   - **不覆盖用户文件**：写配置一律"读-改-写"，只动我们负责的几个键（端口/token），
#     其它键（尤其用户手工加的）原样保留 —— Linux 侧轮换 token 时踩过这个坑。
#   - **可离线**：install_from_zip() 既能下载也能吃用户已经下好的 zip（含 file:// 便于测试）。

from __future__ import annotations
import sys

import json
import logging
import os
import secrets
import shutil
import socket
import urllib.request
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

log = logging.getLogger("astercore.components")

# 组件根目录名（<base>/components/<key>/）：与 data/ accounts/ plugins/ 平级、都放 exe 旁边
COMPONENTS_DIRNAME = "components"

# 探测本机已有后端端点时按顺序试的 (ws_port, http_port)
DEFAULT_ENDPOINTS = ((3001, 3000), (3002, 3000), (6700, 5700))


# --------------------------------------------------------------------------- #
# 描述
# --------------------------------------------------------------------------- #
@dataclass
class Component:
    """一个可选组件（可下载、可托管、需要写配置）"""

    key: str
    label: str
    exe_candidates: tuple[str, ...]           # 相对组件目录；第一个存在即用它
    download_urls: tuple[str, ...] = ()       # 依次尝试（GitHub 优先）
    archive_name: str = ""                    # 下载落盘的临时文件名
    config_writer: Callable[..., Path] | None = None
    note: str = ""                            # 面板上给用户看的一句话
    args: tuple[str, ...] = ()                # 启动参数
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def dir_name(self) -> str:
        return self.key


def component_dir(base_dir: str | Path, key: str) -> Path:
    return Path(base_dir) / COMPONENTS_DIRNAME / key


def bundled_dir(key: str) -> Path | None:
    """软件**自带的**组件目录（PyInstaller 打包时随包分发）。

    ⚠ 为什么要查 `_MEIPASS`：onedir 会把 spec 的 datas 放进 `_internal/`，
    而 `sys._MEIPASS` 指向的就是 `_internal`。所以自带组件在
    `<_MEIPASS>/components/<key>`；源码运行时按 `<repo>/pack/components/<key>` 兜底。
    """
    cands: list[Path] = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        mp = Path(meipass)
        cands += [mp / "components" / key, mp.parent / "components" / key]
    # 源码运行（未打包）时也认 pack/components/<key>
    here = Path(__file__).resolve()
    for up in (here.parents[2] if len(here.parents) > 2 else here.parent,):
        cands.append(up / "pack" / "components" / key)
    for c in cands:
        try:
            if c.is_dir():
                return c
        except OSError:
            continue
    return None


def _scan_exe(root: Path, cmp: Component) -> Path | None:
    if not root.is_dir():
        return None
    for name in cmp.exe_candidates:
        direct = root / name
        if direct.is_file():
            return direct
    # 压缩包里常带一层 <pkg>/ 根目录 → 再扫一层
    for sub in sorted(p for p in root.iterdir() if p.is_dir()):
        for name in cmp.exe_candidates:
            cand = sub / name
            if cand.is_file():
                return cand
    return None


def find_executable(cmp: Component, base_dir: str | Path) -> Path | None:
    """先找用户数据目录里装的；找不到就找**软件自带**的那份（无需下载）"""
    found = _scan_exe(component_dir(base_dir, cmp.key), cmp)
    if found is not None:
        return found
    bundled = bundled_dir(cmp.key)
    if bundled is not None:
        return _scan_exe(bundled, cmp)
    return None


def is_installed(cmp: Component, base_dir: str | Path) -> bool:
    return find_executable(cmp, base_dir) is not None


def status(cmp: Component, base_dir: str | Path, running: bool | None = None) -> dict[str, Any]:
    """面板用状态快照（引导卡片就靠它渲染）"""
    root = component_dir(base_dir, cmp.key)
    exe = find_executable(cmp, base_dir)
    return {
        "key": cmp.key,
        "label": cmp.label,
        "dir": str(root),
        "installed": exe is not None,
        "executable": str(exe) if exe else "",
        "running": running,
        "note": cmp.note,
        "download_urls": list(cmp.download_urls),
    }


# --------------------------------------------------------------------------- #
# 端口 / token
# --------------------------------------------------------------------------- #
def free_port(preferred: int = 0, avoid: Iterable[int] = ()) -> int:
    """取一个空闲端口：先试 preferred，占用则让系统随便给一个"""
    avoid = set(avoid)
    for port in ([preferred] if preferred and preferred not in avoid else []) + [0]:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                s.bind(("127.0.0.1", port))
                got = s.getsockname()[1]
            if got not in avoid:
                return got
        except OSError:
            continue
    raise RuntimeError("找不到可用端口")


def random_token() -> str:
    return secrets.token_urlsafe(24)


# --------------------------------------------------------------------------- #
# 配置写入（一律"读-改-写"，保留用户其它键）
# --------------------------------------------------------------------------- #
def read_json(path: str | Path) -> dict[str, Any]:
    p = Path(path)
    if not p.is_file():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError) as e:
        log.warning("配置文件读取失败（按空处理）: %s — %s", p, e)
        return {}


def merge_write_json(path: str | Path, updates: dict[str, Any]) -> Path:
    """深合并写回：只覆盖 updates 里的键，其它原样保留；原子替换避免写坏"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    data = read_json(p)
    _deep_merge(data, updates)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, p)
    return p


def _deep_merge(dst: dict[str, Any], src: dict[str, Any]) -> dict[str, Any]:
    for k, v in src.items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            _deep_merge(dst[k], v)
        else:
            dst[k] = v
    return dst


def write_napcat_config(base_dir: str | Path, key_or_qq: str | int, ws_port: int,
                        http_port: int, token: str) -> Path:
    """写 NapCat 的 OneBot 配置：<组件目录>/config/onebot11_<QQ>.json

    老配置（用户手装的 NapCat 目录）里可能还有别的键 → 只改我们负责的
    网络/token 部分（`network.websocketServers[0]` / `httpServers[0]`）。
    """
    root = component_dir(base_dir, "napcat")
    path = root / "config" / f"onebot11_{key_or_qq}.json"
    cur = read_json(path)
    networks = cur.setdefault("network", {}) if cur else {}
    ws_list = networks.get("websocketServers") or [{}]
    ws_list[0] = _merge_server(ws_list[0], {
        "name": ws_list[0].get("name") or "astercore-ws",
        "enable": True, "host": "127.0.0.1", "port": ws_port,
        "messagePostFormat": "array", "reportSelfMessage": False,
        "token": token, "enableForcePushEvent": True, "debug": False,
        "heartInterval": 30000,
    })
    http_list = networks.get("httpServers") or [{}]
    http_list[0] = _merge_server(http_list[0], {
        "name": http_list[0].get("name") or "astercore-http",
        "enable": True, "host": "127.0.0.1", "port": http_port,
        "enableHeart": True, "enablePost": False, "messagePostFormat": "array",
        "token": token, "debug": False,
    })
    networks["websocketServers"] = ws_list
    networks["httpServers"] = http_list
    return merge_write_json(path, {"network": networks})


def _merge_server(old: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    out = dict(old or {})
    out.update(new)
    return out


def write_lagrange_config(base_dir: str | Path, ws_port: int, token: str,
                          http_port: int | None = None,
                          sign_server: str = "https://sign.lagrangecore.org/api/sign",
                          uin: int | str | None = None) -> Path:
    """写 Lagrange.OneBot 的 appsettings.json（ForwardWebSocket 正向 WS，内核直接连）

    ⚠ 只补"缺的那些键"：用户可能已经把某个 QQ 扫码登录进去了（`Account.Uin` 有值），
    无条件写成 0 会把登录态冲掉 —— 与 NapCat 写配置同一条红线（只改我们负责的键）。
    """
    root = component_dir(base_dir, "lagrange")
    path = root / "appsettings.json"
    impls: list[dict[str, Any]] = [{
        "Type": "ForwardWebSocket", "Host": "127.0.0.1", "Port": ws_port,
        "AccessToken": token, "HeartBeatInterval": 5,
    }]
    if http_port:
        impls.append({"Type": "Http", "Host": "127.0.0.1", "Port": http_port,
                      "AccessToken": token})

    cur = read_json(path)
    acct_cur = dict(cur.get("Account") or {})
    acct_updates: dict[str, Any] = {}
    if not acct_cur.get("Uin"):
        acct_updates["Uin"] = int(uin) if uin else 0
        acct_updates.setdefault("Password", "")
    if not acct_cur.get("Protocol"):
        acct_updates["Protocol"] = "Linux"
    if "AutoReconnect" not in acct_cur:
        acct_updates["AutoReconnect"] = True

    updates: dict[str, Any] = {
        "Logging": {"LogLevel": {"Default": "Information",
                                 "Microsoft": "Warning",
                                 "System.Net.Http.HttpClient": "Warning"}},
        "SignServerUrl": cur.get("SignServerUrl") or sign_server,
        "QrCode": {"ConsoleCompatibilityMode": True},
        "Implementations": impls,
    }
    if acct_updates:
        updates["Account"] = acct_updates
    return merge_write_json(path, updates)


# --------------------------------------------------------------------------- #
# 下载 / 安装
# --------------------------------------------------------------------------- #
def download(url: str, dest: str | Path, *, timeout: float = 60.0,
             progress: Callable[[int, int], None] | None = None) -> Path:
    """流式下载（stdlib；file:// 也支持，便于离线/测试）"""
    p = Path(dest)
    p.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": "astercore-guide/1"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (镜像固定)
        total = int(resp.headers.get("Content-Length") or 0)
        got = 0
        tmp = p.with_suffix(p.suffix + ".part")
        with open(tmp, "wb") as fh:
            while True:
                chunk = resp.read(262144)
                if not chunk:
                    break
                fh.write(chunk)
                got += len(chunk)
                if progress:
                    try:
                        progress(got, total)
                    except Exception:
                        pass
    os.replace(tmp, p)
    return p


def install_from_zip(cmp: Component, base_dir: str | Path, zip_path: str | Path) -> tuple[bool, str]:
    """把（已下载的）组件压缩包解到组件目录；解完必须能找到可执行文件"""
    root = component_dir(base_dir, cmp.key)
    src = Path(zip_path)
    if not src.is_file():
        return False, f"压缩包不存在: {src}"
    root.mkdir(parents=True, exist_ok=True)
    try:
        with zipfile.ZipFile(src) as zf:
            _safe_extract(zf, root)
    except zipfile.BadZipFile as e:
        return False, f"压缩包损坏: {e}"
    except OSError as e:
        return False, f"解压失败: {e}"
    exe = find_executable(cmp, base_dir)
    if exe is None:
        return False, (f"解压完成但没找到可执行文件（候选: {'/'.join(cmp.exe_candidates)}），"
                       f"请检查包结构：{root}")
    _chmod_exe(exe)
    return True, f"已安装: {exe}"


def _safe_extract(zf: zipfile.ZipFile, dest: Path) -> None:
    """防 zip-slip（路径逃逸）"""
    dest = dest.resolve()
    for member in zf.infolist():
        target = (dest / member.filename).resolve()
        if not str(target).startswith(str(dest)):
            raise OSError(f"压缩包里有越界路径: {member.filename}")
    zf.extractall(dest)


def _chmod_exe(path: Path) -> None:
    if os.name != "nt":
        try:
            path.chmod(path.stat().st_mode | 0o111)
        except OSError:
            pass


def flatten_single_root(root: Path) -> Path | None:
    """压缩包常见 `<pkg>/...`：把唯一子目录里的内容提到根，省得路径候选写两份"""
    if not root.is_dir():
        return None
    entries = [p for p in root.iterdir() if not p.name.startswith("__MACOSX")]
    subdirs = [p for p in entries if p.is_dir()]
    others = [p for p in entries if not p.is_dir()]
    if len(subdirs) == 1 and not others:
        inner = subdirs[0]
        for item in list(inner.iterdir()):
            shutil.move(str(item), str(root / item.name))
        inner.rmdir()
        return root
    return None


def provision(cmp: Component, base_dir: str | Path, *, url: str | None = None,
              zip_path: str | Path | None = None,
              progress: Callable[[int, int], None] | None = None) -> tuple[bool, str]:
    """安装组件：给 zip_path 就直接解压，否则按 url/镜像下载再解压（都失败则报清原因）"""
    root = component_dir(base_dir, cmp.key)
    if zip_path is None:
        candidates = [url] if url else list(cmp.download_urls)
        errors: list[str] = []
        for u in candidates:
            if not u:
                continue
            tmp = root.parent / (cmp.archive_name or f"{cmp.key}.zip")
            try:
                log.info("下载组件 %s: %s", cmp.key, u)
                download(u, tmp, progress=progress)
            except Exception as e:  # noqa: BLE001 每个镜像都要试
                errors.append(f"{u} → {type(e).__name__}: {e}")
                continue
            ok, msg = install_from_zip(cmp, base_dir, tmp)
            try:
                tmp.unlink()
            except OSError:
                pass
            if ok:
                flatten_single_root(root)
                return True, msg
            errors.append(f"{u} → {msg}")
        return False, "所有下载源都失败：" + "；".join(errors) if errors else "没有可用下载源"
    ok, msg = install_from_zip(cmp, base_dir, zip_path)
    if ok:
        flatten_single_root(root)
    return ok, msg


# --------------------------------------------------------------------------- #
# 本机已有后端探测（计划书 §3.1 场景 B/C）
# --------------------------------------------------------------------------- #
def probe_endpoint(http_url: str, token: str = "", timeout: float = 3.0) -> dict[str, Any]:
    """探测一个 OneBot HTTP 端点是否符合预期（引导页「测试连接」用）"""
    url = f"{http_url.rstrip('/')}/get_login_info"
    params = {"access_token": token} if token else {}
    try:
        import requests
        r = requests.get(url, params=params, timeout=timeout)
        j = r.json()
        if j.get("status") == "ok" and j.get("data"):
            return {"ok": True, "msg": "连接成功", "data": j["data"]}
        return {"ok": False, "msg": f"端点有响应但登录态异常: {j.get('message', '')[:80]}"}
    except Exception as e:  # noqa: BLE001 探测失败就是"没找到"
        return {"ok": False, "msg": f"连接失败: {type(e).__name__}: {e}"}


def discover_local(base_dir: str | Path) -> list[dict[str, Any]]:
    """本机已有 NapCat/Lagrange：先看组件目录，再看默认端口（自动发现）"""
    found: list[dict[str, Any]] = []
    for cmp in COMPONENTS.values():
        exe = find_executable(cmp, base_dir)
        if exe:
            found.append({"kind": cmp.key, "how": "component", "executable": str(exe)})
    for ws_port, http_port in DEFAULT_ENDPOINTS:
        res = probe_endpoint(f"http://127.0.0.1:{http_port}")
        if res["ok"]:
            found.append({"kind": "onebot", "how": "port", "ws_port": ws_port,
                          "http_port": http_port, "login": res.get("data")})
    return found


# --------------------------------------------------------------------------- #
# 组件表
# --------------------------------------------------------------------------- #
COMPONENTS: dict[str, Component] = {
    "napcat": Component(
        key="napcat",
        label="NapCat（官方 QQ 客户端 + 中间层）",
        exe_candidates=("NapCatWinBootMain.exe", "NapCatWinBootHook.exe",
                        "napcat.bat", "launcher.bat", "NapCat.Shell.exe"),
        download_urls=(
            "https://github.com/NapNeko/NapCatQQ/releases/latest/download/NapCat.Shell.Windows.Node.zip",
            "https://github.com/NapNeko/NapCatQQ/releases/latest/download/NapCat.Shell.zip",
        ),
        archive_name="napcat.zip",
        config_writer=write_napcat_config,
        note="走官方登录通道，风控风险最低；需要本机已装 QQ。",
    ),
    "lagrange": Component(
        key="lagrange",
        label="Lagrange 内置协议（实验）",
        exe_candidates=("Lagrange.OneBot.exe",),
        download_urls=(
            "https://github.com/LagrangeDev/Lagrange.Core/releases/download/nightly/"
            "Lagrange.OneBot_win-x64_net9.0_SelfContained.zip",
        ),
        archive_name="lagrange.zip",
        config_writer=write_lagrange_config,
        note="免装客户端，自带 .NET 运行时；非官方协议，建议小号。",
    ),
}


def get_component(key: str) -> Component:
    if key not in COMPONENTS:
        raise KeyError(f"未知组件: {key}（可选: {list(COMPONENTS)}）")
    return COMPONENTS[key]


# --------------------------------------------------------------------------- #
# 进程托管（复用 core.process_mgr；按组件 key 单例，避免两处各拉一个进程）
# --------------------------------------------------------------------------- #
from .core.process_mgr import ProcessManager, ProcSpec  # noqa: E402  (放底部避免环依赖)

_HOSTS: dict[str, ProcessManager] = {}


def host_log_path(base_dir: str | Path, key: str) -> Path:
    return component_dir(base_dir, key) / "run.log"


def host_for(cmp: Component, base_dir: str | Path) -> ProcessManager:
    """取（必要时创建）该组件的进程托管器；可执行文件不存在时不报错，start 时才报"""
    mgr = _HOSTS.get(cmp.key)
    if mgr is None or not mgr.executable_exists():
        exe = find_executable(cmp, base_dir)
        spec = ProcSpec(
            name=f"component-{cmp.key}",
            executable=str(exe or (component_dir(base_dir, cmp.key) / cmp.exe_candidates[0])),
            args=list(cmp.args),
            cwd=str(component_dir(base_dir, cmp.key)),
            log_file=str(host_log_path(base_dir, cmp.key)),
        )
        mgr = ProcessManager(spec)
        _HOSTS[cmp.key] = mgr
    return mgr


def host_status(cmp: Component, base_dir: str | Path) -> dict[str, Any]:
    mgr = _HOSTS.get(cmp.key)
    st = status(cmp, base_dir, running=bool(mgr and mgr.is_alive()))
    if mgr:
        st.update({"pid": mgr.proc.pid if mgr.is_alive() else None,
                   "crashes": mgr.crash_count,
                   "last_error": mgr.last_error,
                   "log": str(host_log_path(base_dir, cmp.key))})
    return st


def host_start(cmp: Component, base_dir: str | Path) -> tuple[bool, str]:
    return host_for(cmp, base_dir).start()


def host_stop(cmp: Component, base_dir: str | Path) -> tuple[bool, str]:
    mgr = _HOSTS.get(cmp.key)
    if mgr is None:
        return True, "未在运行"
    mgr.stop()
    return True, "已停止"


def host_reset(cmp: Component, base_dir: str | Path) -> None:
    """安装/换目录后重建托管器（单测与引导重装都用它）"""
    mgr = _HOSTS.pop(cmp.key, None)
    if mgr is not None:
        mgr.stop()


def tail_log(base_dir: str | Path, key: str, limit: int = 40) -> list[str]:
    p = host_log_path(base_dir, key)
    if not p.is_file():
        return []
    try:
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    return lines[-limit:]


def wait_port(port: int, timeout: float = 20.0, interval: float = 0.5) -> bool:
    """等某个 127.0.0.1 端口可连（内置协议进程起来后才监听 WS）"""
    import time as _t
    deadline = _t.time() + timeout
    while _t.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1.0):
                return True
        except OSError:
            _t.sleep(interval)
    return False


def port_of(url: str, default: int = 0) -> int:
    """从 ws://127.0.0.1:3001 之类里取端口（取不到给 default）"""
    from urllib.parse import urlparse
    try:
        p = urlparse(url if "://" in url else f"//{url}").port
        return int(p) if p else default
    except (ValueError, AttributeError):
        return default


def qr_code(base_dir: str | Path, key: str) -> dict[str, Any]:
    """取登录二维码：优先组件目录里的图片（Lagrange 会写 qrcode.png），
    否则把日志尾部当文本二维码用（NapCat 走控制台/WebUI 打印）"""
    root = component_dir(base_dir, key)
    names = ("qrcode.png", "qrcode.jpg", "qrcode.jpeg", "qr.png")
    search_dirs: list[Path] = []
    if root.is_dir():
        search_dirs.append(root)
        search_dirs += sorted(x for x in root.iterdir() if x.is_dir())
    for d in search_dirs:
        for name in names:
            p = d / name
            if p.is_file() and p.stat().st_size > 0:
                return {"kind": "image", "path": str(p), "name": name}
    text = "\n".join(tail_log(base_dir, key, limit=60))
    if any(k in text.lower() for k in ("qrcode", "扫码", "二维码")):
        return {"kind": "text", "text": text}
    return {"kind": "none", "text": text}
