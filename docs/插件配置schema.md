# 插件配置声明（`__config_schema__`）

> 一句话：**可配置项由插件自己声明，面板只实现「类型 → 控件」这一套映射**，
> 所以面板里没有一行代码是给某个特定插件写的，所有插件通吃。

---

## 1. 怎么声明（两种写法，任选）

**① 模块级字典**（推荐：离代码近，**老插件也能直接用**）

```python
__config_schema__ = {
    "reply": {"type": "text", "label": "回复内容"},
    "keywords": {"type": "list", "item_type": "text", "label": "触发关键词"},
}
```

**② 写进 `PluginMeta`**（类插件顺手写在元信息里）

```python
__meta__ = PluginMeta(
    name="demo", name_cn="示例",
    config_schema={"reply": {"type": "text", "label": "回复内容"}},
)
```

优先级：`__config_schema__` > `PluginMeta.config_schema` > 自动推断。
**声明是纯增量元数据**：不影响插件加载、不影响事件分发、不影响配置读写逻辑本身，
所以 Linux 版那 5 个老插件加这个字典也不会改变任何行为（双端对齐不受影响）。

## 2. 字段类型表（面板已实现全部映射）

| type | 前端控件 | 可用的额外字段 |
|---|---|---|
| `text` | 单行输入 | `maxlength` `placeholder` |
| `textarea` | 多行输入 | `rows` `placeholder` |
| `number` | 数字输入 | `min` `max` `step` `unit` |
| `bool` | 开关 | — |
| `select` | 下拉框 | `options`（必填） |
| `multiselect` | 多选（复选组） | `options`（必填）`max_items` |
| `list` | 动态列表（可增删行） | `item_type` `options` `min_items` `max_items` |
| `password` | 密码框 | `secret`（默认 `true`） |
| `group_select` | 群号选择器（从机器人已加入的群拉取） | `multiple`（默认 `true`）`max_items` |
| `color` | 颜色选择（色块 + hex 输入双向同步） | — |
| `file` | 文件路径输入 | `accept` |
| `json` | JSON 文本域（保存时校验） | `rows` |

> 最后一种 `json` 是**扩展**（需求表之外）：给 `video_parser_config.json`
> 那类深层嵌套配置用；推断出的 dict 也走它。

**所有类型都认的公共字段**：
`label`（显示名，缺省用键名）· `help`（说明文字）· `default`（缺省值）·
`required`（必填）· `advanced`（折叠进「高级选项」）· `group`（分组标题）· `readonly`。

`options` 支持三种写法：`[{"value": "a", "label": "甲"}]` / `["a", "b"]` / `{"a": "甲"}`。

## 3. 一份完整示例

```python
__config_schema__ = {
    # 分组：相同 group 的字段会归到同一个标题下，顺序 = 声明顺序
    "keywords": {"type": "list", "item_type": "text", "label": "触发关键词",
                 "group": "基础", "min_items": 1,
                 "help": "消息里含任一关键词就命中"},
    "reply":    {"type": "text", "label": "回复内容", "group": "基础",
                 "maxlength": 200, "placeholder": "已响应 {n} 次～",
                 "help": "{n} 会替换成当前计数"},
    "ratio":    {"type": "number", "label": "触发概率", "min": 0, "max": 100,
                 "step": 5, "unit": "%", "default": 100},
    "mode":     {"type": "select", "label": "回复方式",
                 "options": [{"value": "plain", "label": "纯文本"},
                             {"value": "quote", "label": "引用回复"}],
                 "default": "plain"},
    "features": {"type": "multiselect", "label": "启用的功能",
                 "options": ["签到", "抽奖", "点歌"]},
    "groups":   {"type": "group_select", "label": "生效群",
                 "help": "留空 = 全部群生效"},
    "api_key":  {"type": "password", "label": "API 密钥"},
    "theme":    {"type": "color", "label": "主题色", "default": "#1E40AF"},
    "cookie":   {"type": "file", "label": "Cookie 文件", "accept": ".json,.txt"},
    "notes":    {"type": "textarea", "label": "备注", "rows": 4, "advanced": True},
    "raw":      {"type": "json", "label": "高级配置", "advanced": True},
}
```

