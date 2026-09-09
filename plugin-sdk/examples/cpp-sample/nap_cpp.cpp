// 栖星 AsterCore · C++ 原生插件示例（复用 nap_plugin.h）
// 构建：g++ -shared -fPIC -O2 -I../.. nap_cpp.cpp -o libnap_cpp.so
//       （Windows: cl /LD /I../.. nap_cpp.cpp 或 g++ -shared）
// 行为：消息含"点歌"时回一条（示例占位，真实点歌可接 HTTP API）。

#include <cstdio>
#include <cstring>
#include <string>

#include "nap_plugin.h"

static NapApi g_api;
static bool g_inited = false;

extern "C" {

NAP_EXPORT int nap_plugin_abi_version(void) { return NAP_PLUGIN_ABI_VERSION; }
NAP_EXPORT const char* nap_plugin_name(void) { return "nap_cpp"; }
NAP_EXPORT const char* nap_plugin_version(void) { return "0.1.0"; }

NAP_EXPORT int nap_plugin_init(const char* config_json, const NapApi* api) {
    if (!api || api->abi_version != NAP_PLUGIN_ABI_VERSION) return -1;
    g_api = *api;
    g_api.log(1, "[nap_cpp] C++ 插件已初始化");
    g_inited = true;
    return 0;
}

NAP_EXPORT int nap_plugin_on_event(const char* event_json) {
    if (!g_inited || !event_json) return 2;
    if (!strstr(event_json, "\"type\":\"message\"")) return 0;
    const char* raw = strstr(event_json, "\"raw\":\"");
    if (!raw) return 0;
    raw += sizeof("\"raw\":\"") - 1;  // 跳过 "raw":  （7 字节）
    const char* end = strchr(raw, '"');
    std::string text(raw, end ? end - raw : 0);
    if (text.find("点歌") == std::string::npos) return 0;

    g_api.log(1, "[nap_cpp] 命中: C++ 插件收到点歌请求");
    // 群号提取（紧凑 JSON: "group_id":123）
    std::string gid;
    if (const char* g = strstr(event_json, "\"group_id\":")) {
        g += 11;
        while (*g && *g >= '0' && *g <= '9') { gid += *g++; }
    }
    if (!gid.empty()) {
        char action[512];
        std::snprintf(action, sizeof(action),
            "{\"action\":\"send_group\",\"group_id\":\"%s\",\"message\":"
            "[{\"type\":\"text\",\"data\":{\"text\":\"点歌功能示例占位(可接HTTP点歌站)\"}}]}",
            gid.c_str());
        g_api.action(action, nullptr, nullptr);
    }
    return 1;
}

NAP_EXPORT void nap_plugin_unload(void) {
    if (g_inited) g_api.log(1, "[nap_cpp] C++ 插件已卸载");
    g_inited = false;
}

}  // extern "C"
