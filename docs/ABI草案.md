# 栖星 AsterCore · 原生插件 ABI 规范（草案 v0.1）

> 目标：让**非 Python 语言**（C / C++ / Rust / 易语言等）编写的插件，能以 DLL 形式接入 AsterCore。
> 位置：`plugin-sdk/ABI.md`（尚未建仓库，先入本地 docs）
> 状态：**v0.2 · 评审已过**（子进程隔离 v1 即做，其余按推荐）

---

## 1. 目标与范围

- 插件形态：**Windows x64 DLL**（PE 可执行文件），导出 C 符号
- 运行模型：DLL 由 **plugin host 子进程**（独立进程）用 ctypes 加载，主程序与 host 之间走 **IPC**（§7.1）；
  插件崩溃只崩 host，不拖垮主程序（**子进程隔离，v1 即做**）
- 插件作者视角的接口不变（仍是导出 `nap_plugin_*` 符号 + JSON 交换）；host 只是替主程序调用这些符号的一层
- 所有数据交换用 **JSON 字符串（UTF-8，无 BOM）**——跨语言无结构体对齐问题
- 插件不应直接接触任何框架私有对象

---

## 2. 基础约定

| 项 | 约定 |
|---|---|
| 平台 | Windows x64（首发）；跨平台由主程序加载器抽象，后续可扩 |
| 调用约定 | **cdecl**（C 默认）；易语言等需在导出时对齐（见 §10） |
| 字符编码 | 全部 JSON 字符串 **UTF-8** |
| 内存所有权 | 主程序分配的 → 插件**只读不释放**；插件需要主程序代管的 → 用 `api->alloc/free`（见 §4） |
| 符号导出 | `__declspec(dllexport)` / Rust `#[no_mangle] extern "C"` |
| 插件唯一名 | `nap_plugin_name()` 返回，ASCII 小写+下划线，同账号内全局唯一 |
| 运行模型 | DLL 在 **plugin host 子进程**内被加载调用；主程序↔host 走 IPC（§7.1） |

---

## 3. 插件必须导出的接口（导出符号）

```c
#define NAP_PLUGIN_ABI_VERSION 1

/* 主程序先调用这三个"探测"函数，决定是否加载本 DLL */
int         nap_plugin_abi_version(void);   /* 必须返回 NAP_PLUGIN_ABI_VERSION */
const char* nap_plugin_name(void);          /* UTF-8，指向静态字符串，主程序不释放 */
const char* nap_plugin_version(void);       /* 例如 "1.2.0"，同样静态 */

/* 生命周期 */
int  nap_plugin_init   (const char* config_json, const NapApi* api);
      /* config_json: 该插件的配置（JSON 对象），可为 "{}"；返回 0=成功，非 0=失败（主程序卸载并禁用） */

int  nap_plugin_on_event(const char* event_json);
      /* 每收到一个事件调用一次；返回：0=未处理(放行给后续插件) 1=已处理(终止链) 2=插件内部错误 */

void nap_plugin_unload(void);
      /* 主程序停止投递后调用；插件必须在此前结束自己派生的线程 */
```

可选（推荐实现）：

```c
int nap_plugin_reload(const char* config_json);
      /* 配置被面板修改时调用（不卸载插件），返回 0=成功 */
```

---

## 4. 主程序回调表 NapApi（当前 v1）

```c
typedef struct NapApi {
    int abi_version;                      /* 填 NAP_PLUGIN_ABI_VERSION */

    /* —— 动作：插件 → 主程序 —— */
    /* 执行一个统一动作（见 §6）。成功返回 0。
     * out_json / out_len 可为 NULL（无需返回体）。
     * 需要返回体的动作（如查询群成员）：主程序 alloc 内存写入，插件用毕调用 api->free 释放。 */
    int  (*action)(const char* action_json, char** out_json, int* out_len);

    /* —— 日志 —— */
    /* level: 0=debug 1=info 2=warn 3=error */
    void (*log)(int level, const char* msg);

    /* —— 内存 —— */
    void* (*alloc)(unsigned long long size);
    void  (*free)(void* ptr);
} NapApi;
```

