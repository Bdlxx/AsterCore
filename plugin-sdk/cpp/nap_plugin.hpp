// 栖星 AsterCore · C++ 原生插件 SDK（单头文件，C++11，无第三方依赖）
//
// 你要写的只有一件事：一个继承 nap::Plugin 的类 + 一行 NAP_PLUGIN(你的类)。
// 其余（nap_plugin_* 六个导出、事件 JSON 解析、动作 JSON 拼装、日志、配置）这里都包好了。
//
//   1) 复制 nap_plugin.h 与本文件到你的工程（或把 plugin-sdk 加进 include 目录）
//   2) 写插件：
//        #include "nap_plugin.hpp"
//        class Hello : public nap::Plugin {
//          const char* name() const { return "hello_cpp"; }        // = DLL 文件名
//          int on_event(const nap::Event& ev) {
//            if (ev.is_group() && ev.has_keyword("你好")) {
//              nap::send_group(ev.group_id(), "你好呀～");
//              return 1;                                            // 1=已处理
//            }
//            return 0;                                              // 0=放行
//          }
//        };
//        NAP_PLUGIN(Hello)
//   3) 编译成 **x64** DLL（见 README）：cl /LD /std:c++14 ... 或 zig c++ / g++
//
// ABI 约定（nap_plugin.h）：UTF-8 字符串、**cdecl**、返回 0/1/2。
// 本 SDK 不做任何"隐藏约定"：解析失败、Host 未 attach、动作发送失败都会如实返回 false/2。
#ifndef NAP_PLUGIN_SDK_HPP
#define NAP_PLUGIN_SDK_HPP

#include <cstdarg>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <utility>
#include <vector>

#include "nap_plugin.h"      // ABI：NapApi / NAP_EXPORT / NAP_PLUGIN_ABI_VERSION

namespace nap {

// ===========================================================================
// 一、极简 JSON（只做"解析事件 + 拼动作"这一件事，但该稳的地方都稳）
//     · UTF-8 直通；\uXXXX 与代理对（emoji 之类）正确还原
//     · 解析有深度上限（默认 64），畸形输入不会把栈撑爆
//     · 数字用 strtod + 往返校验，输出不会出现 1.1000000000000001 这种尾巴
// ===========================================================================
class Json {
public:
    enum Type { NUL, BOOL, NUM, STR, ARR, OBJ };

    Json() : type_(NUL), bool_(false), num_(0.0) {}

    static Json boolean(bool v) { Json j; j.type_ = BOOL; j.bool_ = v; return j; }
    static Json number(double v) { Json j; j.type_ = NUM; j.num_ = v; return j; }
    static Json integer(long long v) { Json j; j.type_ = NUM; j.num_ = static_cast<double>(v); return j; }
    static Json string(const std::string& v) { Json j; j.type_ = STR; j.str_ = v; return j; }
    static Json array() { Json j; j.type_ = ARR; return j; }
    static Json object() { Json j; j.type_ = OBJ; return j; }
    static const Json& null() { static const Json k_null; return k_null; }

    Type type() const { return type_; }
    bool is_null() const { return type_ == NUL; }
    bool is_bool() const { return type_ == BOOL; }
    bool is_number() const { return type_ == NUM; }
    bool is_string() const { return type_ == STR; }
    bool is_array() const { return type_ == ARR; }
    bool is_object() const { return type_ == OBJ; }

    bool as_bool(bool fallback = false) const { return type_ == BOOL ? bool_ : fallback; }
    double as_double(double fallback = 0.0) const { return type_ == NUM ? num_ : fallback; }
    long long as_int(long long fallback = 0) const {
        return type_ == NUM ? static_cast<long long>(num_) : fallback;
    }
    const std::string& as_string() const {
        static const std::string k_empty;
        return type_ == STR ? str_ : k_empty;
    }
    /// 数字/字符串都试着当整数用（事件里的 group_id 可能是 "123" 也可能是 123）
    long long as_id(long long fallback = 0) const {
        if (type_ == NUM) return static_cast<long long>(num_);
        if (type_ == STR && !str_.empty()) return static_cast<long long>(strtoll(str_.c_str(), 0, 10));
        return fallback;
    }

