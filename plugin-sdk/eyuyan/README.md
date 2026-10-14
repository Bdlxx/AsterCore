# 易语言原生插件开发指南（栖星 AsterCore）

> 依据：《ABI 草案.md》v0.2 + `plugin-sdk/nap_plugin.h`；平台 **仅 Windows x64**。
> 易语言能产出 DLL，但有三条历史包袱必须先处理，否则用户机器上跑不起来。

## 一、硬性要求（三条，缺一不可）

| # | 要求 | 怎么办 | 怎么验 |
|---|---|---|---|
| 1 | **x64 编译** | 用支持 64 位的编译环境（黑月 / 火山 / 官方 x64 支持库）。AsterCore 是 x64 进程，32 位 DLL 加载即失败 | 任务管理器里 DLL 的位数；或 `check_dll_exports.py` 能加载成功 |
| 2 | **cdecl 调用约定** | 插件的 `nap_plugin_*` 必须是 cdecl（C 默认）。易语言"公开子程序"默认是 stdcall 风格，**必须**在声明层改 | 见下：宿主按 cdecl 调用，stdcall 会栈错位直接崩 |
| 3 | **支持库随包分发** | `krnln.fnr`、`spec.fne` 等放进插件包同目录 | 换一台干净机器试加载 |

> 宿主侧现在**兼容"被修饰的导出名"**：`nap_plugin_on_event`、`_nap_plugin_on_event`、
> `_nap_plugin_on_event@4` 都能找到（见 `src/astercore/native.py::symbol_candidates`）。
> 也就是说，易语言把名字导出成 `_nap_plugin_on_event` 也能被认出来 —— 但**调用约定仍必须是 cdecl**。

## 二、导出符号与结构体（x64 下的准确偏移）

插件必须导出（名字与签名见 `nap_plugin.h`）：

```
nap_plugin_abi_version() -> int          必须返回 1
nap_plugin_name()        -> const char*  UTF-8 静态串
nap_plugin_version()     -> const char*  如 "0.1.0"，静态串
nap_plugin_init(config_json: char*, api: NapApi*) -> int   0=成功
nap_plugin_on_event(event_json: char*)  -> int   0=未处理 1=已处理 2=出错
nap_plugin_unload()      -> void
nap_plugin_reload(config_json: char*)   -> int   可选
```

主程序注入的 `NapApi` 在 **x64** 下的字节布局（易语言按偏移取指针用）：

| 偏移 | 字段 | 类型 | 说明 |
|---|---|---|---|
| 0 | `abi_version` | int(4) | =1；后面 4 字节是对齐填充 |
| **8** | `action` | 函数指针 | `int action(char* action_json, char** out_json, int* out_len)` |
| **16** | `log` | 函数指针 | `void log(int level, char* msg)`，level 0/1/2/3 |
| **24** | `alloc` | 函数指针 | `void* alloc(unsigned long long size)` |
| **32** | `free` | 函数指针 | `void free(void* p)` |
| 40 | — | — | `sizeof(NapApi)` |

⚠ 两个最常见的崩因：**按 4 字节对齐算偏移**（x64 是 8 字节，第一个函数指针在偏移 **8** 不是 4）；
以及**把 `action` 当成 stdcall 调用**。

## 三、字符串传递

- 入口参数是 `char*`（UTF-8 字节流，无 BOM）——易语言里先按字节拷成字节集，再转文本解析 JSON。
- 事件 JSON 是**紧凑格式**（无空格），例如 `{"type":"message",...,"raw":"早上好"}`。
- **插件不返回新分配的字符串**：`name/version` 返回静态串，其余导出都返回 int（规避释放问题）。
- 需要"返回体"的动作（如查群成员）：主程序 `alloc` 一段内存写 JSON，插件用毕调 `api->free`。

## 四、怎么调用主程序的能力（action / log）

`api` 是指向主程序回调表的指针。易语言里调它有两种做法：

1. **推荐：用随包的 C 桥**（`nap_eyuyan_bridge.c` → 编成 `nap_bridge.dll`）。
   它把函数指针包装成普通导出函数，易语言按整数传参即可：
   ```
   nap_bridge_init(api, abi_version)             ' 保存 API 指针
   nap_bridge_log(1, "插件已启动")                ' 打日志
   nap_bridge_action(action_json, out_buf, 4096) ' 执行动作，返回体写进缓冲区
   ```
2. 用支持库的"调用函数指针"能力，按上表偏移直接调（要保证 cdecl 与 x64）。

## 五、骨架与声明

- `nap_plugin_易语言声明.txt`：可直接抄进易语言「DLL命令」表 + 偏移表 + 调用示例。
- `骨架_易语言.txt`：最小可用插件的完整源码骨架（伪码级，能照着填）。
- `nap_eyuyan_bridge.c` + `build_msvc.bat` / `build_gcc.sh`：C 桥（可选但强烈建议）。

## 六、验证清单（发布前逐条过）

```bash
# 1) 导出名齐不齐（宿主会在"无修饰名"和"下划线修饰名"里找）
python plugin-sdk/eyuyan/check_dll_exports.py --dll 你的插件.dll
# 期望输出：6 个必需导出全部 found（reload 可选），并打印 abi_version / name / version 的实际值

# 2) 放进插件目录后，面板「运行日志」应出现：已登记原生插件 <名字>
```

打包：
```
插件包/
├─ 你的插件.dll            ← x64、cdecl、导出 nap_plugin_*
├─ nap_bridge.dll          ← 用了 C 桥才需要
├─ krnln.fnr / spec.fne …  ← 易语言运行库（随包）
└─ 说明.txt                ← 写明"需要 AsterCore x64、已随包运行库"
```

## 七、排查表

| 现象 | 多半是 |
|---|---|
| 加载后立刻崩 / 静默无响应 | 调用约定错（stdcall 当 cdecl）或位数不对（x86） |
| 报"缺少导出符号" | 名字被修饰且宿主没找到 → 跑 `check_dll_exports.py` 看实际导出名 |
| `abi_version` 不是 1 | 按 `nap_plugin.h` 的宏返回 1 |
| 收到事件但回消息无效 | `action` 的 JSON 字段名写错（见 ABI §6）；或忘了 `echo` |
| 换台机器跑不起来 | 少拷支持库（krnln.fnr 等） |

## 八、优先级说明（透明）

C / C++ / Rust 模板已经在仓库里验证过能产出合规 DLL；易语言的**导出名与 cdecl**只能在你的
易语言环境里最终确认（本机没有易语言、也没有 C 编译器，无法代跑）。所以本文档 + 声明 + 骨架
+ C 桥 + 校验脚本是一套"你那边照做就能出合规 DLL"的完整材料，**最后一步真机验证需要你**。