**版本化约定**：结构体字段**只允许尾部追加**；`abi_version` 用于主程序/插件双向校验；旧插件不认识的尾部字段天然忽略（按结构体头部分量拷贝）。

**线程安全**：`api->action` 可从任意线程调用（主程序内部串行化）。

---

## 5. 事件 JSON（主程序 → 插件）

统一事件外壳：

```json
{
  "abi": 1,
  "type": "message",
  "account_id": 740979632,
  "platform": "napcat",
  "message_type": "group",
  "group_id": 123456789,
  "user_id": 10001,
  "self_id": 740979632,
  "time": 1700000000,
  "raw": "早上好",
  "segments": [
    { "type": "text", "data": { "text": "早上好" } }
  ]
}
```

### 字段说明

| 字段 | 必填 | 说明 |
|---|---|---|
| `abi` | 是 | 事件协议版本（=1） |
| `type` | 是 | `message` / `group_increase` / `group_decrease` / `notice` / `request` / `cron` |
| `account_id` | 是 | 来源账号（多账号区分） |
| `platform` | 是 | 后端标识：`napcat` / `lagrange` / … |
| `message_type` | type=message 时 | `group` / `private` |
| `group_id` / `user_id` | 视事件 | 群事件带 group_id；私聊无 group_id |
| `self_id` | 是 | 机器人自身 QQ |
| `time` | 是 | Unix 秒 |
| `raw` | message 时 | 纯文本拼接（无 CQ 码） |
| `segments` | message 时 | 消息段数组（见下） |
| `reply_token` | 可选 | 主程序生成的一次性 token，插件可配合 `action=send_message` 原样回传以精准回复该消息 |

### 消息段（OneBot v11 子集，与现有 Python 插件对齐）

`text` / `image` / `video` / `record` / `file` / `at` / `reply` / `forward`（合并转发，含自定义 `news`）

---

## 6. 动作 JSON（插件 → 主程序）

```json
{
  "action": "send_message",
  "account_id": 740979632,
  "echo": "req-001",
  "target": { "type": "group", "id": 123456789 },
  "message": [ { "type": "text", "data": { "text": "你好" } } ],
  "reply_token": "…"        /* 若希望回复到具体某条消息，回传事件里的 reply_token */
}
```

通用字段：`action`（必填）、`account_id`（可选，默认当前事件账号）、`echo`（透传，配合返回体关联）。

### 动作清单（v1 核心集）

| action | 参数要点 | 返回体 |
|---|---|---|
| `send_message` | target + message | 无（成功即 0） |
| `send_private` | user_id + message | 无 |
| `send_group` | group_id + message | 无 |
| `recall_message` | message_id | 无 |
| `set_group_ban` | group_id + user_id + duration | 无 |
| `set_group_card` | group_id + user_id + card | 无 |
| `get_group_members` | group_id | JSON 数组（经 out_json） |
| `get_group_info` | group_id | JSON 对象 |
| `get_stranger_info` | user_id | JSON 对象 |
| `upload_file` | target + file(容器路径/URL) | 无 |

返回体格式（需要时）：统一 `{ "ok": true, "data": … }` 或 `{ "ok": false, "error": "…" }`，经 `out_json` 返回，插件用毕 `api->free(out_json)`。

---

## 7. 运行模型与线程

### 7.1 进程结构（子进程隔离，v1 即做）

```
AsterCore.exe（主程序）
   │  IPC（JSON 行协议：stdin/stdout 或命名管道）
   ▼
plugin-host.exe（每插件一个 host 子进程）
   │  ctypes 加载
   ▼
插件 DLL（nap_plugin_* 导出）
```

- 每个原生插件运行在**独立 host 子进程**：插件崩溃/野指针只崩该 host
- 主程序监听 host 存活：异常退出 → 标记该插件"已崩溃"并可自动重启（带次数上限）
- 插件不可信的 `api->action` 也经 host 转发回主程序，主程序做权限/参数校验
- 所有 IPC 报文 JSON，UTF-8，按行/定长帧（实现时定）；报文与 §5/§6 事件、动作 JSON 一致

### 7.2 线程约定（host 进程内）