    // ---- 数组 / 对象 ----
    std::size_t size() const {
        if (type_ == ARR) return arr_.size();
        if (type_ == OBJ) return obj_.size();
        return 0;
    }
    const Json& at(std::size_t i) const {
        return (type_ == ARR && i < arr_.size()) ? arr_[i] : null();
    }
    void push(const Json& v) {
        if (type_ != ARR) { type_ = ARR; arr_.clear(); }
        arr_.push_back(v);
    }
    bool has(const std::string& key) const {
        if (type_ != OBJ) return false;
        for (std::size_t i = 0; i < obj_.size(); ++i)
            if (obj_[i].first == key) return true;
        return false;
    }
    const Json& get(const std::string& key) const {
        if (type_ == OBJ) {
            for (std::size_t i = 0; i < obj_.size(); ++i)
                if (obj_[i].first == key) return obj_[i].second;
        }
        return null();
    }
    std::string get_str(const std::string& key, const std::string& fallback = "") const {
        const Json& v = get(key);
        return v.is_string() ? v.as_string() : fallback;
    }
    long long get_id(const std::string& key, long long fallback = 0) const {
        return get(key).as_id(fallback);
    }
    void set(const std::string& key, const Json& v) {
        if (type_ != OBJ) { type_ = OBJ; obj_.clear(); }
        for (std::size_t i = 0; i < obj_.size(); ++i) {
            if (obj_[i].first == key) { obj_[i].second = v; return; }
        }
        obj_.push_back(std::make_pair(key, v));
    }
    Json& operator[](const std::string& key) {
        if (type_ != OBJ) { type_ = OBJ; obj_.clear(); }
        for (std::size_t i = 0; i < obj_.size(); ++i) {
            if (obj_[i].first == key) return obj_[i].second;
        }
        obj_.push_back(std::make_pair(key, Json()));
        return obj_.back().second;
    }

    // ---- 序列化（紧凑；UTF-8 原样输出，控制字符转义） ----
    std::string dump() const {
        std::string out;
        dump_to(out);
        return out;
    }

    // ---- 解析 ----
    static bool parse(const std::string& text, Json* out, std::string* err = 0) {
        Parser p(text);
        Json v;
        p.skip_ws();
        if (!p.value(v, 0)) {
            if (err) *err = p.error.empty() ? std::string("JSON 解析失败") : p.error;
            return false;
        }
        p.skip_ws();
        if (p.pos != p.end) {
            if (err) *err = "JSON 尾部有多余内容";
            return false;
        }
        if (out) *out = v;
        return true;
    }

    static const std::size_t kMaxDepth = 64;

private:
    struct Parser {
        const std::string& s;
        std::size_t pos;
        std::size_t end;
        std::string error;

        explicit Parser(const std::string& src) : s(src), pos(0), end(src.size()) {}

