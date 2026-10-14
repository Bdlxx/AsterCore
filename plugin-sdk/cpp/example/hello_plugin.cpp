// 栖星 AsterCore · C++ 插件示例（用 nap::Plugin SDK 写，约 30 行）
//
// 行为：群里发「你好」「hello」「hi」→ 回一句「你好呀～」；其它消息一律放行。
// 编译（任选其一，必须 **x64**）：
//   cl /nologo /LD /O2 /std:c++14 /I "..\.." /Fe:nap_hello_cpp.dll hello_plugin.cpp
//   python -m ziglang c++ -target x86_64-windows-gnu -shared -O2 -I "..\.." -o nap_hello_cpp.dll hello_plugin.cpp
//   g++ -shared -O2 -m64 -I../.. hello_plugin.cpp -o nap_hello_cpp.dll -static   ← 必须 -static
//     （-static-libgcc -static-libstdc++ 不够：posix 线程模型的 MinGW 还会拖 libwinpthread-1.dll，
//       加载时报"找不到模块或它的依赖"）
//
// 装法：把 nap_hello_cpp.dll 放进 <实例>/plugins/ ，面板「运行日志」里应出现
//       「已登记原生插件 nap_hello_cpp」。
#include "nap_plugin.hpp"

class HelloCpp : public nap::Plugin {
public:
    const char* name() const { return "nap_hello_cpp"; }
    const char* version() const { return "0.1.0"; }
    const char* description() const { return "示例：群里发「你好」回一句（C++ SDK 版）"; }
    const char* author() const { return "AsterCore"; }

    int on_config(const char* config_json) {
        (void)config_json;                       // 要读配置：nap::Host::instance().config()
        nap::logf(1, "%s 已启动（ABI v%d）", name(), nap::Host::instance().abi_version());
        return 0;
    }

    int on_event(const nap::Event& event) {
        if (!event.is_group()) return 0;                       // 私聊不管
        if (!(event.has_keyword("你好") || event.has_keyword("hello") || event.has_keyword("hi")))
            return 0;                                          // 没命中 → 放行（下一个插件还能收到）
        if (!nap::send_group(event.group_id(), "你好呀～")) {
            nap::log_error("回消息失败（宿主没接受动作？）");
            return 2;                                          // 2 = 内部错误
        }
        return 1;                                              // 1 = 已处理，不再往下传
    }

    void on_unload() {
        nap::log_debug("nap_hello_cpp 已卸载");
    }
};

NAP_PLUGIN(HelloCpp)