| 场景 | 约定 |
|---|---|
| 事件投递 | host 内**单线程串行**调用 `nap_plugin_on_event`（同插件实例），插件逻辑无需自行加锁 |
| 插件自起线程 | 允许（如定时轮询）；可随时调 `api->action`（由 host 代理转发，线程安全） |
| 卸载 | 主程序停投递 → 通知 host → host 调 `nap_plugin_unload` 后退出；插件应在 unload 前 join 自己的线程 |
| 多账号 | v1：每账号加载**独立 host 实例**（配置按账号隔离、崩溃互不影响）；插件按 `account_id` 区分事件 |
| 崩溃 | host 退出 → 主程序记录事件日志、标记插件状态，按策略重启（可配置：自动×N / 仅提示） |

---

## 8. 返回码

| 函数 | 码 | 含义 |
|---|---|---|
| `nap_plugin_init` | 0 | 成功 |
| | 非 0 | 初始化失败 → 主程序卸载该插件并标记禁用，写日志 |
| `nap_plugin_on_event` | 0 | 未处理，放行给后续插件 |
| | 1 | 已处理，终止本事件链 |
| | 2 | 内部错误（记日志；按"已处理"停止，避免刷屏） |

---

## 9. 配置与数据目录

- 插件配置：`accounts/<QQ>/data/plugins/<plugin_name>/config.json`
- 面板生成/编辑 → 调 `nap_plugin_reload(config_json)`（未实现 reload 的插件忽略，主程序记一条 info）
- 插件自有数据：同一目录下自行读写 `data.json` 等（由插件负责；目录已由主程序创建）

---

## 10. 各语言落地注意

### C / C++
- C：按 §3/§4 头文件（`nap_plugin.h`，未建，入 SDK 计划）
- C++：所有导出包 `extern "C"`；结构体用 POD

### Rust
- `crate-type = ["cdylib"]`
- `#[no_mangle] pub extern "C" fn nap_plugin_abi_version() -> i32`
- 字符串：读入后转 `String`（UTF-8）；导出名返回 `std::ffi::CString::into_raw`（泄漏一次可接受，或由主程序约定不释放静态字符串）

### 易语言
1. 导出：子程序"公开"，并用支持库声明为 cdecl（勿用默认 stdcall 造成栈错位）
2. 字符串：入口收到的 `char*`（JSON）是内存指针，需在 DLL 内转成易语言文本再解析 JSON（建议用 JSON 解析库）；返回的 JSON 文本需由主程序 `alloc` 或按静态约定——**推荐：所有需要"返回字符串"的导出走 `api->alloc`**（对易语言不友好，故 v1 设计里导出函数都不返回新分配串：`name/version` 返回静态串，`on_event/init` 返回 int）
3. 运行库：`krnln.fnr` 等支持库须随插件包分发
4. 32/64 位：只支持 x64，易语言需 64 位编译（或采用 x86 兼容宿主——**v1 不做 x86**）

---

## 11. 版本与演进策略

- ABI 版本 = 导出接口+事件/动作协议整体版本（当前 1）
- `NapApi` 与事件/动作对象只允许**尾部新增字段**；主程序支持旧版本插件（按导出版本分发不同填充）
- 插件名全局唯一；同名不同版本视为冲突（后加载者被拒）
- 主程序-插件**双向日志可关联**：插件日志走 `api->log`，统一进入面板日志流（带 `[插件名]` 前缀）

---

## 12. 评审决策记录（已定 · v0.2）

| # | 评审点 | 结论 |
|---|---|---|
| 1 | 异步动作 | **全同步 + 主程序超时兜底**（慢动作超时返回错误，插件事件线程不阻塞） |
| 2 | 插件间调用 | **v1 不支持**；插件协作通过发消息/事件由主程序转发完成；接口不占位，未来需要再加 |
| 3 | reply_token | **保留**：事件带一次性 token，插件回复原样回传即可精准回复 |
| 4 | 进程隔离 | **子进程隔离（v1 即做）**：每插件独立 plugin-host（§7.1），崩溃不影响主程序 |
| 5 | reload | **可选**：未实现 reload 的插件，面板改配置时主程序提示"需重启插件生效" |

---

*本文档为 AsterCore 内部规划文档，与《Windows版开发计划书》§5.3 对应。*
