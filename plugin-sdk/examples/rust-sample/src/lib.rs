//! 栖星 AsterCore · Rust 原生插件示例（对应 nap_plugin.h ABI v1）
//! 行为：收到群消息含"在吗/你好"时，经 api->action 回一条消息。
//!
//! 与 C 版本对齐的 ABI 结构（不要用 repr 错位）：结构体布局须与头文件一致。
//! 事件 JSON 由主程序以紧凑格式（无空格）下发。

use std::ffi::{c_char, c_int, c_void, CStr, CString};
use std::ptr;

pub const NAP_PLUGIN_ABI_VERSION: c_int = 1;

#[repr(C)]
pub struct NapApi {
    pub abi_version: c_int,
    pub action: Option<
        unsafe extern "C" fn(
            action_json: *const c_char,
            out_json: *mut *mut c_char,
            out_len: *mut c_int,
        ) -> c_int,
    >,
    pub log: Option<unsafe extern "C" fn(level: c_int, msg: *const c_char)>,
    pub alloc: Option<unsafe extern "C" fn(size: u64) -> *mut c_void>,
    pub free: Option<unsafe extern "C" fn(ptr: *mut c_void)>,
}

static mut API: Option<NapApi> = None;

fn to_str(p: *const c_char) -> String {
    if p.is_null() {
        return String::new();
    }
    unsafe { CStr::from_ptr(p) }.to_string_lossy().into_owned()
}

fn log(level: c_int, msg: &str) {
    unsafe {
        if let Some(api) = API {
            if let Some(f) = api.log {
                if let Ok(c) = CString::new(msg) {
                    f(level, c.as_ptr());
                }
            }
        }
    }
}

/// 极简提取：群文本关键字匹配（示例级，不接 serde）
fn contains_kw(text: &str) -> bool {
    ["你好", "hello", "hi", "在吗"].iter().any(|k| text.contains(k))
}

fn extract_group(ev: &str) -> String {
    // 紧凑 JSON 中 "group_id":123,
    let key = "\"group_id\":";
    match ev.find(key) {
        Some(i) => {
            let rest = &ev[i + key.len()..];
            let digits: String = rest
                .chars()
                .take_while(|c| c.is_ascii_digit())
                .collect();
            digits
        }
        None => String::new(),
    }
}

#[no_mangle]
pub extern "C" fn nap_plugin_abi_version() -> c_int {
    NAP_PLUGIN_ABI_VERSION
}

#[no_mangle]
pub extern "C" fn nap_plugin_name() -> *const c_char {
    b"nap_rs\0".as_ptr() as *const c_char
}

#[no_mangle]
pub extern "C" fn nap_plugin_version() -> *const c_char {
    b"0.1.0\0".as_ptr() as *const c_char
}

#[no_mangle]
pub extern "C" fn nap_plugin_init(config_json: *const c_char, api: *const NapApi) -> c_int {
    if api.is_null() {
        return -1;
    }
    unsafe {
        let a = &*api;
        if a.abi_version != NAP_PLUGIN_ABI_VERSION {
            return -1;
        }
        API = Some(NapApi {
            abi_version: a.abi_version,
            action: a.action,
            log: a.log,
            alloc: a.alloc,
            free: a.free,
        });
    }
    log(1, "[nap_rs] Rust 插件已初始化");
    let _ = to_str(config_json);
    0
}

#[no_mangle]
pub extern "C" fn nap_plugin_on_event(event_json: *const c_char) -> c_int {
    let ev = to_str(event_json);
    if !ev.contains("\"type\":\"message\"") {
        return 0;
    }
    // 文本粗提取（示例级）
    let text: String = ev
        .split("\"raw\":\"")
        .nth(1)
        .and_then(|s| s.split('"').next())
        .unwrap_or("")
        .to_string();
    if !contains_kw(&text) {
        return 0;
    }
    log(1, "[nap_rs] 命中: Rust 插件收到消息");
    let gid = extract_group(&ev);
    if !gid.is_empty() {
        let action = format!(
            "{{\"action\":\"send_group\",\"group_id\":\"{}\",\
             \"message\":[{{\"type\":\"text\",\"data\":{{\"text\":\"你好呀～(来自 Rust 插件)\"}}}}]}}",
            gid
        );
        unsafe {
            if let Some(api) = API {
                if let Some(f) = api.action {
                    let c = CString::new(action).unwrap();
                    f(c.as_ptr(), ptr::null_mut(), ptr::null_mut());
                }
            }
        }
    }
    1
}

#[no_mangle]
pub extern "C" fn nap_plugin_unload() {
    log(1, "[nap_rs] Rust 插件已卸载");
    unsafe { API = None }
}

#[no_mangle]
pub extern "C" fn nap_plugin_reload(_config_json: *const c_char) -> c_int {
    log(1, "[nap_rs] 配置热更（忽略内容）");
    0
}
