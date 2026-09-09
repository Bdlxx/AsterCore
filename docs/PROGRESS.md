# 栖星 AsterCore · 开发进度表

> 更新：2026-09（多任务轮）· 对照《Windows版开发计划书》Phase 0-6
> 状态图例：✅ 完成 · 🔶 部分/雏形 · ⬜ 未开始

---

## 一、总体进度

| 计划书阶段 | 内容 | 状态 |
|---|---|---|
| Phase 0 | 设计定稿（命名/决策/ABI 草案/打包原型） | ✅ |
| Phase 1 | 内核重构（backend 适配层/多账号/插件 SDK） | ✅ |
| Phase 2 | 桌面壳（启动器/首启向导/托盘/pywebview） | 🔶 待 Windows |
| Phase 3 | 插件发布形态（.pyd / 原生 C-ABI / 子进程隔离） | 🔶 |
| Phase 4 | 内置协议直登（Lagrange） | ⬜ |
| Phase 5 | 打包分发与更新器 | 🔶 |
| Phase 6 | 生态与收尾 | ⬜ |

---

## 二、按模块明细

### 内核 core（Phase 1 · ✅ 核心完成）

| 模块 | 说明 | 状态 |
|---|---|---|
| 事件/动作模型 | 统一 Event/Segment/Action（对齐 ABI，带 account_id） | ✅ |
| 插件 SDK | Plugin 类 + 模块函数式 + PluginContext 能力注入 | ✅ |
| 事件总线 | 链式分发（handled/passed/failed）+ 插件加载器(.py/.pyd 优先) | ✅ |
| 插件管理 | enable/disable/reload/list + config.json 读写与热更 | ✅ |
| 后端抽象 | Backend 接口 + 注册表（可插拔） | ✅ |
| OneBot v11 后端 | WS 事件翻译 + 动作映射 + echo 等待 + 自动重连 | ✅（已单测） |
| 环形日志 | LogRing（recent/subscribe/tail_since）+ logging handler | ✅ |
| 账号管理器 | accounts/<id>/backend.json、扫描/启停/重启/删除 | ✅ |

### Web 面板（🔶 雏形可用，桌面壳待做）

| 功能 | 状态 |
|---|---|
| 单端口 JSON API（/api/accounts、插件、日志、配置） | ✅ |
| 多账号管理页（列表/新建/启停/删除） | ✅ |
| 插件开关 + JSON 配置编辑器 | ✅ |
| 插件类型标识（[C]/[pyd]）+ 崩溃计数显示 | ✅ |
| 实时日志流（1s 轮询，跨账号） | ✅ |
| pywebview 内嵌桌面壳 | ⬜ |
| 首启向导（运行方式 + 风险确认） | ⬜ |
| 免密/密码/Token 鉴权分级 | ⬜ |

### 插件体系（Phase 3 · 🔶）

| 项 | 状态 |
|---|---|
| .py 开发态 | ✅ |
| .pyd 发布防改（pack/build_pyd.py，Cython） | ✅（Linux .so 验证；import 优先） |
| 原生 C-ABI（nap_plugin.h + C 示例） | ✅ ctypes 加载 |
| plugin-host 子进程隔离 | ✅（崩溃隔离 + 自动重启 + 统一池接入） |
| 原生/Python 双通道自动判别（import 优先） | ✅ |
| 语言模板（Rust/C++/易语言） | ⬜ |
| host 崩溃自动禁用（重启耗尽 → 停用） | ✅ |

### 打包与分发（Phase 5 · 🔶）

| 项 | 状态 |
|---|---|
| PyInstaller spec（pack/astercore.spec） | ✅（Linux 33MB onedir 验证可运行） |
| Windows CI 工作流 | 🔶 已写好待推（需 workflow scope token） |
| zip 分发 + 更新器（校验/回滚/保 data） | ⬜ |
| NapCat 可选组件引导 | ⬜ |

### 内置协议（Phase 4 · ⬜）

Lagrange 子进程托管 / backend-lagrange / 风险提示 —— 未开始。

---

## 三、测试与交付物

| 项 | 值 |
|---|---|
| 单元测试 | 26 个（翻译/映射/运行时/启停/日志/账号管理/原生ABI/隔离/自动重启） |
| 端到端验证 | fake OneBot WS 联调：WS↔翻译↔插件↔发送 全链路 ✅ |
| 提交数（本地） | 17+ |
| GitHub 远端 | Bdlxx/AsterCore（11 提交已同步；workflow 待权限） |
| 示例插件 | demo_hello（打招呼，ctx 发送） |

---

## 四、近期候选任务（按优先级）

1. P2 桌面壳：pywebview 窗口 + 托盘 + 首启向导（需先解决 Windows 构建/运行环境）
2. P3：Cython .pyd 构建脚本（插件发布态）
3. P3：原生插件 host 子进程隔离骨架（跨平台可先做 host 协议）
4. P5：Windows 真实构建验证（GitHub Actions 或本地 Windows）
5. P1 补充：消息段全类型测试、类插件示例（配置热更演示）

---

*对照文档：《Windows版开发计划书.md》（v0.5，本地）·《ABI草案.md》（v0.2，本地）*
