/* 栖星 AsterCore · 易语言 C 桥（nap_bridge.dll）
 *
 * 为什么要有它：NapApi 里的 action/log/alloc/free 是**函数指针**，易语言直接按偏移
 * 调用需要"调用函数指针"支持库并且要自己保证 cdecl/x64；这个 DLL 把三件事包成普通导出，
 * 易语言按整数/文本传参即可。
 *
 * 编译（任选其一）：
 *   build_msvc.bat      cl /LD /O2 /Fe:nap_bridge.dll nap_eyuyan_bridge.c
 *   build_gcc.sh        gcc -shared -O2 -o nap_bridge.dll nap_eyuyan_bridge.c
 * 产物 nap_bridge.dll 放进插件包同目录。
 *
 * ⚠ 这个 DLL 不实现插件 ABI —— 插件的 nap_plugin_* 仍由你的易语言 DLL 导出。
 */
#include <stdio.h>
#include <string.h>
#include <windows.h>

/* 与 nap_plugin.h 的 NapApi 必须逐字段一致（x64: int 后有 4 字节对齐填充） */
typedef struct NapApi {
    int abi_version;                                    /* 偏移 0（+4 填充） */
    int (*action)(const char* action_json, char** out_json, int* out_len);  /* 8  */
    void (*log)(int level, const char* msg);            /* 16 */
    void* (*alloc)(unsigned long long size);            /* 24 */
    void  (*free)(void* ptr);                           /* 32 */
} NapApi;                                               /* 40 字节 */

static const NapApi* g_api = NULL;

/* 初始化：把 nap_plugin_init 收到的 api 指针交给桥保存（返回 0 成功、-1 参数为空） */
__declspec(dllexport) int __cdecl nap_bridge_init(void* api, int abi_version)
{
    if (!api) return -1;
    if (abi_version != 1) return -2;                    /* 与 nap_plugin.h 的 ABI 版本对齐 */
    g_api = (const NapApi*)api;
    /* 探一下结构体偏移是否如预期（填错结构体这里就会看出来） */
    if (g_api->abi_version != 1) return -3;
    return 0;
}

/* 日志：level 0/1/2/3；msg 传 UTF-8 字节流（易语言的"字节集"直接传地址） */
__declspec(dllexport) void __cdecl nap_bridge_log(int level, const char* msg)
{
    if (g_api && g_api->log) g_api->log(level, msg ? msg : "");
}

/* 执行动作：action_json 为 UTF-8 的紧凑 JSON；返回体写进 out_buf（UTF-8，NUL 结尾）
 * 返回 0 成功、负数失败（-10 未初始化 / -11 动作返回失败）。 */
__declspec(dllexport) int __cdecl nap_bridge_action(const char* action_json,
                                                   char* out_buf, int out_cap)
{
    char* out = NULL;
    int   out_len = 0;
    int   rc;
    if (!g_api || !g_api->action) return -10;
    if (out_buf && out_cap > 0) out_buf[0] = '\0';
    rc = g_api->action(action_json ? action_json : "", out_buf ? &out : NULL,
                       out_buf ? &out_len : NULL);
    if (rc != 0) return -11;
    if (out_buf && out && out_len > 0) {
        int n = out_len < out_cap - 1 ? out_len : out_cap - 1;
        memcpy(out_buf, out, (size_t)n);
        out_buf[n] = '\0';
        if (g_api->free) g_api->free(out);
    }
    return 0;
}

/* 版本/连通性自检：返回 ABI 版本（<0 表示桥还没 init） */
__declspec(dllexport) int __cdecl nap_bridge_abi_check(void)
{
    return g_api ? g_api->abi_version : -1;
}

/* 可选：把 UTF-8 转成宽字符给易语言做界面上显示时用（这里不做完整实现，避免误用） */
__declspec(dllexport) int __cdecl nap_bridge_struct_size(void)
{
    return (int)sizeof(NapApi);              /* 期望 40：易语言那边可以拿它核对偏移 */
}

BOOL WINAPI DllMain(HINSTANCE h, DWORD reason, LPVOID reserved)
{
    (void)h; (void)reserved;
    if (reason == DLL_PROCESS_DETACH) g_api = NULL;
    return TRUE;
}