/*
 * 栖星 AsterCore · 原生插件 ABI v1（nap_plugin.h）
 * 对应《ABI草案.md》v0.2。任何能产出 C 导出 DLL/SO 的语言皆可实现。
 *
 * 约定：
 *  - 所有字符串为 UTF-8（无 BOM）
 *  - 调用约定：cdecl（Windows 上勿用 stdcall）
 *  - 插件导出 nap_plugin_* 符号；主程序（或 plugin-host）调用
 *  - 返回的 const char* 指向插件静态内存，调用方只读不释放
 */
#ifndef NAP_PLUGIN_H
#define NAP_PLUGIN_H

#ifdef _WIN32
#  define NAP_EXPORT __declspec(dllexport)
#else
#  define NAP_EXPORT __attribute__((visibility("default")))
#endif

#ifdef __cplusplus
extern "C" {
#endif

#define NAP_PLUGIN_ABI_VERSION 1

/* ---------- 插件 → 主程序：动作回调表（主程序注入，见 init） ---------- */
typedef struct NapApi {
    int abi_version;
    /* 执行统一动作（见 ABI 文档 §6）。成功返回 0。
     * out_json/out_len 可传 NULL；需要返回体时主程序分配内存，
     * 插件用毕调用 free 释放。 */
    int (*action)(const char* action_json, char** out_json, int* out_len);
    /* 日志：level 0=debug 1=info 2=warn 3=error */
    void (*log)(int level, const char* msg);
    /* 内存：由主程序实现，线程安全 */
    void* (*alloc)(unsigned long long size);
    void  (*free)(void* ptr);
} NapApi;

/* ---------- 插件必须导出 ---------- */
NAP_EXPORT int         nap_plugin_abi_version(void);   /* 必须返回 NAP_PLUGIN_ABI_VERSION */
NAP_EXPORT const char* nap_plugin_name(void);          /* UTF-8 静态串 */
NAP_EXPORT const char* nap_plugin_version(void);       /* 如 "0.1.0" */

/* 初始化：config_json 为该插件配置（JSON 对象，可为 "{}"）。
 * 返回 0 成功；非 0 失败（主程序卸载并禁用） */
NAP_EXPORT int  nap_plugin_init(const char* config_json, const NapApi* api);

/* 事件（JSON，见 ABI 文档 §5）。返回：
 *   0 = 未处理（放行）  1 = 已处理（终止链）  2 = 内部错误 */
NAP_EXPORT int  nap_plugin_on_event(const char* event_json);

/* 卸载：主程序停止投递后调用；插件须先结束自己的线程 */
NAP_EXPORT void nap_plugin_unload(void);

/* ---------- 可选 ---------- */
/* 配置热更：返回 0 成功（未实现可不导出） */
NAP_EXPORT int  nap_plugin_reload(const char* config_json);

#ifdef __cplusplus
}
#endif

#endif /* NAP_PLUGIN_H */