        bool fail(const char* what) {
            if (error.empty()) {
                char buf[128];
                std::snprintf(buf, sizeof(buf), "%s（第 %lu 字节处）", what,
                              static_cast<unsigned long>(pos));
                error = buf;
            }
            return false;
        }
        void skip_ws() {
            while (pos < end) {
                char c = s[pos];
                if (c == ' ' || c == '\t' || c == '\n' || c == '\r') ++pos;
                else break;
            }
        }
        bool value(Json& out, int depth) {
            if (depth > static_cast<int>(kMaxDepth)) return fail("JSON 嵌套太深");
            skip_ws();
            if (pos >= end) return fail("JSON 意外结束");
            char c = s[pos];
            if (c == '{') return object(out, depth);
            if (c == '[') return array(out, depth);
            if (c == '"') {
                std::string str;
                if (!string_literal(str)) return false;
                out = Json::string(str);
                return true;
            }
            if (c == 't' || c == 'f') return boolean(out);
            if (c == 'n') return null_value(out);
            return number(out);
        }
        bool boolean(Json& out) {
            if (end - pos >= 4 && s.compare(pos, 4, "true") == 0) { pos += 4; out = Json::boolean(true); return true; }
            if (end - pos >= 5 && s.compare(pos, 5, "false") == 0) { pos += 5; out = Json::boolean(false); return true; }
            return fail("JSON 布尔值不合法");
        }
        bool null_value(Json& out) {
            if (end - pos >= 4 && s.compare(pos, 4, "null") == 0) { pos += 4; out = Json(); return true; }
            return fail("JSON null 不合法");
        }
        bool number(Json& out) {
            const char* begin = s.c_str() + pos;
            char* stop = 0;
            double v = std::strtod(begin, &stop);
            if (stop == begin) return fail("JSON 数字不合法");
            pos += static_cast<std::size_t>(stop - begin);
            out = Json::number(v);
            return true;
        }
        bool string_literal(std::string& out) {
            if (s[pos] != '"') return fail("JSON 字符串少了引号");
            ++pos;
            out.clear();
            while (pos < end) {
                unsigned char c = static_cast<unsigned char>(s[pos]);
                if (c == '"') { ++pos; return true; }
                if (c == '\\') {
                    ++pos;
                    if (pos >= end) return fail("JSON 转义序列不完整");
                    char e = s[pos++];
                    switch (e) {
                        case '"':  out.push_back('"');  break;
                        case '\\': out.push_back('\\'); break;
                        case '/':  out.push_back('/');  break;
                        case 'b':  out.push_back('\b'); break;
                        case 'f':  out.push_back('\f'); break;
                        case 'n':  out.push_back('\n'); break;
                        case 'r':  out.push_back('\r'); break;
                        case 't':  out.push_back('\t'); break;
                        case 'u': {
                            unsigned int cp = 0;
                            if (!hex4(cp)) return false;
                            if (cp >= 0xD800 && cp <= 0xDBFF) {          // 高位代理 → 找低位
                                if (pos + 1 < end && s[pos] == '\\' && s[pos + 1] == 'u') {
                                    pos += 2;
                                    unsigned int low = 0;
                                    if (!hex4(low)) return false;
                                    if (low >= 0xDC00 && low <= 0xDFFF) {
                                        cp = 0x10000 + ((cp - 0xD800) << 10) + (low - 0xDC00);
                                    } else {
                                        append_utf8(out, cp);                // 孤立的代理，按原值输出
                                        cp = low;
                                    }
                                }
                            }
                            append_utf8(out, cp);
                            break;
                        }
                        default: return fail("JSON 转义字符不认识");
                    }
                    continue;
                }
                if (c < 0x20) return fail("JSON 字符串里有未转义的控制字符");
                out.push_back(static_cast<char>(c));
                ++pos;
            }
            return fail("JSON 字符串没有收尾引号");
        }
        bool hex4(unsigned int& out) {
            if (pos + 4 > end) return fail("\\u 后面不足 4 位");
            unsigned int v = 0;
            for (int i = 0; i < 4; ++i) {
                char c = s[pos++];
                v <<= 4;
                if (c >= '0' && c <= '9') v |= static_cast<unsigned int>(c - '0');
                else if (c >= 'a' && c <= 'f') v |= static_cast<unsigned int>(c - 'a' + 10);
                else if (c >= 'A' && c <= 'F') v |= static_cast<unsigned int>(c - 'A' + 10);
                else return fail("\\u 里有非十六进制字符");
            }
            out = v;
            return true;
        }
        static void append_utf8(std::string& out, unsigned int cp) {
            if (cp <= 0x7F) {
                out.push_back(static_cast<char>(cp));
            } else if (cp <= 0x7FF) {
                out.push_back(static_cast<char>(0xC0 | (cp >> 6)));
                out.push_back(static_cast<char>(0x80 | (cp & 0x3F)));
            } else if (cp <= 0xFFFF) {
                out.push_back(static_cast<char>(0xE0 | (cp >> 12)));
                out.push_back(static_cast<char>(0x80 | ((cp >> 6) & 0x3F)));
                out.push_back(static_cast<char>(0x80 | (cp & 0x3F)));
            } else {
                out.push_back(static_cast<char>(0xF0 | (cp >> 18)));
                out.push_back(static_cast<char>(0x80 | ((cp >> 12) & 0x3F)));
                out.push_back(static_cast<char>(0x80 | ((cp >> 6) & 0x3F)));
                out.push_back(static_cast<char>(0x80 | (cp & 0x3F)));
            }
        }
        bool array(Json& out, int depth) {
            ++pos;                                     // '['
            out = Json::array();
            skip_ws();
            if (pos < end && s[pos] == ']') { ++pos; return true; }
            for (;;) {
                Json item;
                if (!value(item, depth + 1)) return false;
                out.push(item);
                skip_ws();
                if (pos >= end) return fail("JSON 数组没有收尾 ]");
                if (s[pos] == ',') { ++pos; continue; }
                if (s[pos] == ']') { ++pos; return true; }
                return fail("JSON 数组里该是 , 或 ]");
            }
        }
        bool object(Json& out, int depth) {
            ++pos;                                     // '{'
            out = Json::object();
            skip_ws();
            if (pos < end && s[pos] == '}') { ++pos; return true; }
            for (;;) {
                skip_ws();
                std::string key;
                if (pos >= end || s[pos] != '"') return fail("JSON 对象的键必须是字符串");
                if (!string_literal(key)) return false;
                skip_ws();
                if (pos >= end || s[pos] != ':') return fail("JSON 对象的键后面少了 :");
                ++pos;
                Json item;
                if (!value(item, depth + 1)) return false;
                out.set(key, item);
                skip_ws();
                if (pos >= end) return fail("JSON 对象没有收尾 }");
                if (s[pos] == ',') { ++pos; continue; }
                if (s[pos] == '}') { ++pos; return true; }
                return fail("JSON 对象里该是 , 或 }");
            }
        }
    };