## 4. 没声明会怎样（不会白屏）

| 情况 | 面板表现 |
|---|---|
| 插件声明了 schema | 标题旁标「由插件声明（`__config_schema__`）」，按声明渲染 |
| 没声明、但已有配置 | 按当前值的类型**推断**字段，标注「按当前值自动推断」 |
| 没声明、也没配置 | 提示"这个插件没有可配置项"，并提示可以加 `__config_schema__` |
| 声明写错了（未知类型等） | 坏字段被**跳过并记录**，其余字段照常渲染，`schema_errors` 一并返回 |

推断规则：`bool→开关` · 数字→数字输入 · 字符串→单行（很长/多行→多行）·
列表→动态列表（元素类型取第一个元素）· 字典→JSON · **键名含
`secret/token/password/api_key/cookie/credential` → 密码框**。

**任何情况下都保留 JSON 原文页签**——这是逃生口，深配置和实验性字段都能手改。

## 5. 保存时的服务端行为（不是只有前端在把关）

1. **只在插件声明了 schema 时才做类型转换与校验**（`strict`）。
   推断出来的 schema 只用于渲染，**不拿它当法律改写用户数据**。
2. 校验规则来自字段声明：
   - `number` → 必须能转数字，且落在 `min`/`max` 内；**整数值一律存 `int`**（不会留下 `55.0`）
   - `required` → 空字符串/空列表/`None` 都算空
   - `list` → 逐项按 `item_type` 转换，并检查 `min_items`/`max_items`；错误信息带**第几项**
   - `text`/`textarea` → `maxlength`
   - `select` → 转成字符串；`multiselect`/`group_select`（多选）→ **字符串列表**
   - `json` → 字符串会被解析成对象，格式错误直接拒绝
   - `bool` → 认 `true/1/yes/on/是` 与 `false/0/no/off/否`
3. 校验不通过 → **HTTP 400 + `field_errors`**（键是字段名），前端把对应控件标红并显示原因；
   **一个字节都不落盘**。
4. **未在 schema 里声明的键原样保留** —— 用户在 JSON 页签里手写的、插件自己加的键都不会丢。
5. 高级用户显式走 `POST .../config?raw=1` 可跳过校验与转换（JSON 页签的逃生口）。

## 6. 面板侧实现位置（要给面板加类型时看这里）

| 位置 | 作用 |
|---|---|
| `src/astercore/core/config_schema.py` | 归一化 / 推断 / 取值转换 / 校验合并（**纯逻辑，全单测**） |
| `runtime.get_plugin_schema(name)` | 取声明（模块级 → `PluginMeta` → 推断），老插件经兼容层适配器也能取到 |
| `GET /api/accounts/<aid>/plugins/<name>/schema` | 面板拿字段表 + 当前值 |
| `POST /api/accounts/<aid>/plugins/<name>/config` | 保存（默认校验合并；`?raw=1` 跳过） |
| `web/static/index.html` 的 `cfgControl()` | **唯一的类型 → 控件映射表**，加新类型只改这一处 |
| `GET /api/accounts/<aid>/groups` | `group_select` 的群列表来源；拉不到时降级成手填群号 |

## 7. 怎么验证

```bash
cd /root/astercore
python3 -m unittest tests.test_config_schema tests.test_plugin_config_api
python3 tools/verify_config_form.py      # 真 Chromium：11 种控件逐个渲染 + 填表 + 保存 + 回读
```

`verify_config_form.py` 覆盖：每类控件的渲染细节（maxlength/rows/min/max/step/unit/
options/multiple/accept）、动态列表增删、群列表来自机器人、填表保存后**回读类型正确**、
JSON 页签双向不丢、越界值被服务端拦下并标红、全程零 JS 异常。

---

*相关：`docs/桌面版施工对照.md` · `tests/test_config_schema.py` · `tools/verify_config_form.py`*
