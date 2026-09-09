/*
 * 栖星 AsterCore · C 示例插件：nap_hello
 * 构建（Linux .so）：  cc -shared -fPIC -O2 nap_hello.c -o libnap_hello.so
 * 构建（Windows .dll）：cl /LD nap_hello.c /Fe:nap_hello.dll  （或 gcc -shared）
 * 收到群消息文本 "你好/hello/hi/在吗" → 调用 api->action 回一条群消息。
 *
 * JSON 为最小手拼（无第三方依赖）；复杂插件建议接入 JSON 库。
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "nap_plugin.h"

static NapApi g_api;
static int g_inited = 0;

NAP_EXPORT int nap_plugin_abi_version(void) { return NAP_PLUGIN_ABI_VERSION; }

NAP_EXPORT const char* nap_plugin_name(void) { return "nap_hello"; }

NAP_EXPORT const char* nap_plugin_version(void) { return "0.1.0"; }

static int contains_keyword(const char* text) {
    static const char* kws[] = {"你好", "hello", "hi", "在吗"};
    for (size_t i = 0; i < sizeof(kws) / sizeof(kws[0]); i++) {
        if (strstr(text, kws[i]) != NULL) return 1;
    }
    return 0;
}

/* 从事件 JSON 粗提取文本与 group_id（示例级别，未接 JSON 库） */
static void extract_msg(const char* ev, char* text_out, size_t text_len,
                        char* group_out, size_t group_len) {
    const char* t = strstr(ev, "\"raw\"");
    const char* g = strstr(ev, "\"group_id\"");
    if (t) {
        t = strchr(t, ':'); t = strchr(t, '"') + 1;
        const char* end = strchr(t, '"');
        size_t n = (size_t)(end - t);
        if (n >= text_len) n = text_len - 1;
        memcpy(text_out, t, n); text_out[n] = 0;
    }
    if (g) {
        g = strchr(g, ':') + 1;
        while (*g == ' ' || *g == '"') g++;
        const char* end = g;
        while (*end && *end != ',' && *end != ' ' && *end != '"') end++;
        size_t n = (size_t)(end - g);
        if (n >= group_len) n = group_len - 1;
        memcpy(group_out, g, n); group_out[n] = 0;
    }
}

NAP_EXPORT int nap_plugin_init(const char* config_json, const NapApi* api) {
    if (!api || api->abi_version != NAP_PLUGIN_ABI_VERSION) return -1;
    g_api = *api;
    g_api.log(1, "[nap_hello] 已初始化");
    g_inited = 1;
    return 0;
}

NAP_EXPORT int nap_plugin_on_event(const char* event_json) {
    if (!g_inited) return 2;
    if (!event_json || strstr(event_json, "\"type\":\"message\"") == NULL) return 0;

    char text[256] = {0}, group[32] = {0};
    extract_msg(event_json, text, sizeof(text), group, sizeof(group));
    if (!contains_keyword(text)) return 0;

    g_api.log(1, "[nap_hello] 命中: C 插件收到消息");
    /* 回群消息：action = send_group（经 host/内核执行真实发送） */
    char action[512];
    snprintf(action, sizeof(action),
             "{\"action\":\"send_group\",\"group_id\":\"%s\","
             "\"message\":[{\"type\":\"text\",\"data\":{\"text\":\"你好呀～(来自 C 插件)\"}}]}",
             group);
    g_api.action(action, NULL, NULL);
    return 1;
}

NAP_EXPORT void nap_plugin_unload(void) {
    if (g_inited) g_api.log(1, "[nap_hello] 已卸载");
    g_inited = 0;
}

NAP_EXPORT int nap_plugin_reload(const char* config_json) {
    g_api.log(1, "[nap_hello] 配置热更（忽略内容）");
    return 0;
}