    void dump_to(std::string& out) const {
        switch (type_) {
            case NUL:  out += "null"; break;
            case BOOL: out += (bool_ ? "true" : "false"); break;
            case NUM:  out += format_number(num_); break;
            case STR:  escape_into(str_, out); break;
            case ARR:
                out.push_back('[');
                for (std::size_t i = 0; i < arr_.size(); ++i) {
                    if (i) out.push_back(',');
                    arr_[i].dump_to(out);
                }
                out.push_back(']');
                break;
            case OBJ:
                out.push_back('{');
                for (std::size_t i = 0; i < obj_.size(); ++i) {
                    if (i) out.push_back(',');
                    escape_into(obj_[i].first, out);
                    out.push_back(':');
                    obj_[i].second.dump_to(out);
                }
                out.push_back('}');
                break;
        }
    }
    static std::string format_number(double v) {
        char buf[40];
        if (v == static_cast<double>(static_cast<long long>(v)) &&
            v > -1e15 && v < 1e15) {                        // 整数就按整数写（group_id 这类）
            std::snprintf(buf, sizeof(buf), "%lld", static_cast<long long>(v));
            return std::string(buf);
        }
        std::snprintf(buf, sizeof(buf), "%.15g", v);         // 先用 15 位
        if (std::strtod(buf, 0) != v) std::snprintf(buf, sizeof(buf), "%.17g", v);
        return std::string(buf);
    }
    static void escape_into(const std::string& s, std::string& out) {
        out.push_back('"');
        for (std::size_t i = 0; i < s.size(); ++i) {
            unsigned char c = static_cast<unsigned char>(s[i]);
            switch (c) {
                case '"':  out += "\\\""; break;
                case '\\': out += "\\\\"; break;
                case '\b': out += "\\b"; break;
                case '\f': out += "\\f"; break;
                case '\n': out += "\\n"; break;
                case '\r': out += "\\r"; break;
                case '\t': out += "\\t"; break;
                default:
                    if (c < 0x20) {
                        char buf[8];
                        std::snprintf(buf, sizeof(buf), "\\u%04x", c);
                        out += buf;
                    } else {
                        out.push_back(static_cast<char>(c));   // UTF-8 直通
                    }
            }
        }
        out.push_back('"');
    }

    Type type_;
    bool bool_;
    double num_;
    std::string str_;
    std::vector<Json> arr_;
    std::vector<std::pair<std::string, Json> > obj_;
};

// ===========================================================================
// 二、事件（宿主传进来的 UTF-8 JSON → 好用的取值接口）
// ===========================================================================
class Event {
public:
    Event() : ok_(false) {}

