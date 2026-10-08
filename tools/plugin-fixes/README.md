# 插件侧补丁（本仓只存补丁，不改插件）

插件（`video_parser.py` / `video_parser_core/` / `marry.py` / `jm_*` / `pseudo_persona.py` …）
**不在本仓**，它们在独立公开仓库：

    git@github.com:Bdlxx/NapCat-WordLibBot-Plugins.git

本机（构建机）对那个仓**只有只读权限**（deploy key），推不上去，所以把补丁落在这里，
方便在能推的环境里 `git am` 应用：

```bash
git clone git@github.com:Bdlxx/NapCat-WordLibBot-Plugins.git
cd NapCat-WordLibBot-Plugins
git am /path/to/0001-video_parser_core-hardening.patch
git push
```

## 0001-video_parser_core-hardening.patch

修两个**高优**缺陷（Windows 上必现，现象都是"静默半死"）：

1. `parsers/__init__.py` 19 行无条件 import 各平台 → 任一平台依赖缺失就**整包 ImportError**
   （现场表现：发链接毫无反应、只留一行 error）。改为逐个容错 import，失败的记进
   `FAILED_IMPORTS` 跳过，其余平台照常注册；`main.py` 启动时显式打印未加载的平台。
2. `cookie.py::save_to_file()` 的 `cj.save()` 无异常保护，而它被 13 个解析器的 `__init__`
   间接调用 → Windows「文件被占用/OneDrive/只读」抛 OSError 时 `_register_parser` 中断，
   **该平台之后所有平台永不注册**。改为只告警，内存 cookie 仍有效、下次 save 重试。

验证（真代码 + 真依赖）：正常 18 个平台全注册；模拟缺 `xhs` 依赖时其余 17 个照常可用；
模拟 `cj.save` 抛 OSError 时只告警不抛。

> 两个 Linux 实例（`instances/*/plugins/video_parser_core/`）**已经打上这个修复**并重启验证，
> 但那是生产副本、不进仓库：上游不合并的话，下次 `install.sh` 更新插件会把它冲掉。
