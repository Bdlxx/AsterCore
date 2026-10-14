# 栖星 AsterCore · C++ 插件 SDK

给"想用 C++ 写原生插件"的人：**一个头文件，零第三方依赖**，把 ABI、JSON、事件解析、动作发送都封好了。
你要写的只有一个类 + 一行注册。

> 只想要"最小可跑"的裸 ABI 示例（不引 SDK）？看 `plugin-sdk/examples/c-sample/nap_hello.c`
> 与 `plugin-sdk/examples/cpp-sample/nap_cpp.cpp`。ABI 全文见 `docs/ABI草案.md`。

---

## 30 秒上手

```cpp
#include "nap_plugin.hpp"          // 再带上 plugin-sdk/nap_plugin.h（ABI 头）

class HelloCpp : public nap::Plugin {
public:
    const char* name() const { return "nap_hello_cpp"; }   // = DLL 文件名，别带空格
    const char* version() const { return "0.1.0"; }

    int on_event(const nap::Event& event) {                // 0=放行 1=已处理 2=内部错误
        if (event.is_group() && event.has_keyword("你好")) {
            return nap::send_group(event.group_id(), "你好呀～") ? 1 : 2;
        }
        return 0;
    }
};

NAP_PLUGIN(HelloCpp)              // 一行生成 nap_plugin_* 六个导出
```

完整可编译版本：`example/hello_plugin.cpp`。

---

## 编译（**必须 x64**）

AsterCore 是 64 位进程，**32 位 DLL 加载必失败**。命令里请确保目标是 x64。

### MSVC（推荐，Visual Studio 的「x64 Native Tools 命令提示符」里）

```bat
cl /nologo /LD /O2 /MT /std:c++14 /utf-8 /I ".." /I "." /Fe:nap_hello_cpp.dll example\hello_plugin.cpp
```

或直接双击本目录的 `build_msvc.bat`。

> ⚠ **`/utf-8` 别省**。不加它，中文字面量会按系统 ANSI（中文系统上就是 GBK）编码，
> 于是 `has_keyword("你好")` 永远匹配不上 UTF-8 的事件文本 —— 这种坑很安静，只是"插件像没生效"。
> （SDK 里 `nap_utf8_literals_ok()` 会做运行期自检并在 init 时打一条 error 日志提醒。）

### 没有 Visual Studio？用 zig 自带的 C++ 编译器（一条 pip 就行）

```bat
python -m pip install ziglang
build_zig.bat            rem 或手动：
python -m ziglang c++ -target x86_64-windows-gnu -shared -O2 -fexec-charset=UTF-8 ^
       -I ".." -I "." -o nap_hello_cpp.dll example\hello_plugin.cpp
```

### MinGW / GCC

```sh
g++ -shared -O2 -m64 -fexec-charset=UTF-8 -I.. -I. hello_plugin.cpp -o nap_hello_cpp.dll -static
```

> ⚠ **必须是 `-static`，不是 `-static-libgcc -static-libstdc++`**。winlibs / MSYS2 的 GCC 多用
> **posix 线程模型**，那两个开关**不会**把 `libwinpthread-1.dll` 静态链进去 → DLL 编得出来，
> 但加载时报「找不到模块或它的依赖」✗（冻结版宿主是 x64 进程，加载失败就是"插件装不进去"）。
> `-static` 一次把 libgcc / libstdc++ / winpthread 全静态化，产物只剩系统 DLL ✓。
> 这条有回归测试看着：`tests/test_cpp_sdk.py::CppSdkBuildTest`（探针会试编一个用到 C++ 运行库的
> 小 DLL 并当场加载，加载不了就直接判这条工具链不合格）。

### CMake（把 SDK 当 INTERFACE 库用）

```cmake
add_subdirectory(plugin-sdk/cpp)
target_link_libraries(你的插件 PRIVATE nap_sdk)      # 头文件路径 + /utf-8 都替你加好了
```

---

## SDK 给了什么