    bool parse(const std::string& raw_json) {
        raw_ = raw_json;
        std::string err;
        ok_ = Json::parse(raw_json, &json_, &err);
        if (!ok_) error_ = err;
        return ok_;
    }

    bool ok() const { return ok_; }
    const std::string& error() const { return error_; }
    const std::string& raw() const { return raw_; }
    const Json& json() const { return json_; }

    std::string post_type() const { return json_.get_str("post_type", json_.get_str("type")); }
    std::string message_type() const { return json_.get_str("message_type"); }
    bool is_group() const { return message_type() == "group"; }
    bool is_private() const { return message_type() == "private"; }

    /// 来源账号（原生事件里可能是数字也可能是字符串，统一按字符串给）
    std::string account_id() const {
        const Json& v = json_.get("account_id");
        if (v.is_string()) return v.as_string();
        if (v.is_number()) return v.dump();
        return "";
    }
    std::string platform() const { return json_.get_str("platform"); }
    std::string reply_token() const { return json_.get_str("reply_token"); }

    long long group_id() const { return json_.get_id("group_id", 0); }
    long long user_id() const { return json_.get_id("user_id", 0); }
    long long self_id() const { return json_.get_id("self_id", 0); }

    /// 纯文本：**两种事件形状都认** —— 原生插件拿到的是本框架的 raw/segments，
    /// OneBot v11 形状（老插件/离线自测）是 raw_message/message。都没有就把 text 段拼起来。
    std::string raw_message() const {
        const Json& raw = json_.get("raw_message");
        if (raw.is_string() && !raw.as_string().empty()) return raw.as_string();
        const Json& raw2 = json_.get("raw");
        if (raw2.is_string() && !raw2.as_string().empty()) return raw2.as_string();
        std::string out;
        const Json& segs = segments();
        for (std::size_t i = 0; i < segs.size(); ++i) {
            const Json& seg = segs.at(i);
            if (seg.get_str("type") == "text") out += seg.get("data").get_str("text");
        }
        return out;
    }

    /// 消息段：原生事件叫 segments，OneBot 形状叫 message（两种都返回；都没有则返回 null）
    const Json& segments() const {
        const Json& m = json_.get("message");
        if (m.is_array()) return m;
        return json_.get("segments");
    }

    /// 文本里是否含某子串（UTF-8 按字节比较；中文关键词也能正确命中）
    bool has_keyword(const std::string& kw) const {
        if (kw.empty()) return false;
        return raw_message().find(kw) != std::string::npos;
    }

    /// 是否是发给"我"的（@本机器人）—— 事件段里有 at 且 qq == self_id
    bool at_me() const {
        const Json& segs = segments();
        for (std::size_t i = 0; i < segs.size(); ++i) {
            const Json& seg = segs.at(i);
            if (seg.get_str("type") != "at") continue;
            const Json& data = seg.get("data");
            long long qq = data.get("qq").as_id(0);
            if (qq == self_id() || data.get_str("qq") == "all") return true;
        }
        return false;
    }

private:
    bool ok_;
    std::string raw_;
    std::string error_;
    Json json_;
};

// ===========================================================================
// 三、宿主回调表（nap_plugin_init 拿到的 NapApi）
// ===========================================================================
class Host {
public:
    static Host& instance() {
        static Host inst;
        return inst;
    }

    void attach(const NapApi* api) {
        if (!api) return;
        api_ = *api;                                  // 拷一份，免得宿主提前释放
        ready_ = true;
    }
    bool ready() const { return ready_; }
    int abi_version() const { return ready_ ? api_.abi_version : 0; }

    /// init 时传入的配置 JSON（解析失败时是 null）
    const Json& config() const { return config_; }

