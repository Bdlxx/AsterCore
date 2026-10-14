// 栖星 AsterCore · C++ SDK 自带单元测试（编成 exe 直接跑；退出码 0 = 全过）
//
// 编：python -m ziglang c++ -target x86_64-windows-gnu -std=c++11 -O2 -I .. -o test_sdk.exe test_sdk.cpp
// 跑：test_sdk.exe
#include "../nap_plugin.hpp"

#include <cstdio>
#include <string>
#include <vector>

static int g_total = 0;
static int g_failed = 0;

#define CHECK(cond)                                                            \
    do {                                                                       \
        ++g_total;                                                             \
        if (!(cond)) {                                                         \
            ++g_failed;                                                        \
            std::printf("  ✗ 第 %d 行: %s\n", __LINE__, #cond);                 \
        }                                                                      \
    } while (0)

#define CHECK_EQ(a, b)                                                         \
    do {                                                                       \
        ++g_total;                                                             \
        if (!((a) == (b))) {                                                   \
            ++g_failed;                                                        \
            std::printf("  ✗ 第 %d 行: %s != %s\n", __LINE__, #a, #b);          \
        }                                                                      \
    } while (0)

#define SECTION(name) std::printf("[%s]\n", name)

// --------------------------------------------------------------------------
// 假的宿主回调表：把 SDK 发出去的动作/日志抓下来断言
// --------------------------------------------------------------------------
static std::string g_last_action;
static std::vector<std::string> g_logs;
static int g_action_rc = 0;

static int fake_action(const char* json, char** out_json, int* out_len) {
    g_last_action = json ? json : "";
    if (out_json) *out_json = 0;
    if (out_len) *out_len = 0;
    return g_action_rc;
}
static void fake_log(int level, const char* msg) {
    char buf[32];
    std::snprintf(buf, sizeof(buf), "%d:", level);
    g_logs.push_back(std::string(buf) + (msg ? msg : ""));
}
static void* fake_alloc(unsigned long long size) { return std::malloc(static_cast<std::size_t>(size)); }
static void fake_free(void* p) { std::free(p); }

static NapApi make_api() {
    NapApi api;
    std::memset(&api, 0, sizeof(api));
    api.abi_version = NAP_PLUGIN_ABI_VERSION;
    api.action = fake_action;
    api.log = fake_log;
    api.alloc = fake_alloc;
    api.free = fake_free;
    return api;
}

// --------------------------------------------------------------------------
// 被测插件（顺便验证 NAP_PLUGIN 宏生成的导出）
// --------------------------------------------------------------------------
class TestPlugin : public nap::Plugin {
public:
    const char* name() const { return "test_plugin"; }
    const char* version() const { return "9.9.9"; }
    const char* description() const { return "测试用"; }
    const char* author() const { return "tests"; }
    int on_config(const char* cfg) {
        cfg_ = cfg ? cfg : "";
        return 0;
    }
    int on_event(const nap::Event& ev) {
        if (ev.is_group() && ev.has_keyword("你好")) {
            nap::send_group(ev.group_id(), "你好呀～");
            return 1;
        }
        return 0;
    }
    std::string cfg_;
};

NAP_PLUGIN(TestPlugin)

// --------------------------------------------------------------------------
static void test_json_parse() {
    SECTION("JSON 解析");
    nap::Json j;
    std::string err;
    CHECK(nap::Json::parse("{\"a\":1,\"b\":\"x\",\"c\":[true,null,2.5],\"d\":{\"e\":\"\"}}", &j, &err));
    CHECK(j.is_object());
    CHECK_EQ(j.get("a").as_int(-1), 1);
    CHECK(j.get("a").is_number());
    CHECK_EQ(j.get("b").as_string(), std::string("x"));
    CHECK(j.get("c").is_array());
    CHECK_EQ(j.get("c").size(), static_cast<std::size_t>(3));
    CHECK(j.get("c").at(0).as_bool());
    CHECK(j.get("c").at(1).is_null());
    CHECK(j.get("c").at(2).as_double() > 2.4);
    CHECK(j.get("d").get("e").is_string());
    CHECK(!j.has("nope"));
    CHECK(j.get("nope").is_null());
    CHECK(j.get("c").at(99).is_null());            // 越界 → null，不崩

    // 数字/字符串都能当 ID 用（事件里两种都可能出现）
    nap::Json id;
    CHECK(nap::Json::parse("{\"group_id\":123456}", &id, &err));
    CHECK_EQ(id.get("group_id").as_id(0), 123456LL);
    CHECK(nap::Json::parse("{\"group_id\":\"123456\"}", &id, &err));
    CHECK_EQ(id.get("group_id").as_id(0), 123456LL);
}

static void test_json_strings() {
    SECTION("JSON 字符串/转义/UTF-8");
    nap::Json j;
    std::string err;
    CHECK(nap::Json::parse("\"\\\"\\\\\\/\\b\\f\\n\\r\\t\"", &j, &err));
    CHECK_EQ(j.as_string(), std::string("\"\\/\b\f\n\r\t"));

    CHECK(nap::Json::parse("\"\\u4f60\\u597d\"", &j, &err));      // 你好
    CHECK_EQ(j.as_string(), std::string("你好"));

    CHECK(nap::Json::parse("\"\\ud83d\\ude00\"", &j, &err));      // 😀（代理对）
    CHECK_EQ(j.as_string().size(), static_cast<std::size_t>(4));
    CHECK_EQ(j.as_string(), std::string("\xf0\x9f\x98\x80"));

    // 原样 UTF-8（中文、emoji）解析后不变
    CHECK(nap::Json::parse("{\"raw_message\":\"你好 😀\"}", &j, &err));
    CHECK_EQ(j.get("raw_message").as_string(), std::string("你好 😀"));

    // 控制字符必须转义，不能直接塞进字符串
    CHECK(!nap::Json::parse(std::string("\"a\tb\""), &j, &err));
}

static void test_json_errors() {
    SECTION("JSON 错误输入要报错而不是崩");
    const char* bad[] = {
        "{\"a\":}", "[1,]", "{\"a\" 1}", "[1 2]", "\"abc", "{}extra", "", "{\"a\":1"
    };
    for (std::size_t i = 0; i < sizeof(bad) / sizeof(bad[0]); ++i) {
        nap::Json j;
        std::string err;
        CHECK(!nap::Json::parse(bad[i], &j, &err));
        CHECK(!err.empty());
    }
    // 深度上限（不设限会被恶意输入把栈撑爆）
    std::string deep;
    for (int i = 0; i < 200; ++i) deep += "[";
    nap::Json j;
    std::string err;
    CHECK(!nap::Json::parse(deep, &j, &err));
}

static void test_json_dump() {
    SECTION("JSON 序列化");
    nap::Json obj = nap::Json::object();
    obj["action"] = nap::Json::string("send_group");
    obj["group_id"] = nap::Json::integer(123456);
    obj["flag"] = nap::Json::boolean(true);
    obj["ratio"] = nap::Json::number(1.1);
    obj["none"] = nap::Json();
    CHECK_EQ(obj.dump(),
             std::string("{\"action\":\"send_group\",\"group_id\":123456,\"flag\":true,"
                         "\"ratio\":1.1,\"none\":null}"));

    // 转义 + 往返
    const char* tricky[] = {
        "hello \"world\"", "back\\slash", "line\nbreak", "你好呀～", "😀", "\x01\x02"
    };
    for (std::size_t i = 0; i < sizeof(tricky) / sizeof(tricky[0]); ++i) {
        nap::Json s = nap::Json::string(tricky[i]);
        nap::Json back;
        std::string err;
        CHECK(nap::Json::parse(s.dump(), &back, &err));
        CHECK_EQ(back.as_string(), std::string(tricky[i]));
    }
    // 整数不要出现 1.0000000000000001 这种尾巴
    CHECK_EQ(nap::Json::number(1234567890123.0).dump(), std::string("1234567890123"));
}

static void test_event() {
    SECTION("事件解析");
    const std::string ev_json =
        "{\"post_type\":\"message\",\"message_type\":\"group\",\"raw_message\":\"你好呀\","
        "\"user_id\":123456789,\"group_id\":957918829,\"self_id\":10001,\"time\":0,"
        "\"message\":[{\"type\":\"at\",\"data\":{\"qq\":\"10001\"}},"
        "{\"type\":\"text\",\"data\":{\"text\":\"你好\"}}]}";
    nap::Event ev;
    CHECK(ev.parse(ev_json));
    CHECK(ev.ok());
    CHECK_EQ(ev.message_type(), std::string("group"));
    CHECK(ev.is_group());
    CHECK(!ev.is_private());
    CHECK_EQ(ev.group_id(), 957918829LL);
    CHECK_EQ(ev.user_id(), 123456789LL);
    CHECK_EQ(ev.self_id(), 10001LL);
    CHECK_EQ(ev.raw_message(), std::string("你好呀"));
    CHECK(ev.has_keyword("你好"));
    CHECK(!ev.has_keyword("签到"));
    CHECK(ev.at_me());
    CHECK_EQ(ev.segments().size(), static_cast<std::size_t>(2));

    // 没有 raw_message 时用 text 段拼
    const std::string ev2 =
        "{\"post_type\":\"message\",\"message_type\":\"group\",\"group_id\":1,\"self_id\":9,"
        "\"message\":[{\"type\":\"text\",\"data\":{\"text\":\"签到\"}},"
        "{\"type\":\"text\",\"data\":{\"text\":\"吧\"}}]}";
    nap::Event e2;
    CHECK(e2.parse(ev2));
    CHECK_EQ(e2.raw_message(), std::string("签到吧"));
    CHECK(!e2.at_me());                       // 没 @ 我

    // 坏 JSON：不崩，ok()=false 并带错误信息
    nap::Event bad;
    CHECK(!bad.parse("{不是 JSON"));
    CHECK(!bad.ok());
    CHECK(!bad.error().empty());
    CHECK(bad.raw_message().empty());

    // ⚠ **原生插件实际收到的是这个形状**（宿主 Event.to_dict()：type / raw / segments）
    //    —— 与 OneBot 形状不同，SDK 必须两种都认（否则真机上"关键词一个都匹配不上"）
    const std::string native_ev =
        "{\"type\":\"message\",\"account_id\":\"10001\",\"platform\":\"onebot\",\"time\":0,"
        "\"user_id\":10000,\"group_id\":5,\"self_id\":\"1\",\"message_type\":\"group\","
        "\"raw\":\"你好\",\"segments\":[{\"type\":\"text\",\"data\":{\"text\":\"你好\"}}],"
        "\"reply_token\":\"tk-1\"}";
    nap::Event nv;
    CHECK(nv.parse(native_ev));
    CHECK(nv.is_group());
    CHECK_EQ(nv.group_id(), 5LL);
    CHECK_EQ(nv.raw_message(), std::string("你好"));       // 认 raw
    CHECK(nv.has_keyword("你好"));
    CHECK_EQ(nv.segments().size(), static_cast<std::size_t>(1));
    CHECK_EQ(nv.account_id(), std::string("10001"));
    CHECK_EQ(nv.platform(), std::string("onebot"));
    CHECK_EQ(nv.reply_token(), std::string("tk-1"));

    // 原生形状 + 没有 raw → 用 segments 里的 text 段拼
    const std::string native2 =
        "{\"type\":\"message\",\"message_type\":\"group\",\"group_id\":5,\"self_id\":1,"
        "\"segments\":[{\"type\":\"at\",\"data\":{\"qq\":\"1\"}},"
        "{\"type\":\"text\",\"data\":{\"text\":\"签到\"}}]}";
    nap::Event n2;
    CHECK(n2.parse(native2));
    CHECK_EQ(n2.raw_message(), std::string("签到"));
    CHECK(n2.at_me());
}

/// ⚠ 必须在**任何** attach 之前跑（Host 是进程内单例，attach 之后就不"空"了）
static void test_host_not_attached() {
    SECTION("还没 attach 时不该崩");
    CHECK(!nap::Host::instance().ready());
    CHECK_EQ(nap::Host::instance().abi_version(), 0);
    CHECK(!nap::send_group(1, "x"));            // 动作失败，但不是崩溃
    nap::log_info("没 attach 时的日志：丢掉即可");  // 不该崩
    nap::Json cfg = nap::Host::instance().config();
    CHECK(cfg.is_null());
}

static void test_host_actions() {
    SECTION("动作/日志（走宿回调表）");
    NapApi api = make_api();
    nap::Host::instance().attach(&api);
    CHECK(nap::Host::instance().ready());
    CHECK_EQ(nap::Host::instance().abi_version(), NAP_PLUGIN_ABI_VERSION);

    g_last_action.clear();
    CHECK(nap::send_group(957918829, "你好呀～"));
    nap::Json sent;
    std::string err;
    CHECK(nap::Json::parse(g_last_action, &sent, &err));
    CHECK_EQ(sent.get("action").as_string(), std::string("send_group"));
    CHECK_EQ(sent.get("group_id").as_id(0), 957918829LL);
    CHECK_EQ(sent.get("message").at(0).get("data").get_str("text"), std::string("你好呀～"));

    // 图文混排 + @
    std::vector<nap::Json> segs;
    segs.push_back(nap::seg_at(10001));
    segs.push_back(nap::seg_text("看这个："));
    segs.push_back(nap::seg_image("https://example.com/a.png"));
    CHECK(nap::send_private(10000, nap::message(segs)));
    CHECK(nap::Json::parse(g_last_action, &sent, &err));
    CHECK_EQ(sent.get("action").as_string(), std::string("send_private"));
    CHECK_EQ(sent.get("user_id").as_id(0), 10000LL);
    CHECK_EQ(sent.get("message").size(), static_cast<std::size_t>(3));
    CHECK_EQ(sent.get("message").at(2).get("data").get_str("file"),
             std::string("https://example.com/a.png"));

    // 宿说"动作失败" → 我们也得如实返回 false
    g_action_rc = -1;
    CHECK(!nap::send_group(1, "x"));
    g_action_rc = 0;

    // 日志（含 printf 风格）
    g_logs.clear();
    nap::log_info("普通日志");
    nap::logf(2, "带参数：%d %s", 42, "ok");
    CHECK_EQ(g_logs.size(), static_cast<std::size_t>(2));
    CHECK_EQ(g_logs[0], std::string("1:普通日志"));
    CHECK_EQ(g_logs[1], std::string("2:带参数：42 ok"));
}

static void test_plugin_macro() {
    SECTION("NAP_PLUGIN 宏生成的导出");
    // 这条要是挂了，说明**本工程编译时没开 UTF-8** → 中文关键词匹配全都会是假的
    CHECK(nap::utf8_literals_ok());
    CHECK_EQ(nap_plugin_abi_version(), NAP_PLUGIN_ABI_VERSION);
    CHECK_EQ(std::string(nap_plugin_name()), std::string("test_plugin"));
    CHECK_EQ(std::string(nap_plugin_version()), std::string("9.9.9"));

    NapApi bad = make_api();
    bad.abi_version = 99;
    CHECK_EQ(nap_plugin_init("{}", &bad), -1);          // ABI 不符 → -1
    CHECK_EQ(nap_plugin_init("{\"k\":1}", 0), -1);      // api 为空 → -1

    NapApi api = make_api();
    CHECK_EQ(nap_plugin_init("{\"k\":1}", &api), 0);
    CHECK_EQ(nap_plugin_singleton().cfg_, std::string("{\"k\":1}"));
    CHECK_EQ(nap::Host::instance().config().get("k").as_int(0), 1);   // init 的配置能读到

    // 没命中 → 0；命中 → 1 且动作发出去了
    CHECK_EQ(nap_plugin_on_event("{\"message_type\":\"group\",\"raw_message\":\"签到\"}"), 0);
    g_last_action.clear();
    CHECK_EQ(nap_plugin_on_event(
                 "{\"message_type\":\"group\",\"group_id\":5,\"raw_message\":\"你好\"}"), 1);
    CHECK(g_last_action.find("\"group_id\":5") != std::string::npos);

    // 坏 JSON → 2（内部错误），且不会崩
    CHECK_EQ(nap_plugin_on_event("{坏"), 2);
    CHECK_EQ(nap_plugin_on_event(0), 2);

    // 热更走同一个 on_config
    CHECK_EQ(nap_plugin_reload("{\"k\":2}"), 0);
    CHECK_EQ(nap::Host::instance().config().get("k").as_int(0), 2);

    nap_plugin_unload();                                // 不该崩
}

int main() {
    std::printf("== C++ SDK 自测 ==\n");
    test_host_not_attached();          // 必须第一个（之后 Host 就被 attach 了）
    test_json_parse();
    test_json_strings();
    test_json_errors();
    test_json_dump();
    test_event();
    test_host_actions();
    test_plugin_macro();
    std::printf("== %d 条断言，失败 %d ==\n", g_total, g_failed);
    if (g_failed) {
        std::printf("结果：失败 ✗\n");
        return 1;
    }
    std::printf("结果：全部通过 ✅\n");
    return 0;
}
