# 栖星 AsterCore · 开发进度表

> 更新：2026-09（v0.2.0 可用版）· 对照《Windows版开发计划书》Phase 0-6
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
| 插件开关 + JSON 配置编辑器 + 单插件热重载 | ✅ |
| 插件类型标识（[C]/[pyd]）+ 崩溃计数显示 | ✅ |
| 实时日志流（1s 轮询，跨账号） | ✅ |
| pywebview 内嵌桌面壳 | ⬜ |
| 首启向导（运行方式 + 风险确认） | 🔶 决策逻辑已实现（app.py），窗口层待编译 |
| 免密/密码/Token 鉴权分级 | ✅（auth.py + 前端登录层） |

### 插件体系（Phase 3 · 🔶）

| 项 | 状态 |
|---|---|
| .py 开发态 | ✅ |
| .pyd 发布防改（pack/build_pyd.py，Cython） | ✅（Linux .so 验证；import 优先） |
| 原生 C-ABI（nap_plugin.h + C 示例） | ✅ ctypes 加载 |
| plugin-host 子进程隔离 | ✅（崩溃隔离 + 自动重启 + 统一池接入） |
| 原生/Python 双通道自动判别（import 优先） | ✅ |
| Rust 模板（cdylib，plugin-sdk/rust-sample） | ✅（文件就绪，待 cargo 编译验证） |
| C++ / 易语言模板 | ⬜ |
| host 崩溃自动禁用（重启耗尽 → 停用） | ✅ |

### 打包与分发（Phase 5 · 🔶）

| 项 | 状态 |
|---|---|
| PyInstaller spec（pack/astercore.spec） | ✅（Linux 33MB onedir 验证可运行） |
| Windows CI 工作流 | 🔶 已写好待推（需 workflow scope token） |
| zip 分发 + 更新器（校验/替换/保 data/自动回滚） | ✅ 内核（update.py） |
| NapCat 可选组件引导 | ⬜ |

### 内置协议（Phase 4 · ⬜）

Lagrange 子进程托管 / backend-lagrange / 风险提示 —— 未开始。

---

## 三、测试与交付物

| 项 | 值 |
|---|---|
| 单元测试 | 79 个（+首启引导/路径/入口路由/面板静态与API冒烟/原生E2E） |
| 端到端验证 | fake OneBot WS 联调：WS↔翻译↔插件↔发送 全链路 ✅ |
| 面板验证 | Playwright+Chromium 实跑：账号卡片/插件列表渲染、零 JS 报错 ✅ |
| 打包验收 | `tools/e2e_frozen.py`：解压即用全流程 29 项检查（含浏览器级）✅ |
| Git 远端 | Bdlxx/AsterCore（master + tag 已同步） |
| 示例插件 | demo_hello(.py/.so) · demo_counter(类插件/热更) · C/Rust 原生示例 |

> 已发布：v0.1.0 / v0.1.1 / v0.1.2 / v0.1.3（修复面板 JS 失效）/ v0.2.0（可用版）

### v0.2.0 可用版做了什么
| 项 | 说明 |
|---|---|
| 冻结路径 | 打包运行以 exe 所在目录为基准，data/accounts/plugins 不再随启动位置乱跑 |
| 首启引导 | 自动建目录 + 播种示例插件（不覆盖用户文件），双击即用 |
| 入口路由 | 带启动器参数(如 --port/--no-browser)不再掉进 CLI 解析报错；CLI 需 --cli 或专属参数 |
| 用户可见插件目录 | exe 同级 plugins/，面板显示路径提示；所有账号共享加载，可分别启停 |
| 向导健壮化 | 无控制台/输入被重定向时自动用推荐模式，不再卡死；回车即用推荐项 |
| 端口避让 | 面板端口被占用自动顺延，避免二次启动失败 |
| 版本贯穿 | `__version__` 统一到 CLI 标题/面板/接口，pyproject 同步校验 |
| 面板体验 | 打开即自动选中运行中账号（插件/调试台/日志立即可用） |
| 发布物 | zip 内附《使用说明.txt》（Windows 图文步骤）与 README |

---

## 四、真连验证记录（2026-09）

- ✅ NapCat 多客户端不互踢：asterCore 第二连接可握手，线上 bot 不受影响
- ✅ 强制事件推送正常：新连接收到 `group_increase` 等 notice
- ⚠️ **普通群消息不推给短时第二 WS 客户端**（NapCat 推送策略；NapCat 进程日志确认已收到消息但未转发给新连接）——协议层差异非代码问题
- ✅ 协议全链路（事件翻译/8 类动作/echo/自动重连）由 fake_onebot E2E（30+ 断言）覆盖
- ℹ️ Windows 版每账号独立 NapCat 实例，天然无共享连接推送限制

## 五、近期候选任务（按优先级）

1. P2 桌面壳：pywebview 窗口 + 托盘 + 首启向导（需先解决 Windows 构建/运行环境）
2. P3：Cython .pyd 构建脚本（插件发布态）
3. P3：原生插件 host 子进程隔离骨架（跨平台可先做 host 协议）
4. P5：Windows 真实构建验证（GitHub Actions 或本地 Windows）
5. P1 补充：消息段全类型测试、类插件示例（配置热更演示）

---

*对照文档：《Windows版开发计划书.md》（v0.5，本地）·《ABI草案.md》（v0.2，本地）*