    void log(int level, const std::string& msg) const {
        if (ready_ && api_.log) api_.log(level, msg.c_str());
    }
    bool action(const std::string& action_json, std::string* out = 0) const {
        if (!ready_ || !api_.action) return false;
        char* buf = 0;
        int len = 0;
        int rc = api_.action(action_json.c_str(), out ? &buf : 0, out ? &len : 0);
        if (out) {
            if (buf && len > 0) out->assign(buf, static_cast<std::size_t>(len));
            else out->clear();
            if (buf && api_.free) api_.free(buf);
        }
        return rc == 0;
    }
    bool action(const Json& json, std::string* out = 0) const { return action(json.dump(), out); }

    bool send_group(long long group_id, const Json& message) const {
        Json a = Json::object();
        a.set("action", Json::string("send_group"));
        a.set("group_id", Json::integer(group_id));
        a.set("message", message);
        return action(a);
    }
    bool send_group(long long group_id, const std::string& text) const {
        return send_group(group_id, message_of_text(text));
    }
    bool send_private(long long user_id, const Json& message) const {
        Json a = Json::object();
        a.set("action", Json::string("send_private"));
        a.set("user_id", Json::integer(user_id));
        a.set("message", message);
        return action(a);
    }
    bool send_private(long long user_id, const std::string& text) const {
        return send_private(user_id, message_of_text(text));
    }

    static Json message_of_text(const std::string& text) {
        Json data = Json::object();
        data.set("text", Json::string(text));
        Json seg = Json::object();
        seg.set("type", Json::string("text"));
        seg.set("data", data);
        Json arr = Json::array();
        arr.push(seg);
        return arr;
    }

    /// 解析 init 的配置（Host::instance().attach 之后才有意义）
    void load_config(const char* config_json) {
        if (!config_json) return;
        std::string err;
        Json parsed;
        if (Json::parse(config_json, &parsed, &err)) config_ = parsed;
    }

private:
    Host() : ready_(false) {
        std::memset(&api_, 0, sizeof(api_));
    }
    NapApi api_;
    bool ready_;
    Json config_;
};

// ---- 便捷函数（插件里最常用的几个） ----
inline bool send_group(long long group_id, const std::string& text) {
    return Host::instance().send_group(group_id, text);
}
inline bool send_group(long long group_id, const Json& message) {
    return Host::instance().send_group(group_id, message);
}
inline bool send_private(long long user_id, const std::string& text) {
    return Host::instance().send_private(user_id, text);
}
inline bool send_private(long long user_id, const Json& message) {
    return Host::instance().send_private(user_id, message);
}
inline void log_debug(const std::string& m) { Host::instance().log(0, m); }
inline void log_info(const std::string& m)  { Host::instance().log(1, m); }
inline void log_warn(const std::string& m)  { Host::instance().log(2, m); }
inline void log_error(const std::string& m) { Host::instance().log(3, m); }

/// printf 风格日志（超长自动截断，不会因为格式化出错就崩）
inline void logf(int level, const char* fmt, ...) {
    char buf[1024];
    va_list ap;
    va_start(ap, fmt);
    int n = std::vsnprintf(buf, sizeof(buf), fmt, ap);
    va_end(ap);
    if (n < 0) return;
    buf[sizeof(buf) - 1] = '\0';
    Host::instance().log(level, std::string(buf));
}

inline Json seg_text(const std::string& text) {
    Json data = Json::object();
    data.set("text", Json::string(text));
    Json seg = Json::object();
    seg.set("type", Json::string("text"));
    seg.set("data", data);
    return seg;
}
inline Json seg_image(const std::string& file_or_url) {
    Json data = Json::object();
    data.set("file", Json::string(file_or_url));
    Json seg = Json::object();
    seg.set("type", Json::string("image"));
    seg.set("data", data);
    return seg;
}
inline Json seg_at(long long user_id) {
    Json data = Json::object();
    data.set("qq", Json::integer(user_id));
    Json seg = Json::object();
    seg.set("type", Json::string("at"));
    seg.set("data", data);
    return seg;
}
inline Json message(const std::vector<Json>& segments) {
    Json arr = Json::array();
    for (std::size_t i = 0; i < segments.size(); ++i) arr.push(segments[i]);
    return arr;
}

/// 编译期自检：源码里的中文字面量到底被编成了什么编码？
///
/// MSVC 默认按"系统 ANSI"（中文系统 = GBK）编窄字面量，GCC/Clang 默认 UTF-8。
/// 不开 `/utf-8`（MSVC）或 `-fexec-charset=UTF-8`（GCC/Clang）时，
/// `has_keyword("你好")` 拿 GBK 字节去比 UTF-8 事件文本 → **永远匹配不上**，
/// 而且一点报错都没有（表现只是"插件像没生效"）。所以这里做一次运行期自检，
/// 由 NAP_PLUGIN 生成的 init 里调用，编错了会直接打一条 error 日志。
inline bool utf8_literals_ok() {
    static const char k_plain[] = "你好";                     // 跟随编译器执行字符集
    static const char k_utf8[] = "\xe4\xbd\xa0\xe5\xa5\xbd";  // 明确的 UTF-8（你 好）
    return sizeof(k_plain) == sizeof(k_utf8) &&
           std::memcmp(k_plain, k_utf8, sizeof(k_utf8)) == 0;
}

// ===========================================================================
// 四、插件基类 + 一行注册
// ===========================================================================
class Plugin {
public:
    virtual ~Plugin() {}
    /// 英文名（= DLL 文件名，别带空格）
    virtual const char* name() const = 0;
    virtual const char* version() const { return "1.0.0"; }
    virtual const char* description() const { return ""; }
    virtual const char* author() const { return ""; }
    /// init / reload 都会调；返回 0 表示没问题（返回非 0 宿主会记日志）
    virtual int on_config(const char* config_json) { (void)config_json; return 0; }
    /// 收到事件：0=放行 1=已处理 2=内部错误（与 ABI 一致）
    virtual int on_event(const Event& event) = 0;
    virtual void on_unload() {}
};

}  // namespace nap