| 能力 | 接口 |
|---|---|
| 事件取值 | `event.message_type()` / `is_group()` / `is_private()` / `group_id()` / `user_id()` / `self_id()` / `account_id()` / `platform()` |
| 文本与段 | `event.raw_message()`（**raw 与 raw_message 两种事件形状都认**）、`event.has_keyword("你好")`、`event.at_me()`、`event.segments()` |
| 发消息 | `nap::send_group(gid, "文本")` / `nap::send_group(gid, nap::message({seg_at(1), seg_text("看："), seg_image(url)}))` / `nap::send_private(...)` |
| 泛动作 | `nap::Host::instance().action(json)`（返回体写进 `std::string*`），JSON 用自带的 `nap::Json` 拼 |
| 日志 | `nap::log_info/warn/error/debug(std::string)`、`nap::logf(1, "带参数：%d %s", 42, "ok")` |
| 配置 | `nap::Host::instance().config()`（init/reload 传入的 JSON 已解析好，`nap::Json` 取值） |
| JSON | `nap::Json`：解析 + 序列化（UTF-8 直通、`\uXXXX` 与代理对、深度上限、往返不丢精度） |

### ⚠ 事件形状：两种都要能认

- **原生插件实际收到的是本框架的形状**（`Event.to_dict()`）：`type` / `raw` / `segments` / `message_type` / `account_id` / `platform` / `reply_token`
- **老插件契约（OneBot v11 形状）**：`post_type` / `raw_message` / `message`

SDK 的 `raw_message()` 与 `segments()` **两种都认**（先 `raw_message`/`message`，再 `raw`/`segments`，都没有就把 `text` 段拼起来）。
写插件时只管调 `raw_message()` / `has_keyword()` 就行 —— 但**别自己去读 `json["message"]`**，那会在原生事件上取到空。

### 返回值语义（与 ABI 一致）

| 返回 | 含义 |
|---|---|
| `0` | 未处理 → 放行，后面的插件还能收到这个事件 |
| `1` | 已处理 → 停链 |
| `2` | 内部错误（宿主会记日志；SDK 在 JSON 解析失败时也返回 2） |

---

## 自测与校验（发布前逐条过）

```bat
rem 1) SDK 自带单元测试（JSON/事件/动作/宏生成的导出，100+ 条断言）
python -m ziglang c++ -std=c++11 -O1 -I ".." -I "." -o nap_sdk_tests.exe tests\test_sdk.cpp
nap_sdk_tests.exe            rem 期望输出「结果：全部通过 ✅」

rem 2) 导出名与 ABI（宿主会在"无修饰名"和"下划线修饰名"里找）
python plugin-sdk\tools\check_dll_exports.py --dll nap_hello_cpp.dll

rem 3) 放进 <实例>\plugins\ ，面板「运行日志」应出现：已登记原生插件 nap_hello_cpp
```

`tests/test_cpp_sdk.py`（Python 侧）把上面 1~3 步**自动跑了一遍**（有编译器才跑；用真实宿主加载 DLL 并派发事件）。

---

## 打包清单

```
你的插件.dll            ← x64、导出 nap_plugin_*（/MT 或 -static-libstdc++ → 不依赖额外 DLL）
说明.txt                ← 写清"需要 AsterCore x64"
（选）配置模板.json
```

## 常见问题

| 现象 | 多半是 |
|---|---|
| 加载失败 / 面板报位数不符 | 编成了 **32 位**（要 x64；`-target x86_64-windows-gnu` 或 x64 工具链） |
| 插件加载了但"什么都不做" | 中文字面量没按 UTF-8（MSVC 加 `/utf-8`；GCC/Clang 加 `-fexec-charset=UTF-8`） |
| 报"缺少导出符号" | 用了 C++ 名字修饰（必须 `extern "C"`）或没写 `NAP_PLUGIN(你的类)` |
| 换台机器跑不起来 / 加载报"找不到模块或它的依赖" | MSVC 用了 `/MD`（要 `/MT`），或 MinGW 只加了 `-static-lib*`（**要 `-static`**，否则会拖 `libwinpthread-1.dll`） |
| 一收事件就崩 | 别在 `on_event` 里抛异常/长阻塞；SDK 只保证"JSON 坏了不崩"（返回 2） |