/// 一行把插件类变成合规 DLL：生成 nap_plugin_* 六个导出（外加可选的 reload）
/// 用法：写完类之后在文件末尾写 NAP_PLUGIN(你的类名)
#define NAP_PLUGIN(CLASS)                                                          \
    namespace {                                                                    \
    CLASS& nap_plugin_singleton() {                                                \
        static CLASS instance;                                                     \
        return instance;                                                           \
    }                                                                              \
    }                                                                              \
    extern "C" {                                                                   \
    NAP_EXPORT int nap_plugin_abi_version(void) { return NAP_PLUGIN_ABI_VERSION; } \
    NAP_EXPORT const char* nap_plugin_name(void) {                                 \
        return nap_plugin_singleton().name();                                      \
    }                                                                              \
    NAP_EXPORT const char* nap_plugin_version(void) {                              \
        return nap_plugin_singleton().version();                                   \
    }                                                                              \
    NAP_EXPORT int nap_plugin_init(const char* config_json, const NapApi* api) {    \
        if (!api || api->abi_version != NAP_PLUGIN_ABI_VERSION) return -1;         \
        nap::Host::instance().attach(api);                                         \
        if (!nap::utf8_literals_ok()) {                                            \
            nap::log_error("编译时没开 UTF-8：中文字面量按系统 ANSI 编了，"        \
                           "关键词匹配会永远失败。MSVC 加 /utf-8；"                \
                           "GCC/Clang 加 -fexec-charset=UTF-8。");                 \
        }                                                                          \
        nap::Host::instance().load_config(config_json);                            \
        return nap_plugin_singleton().on_config(config_json);                      \
    }                                                                              \
    NAP_EXPORT int nap_plugin_on_event(const char* event_json) {                   \
        if (!event_json) return 2;                                                 \
        nap::Event event;                                                          \
        if (!event.parse(event_json)) {                                            \
            nap::logf(2, "事件 JSON 解析失败：%s", event.error().c_str());          \
            return 2;                                                              \
        }                                                                          \
        return nap_plugin_singleton().on_event(event);                             \
    }                                                                              \
    NAP_EXPORT void nap_plugin_unload(void) { nap_plugin_singleton().on_unload(); } \
    NAP_EXPORT int nap_plugin_reload(const char* config_json) {                    \
        nap::Host::instance().load_config(config_json);                            \
        return nap_plugin_singleton().on_config(config_json);                      \
    }                                                                              \
    }

#endif  // NAP_PLUGIN_SDK_HPP
