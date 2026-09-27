# -*- coding: utf-8 -*-
# 插件配置 schema 测试（可配置项由插件自己声明 → WebUI 通用映射）
#
# 需求给的类型表：text / textarea / number / bool / select / multiselect /
# list / password / group_select / color / file（+ json 作为嵌套对象的扩展）。
# 这里逐类钉住：归一化、推断、取值转换、校验、合并。
import unittest

from astercore.core.config_schema import (FIELD_TYPES, SchemaError, build_payload,
                                          coerce_value, infer_field, infer_schema,
                                          is_secret_key, normalize_field,
                                          normalize_options, normalize_schema,
                                          validate_and_merge)


class NormalizeTest(unittest.TestCase):

    def test_page_has_all_required_types(self):
        """需求列的类型一个都不能少（少一个就有插件渲染不出来）"""
        for t in ("text", "textarea", "number", "bool", "select",
                  "multiselect", "list", "password", "group_select",
                  "color", "file"):
            self.assertIn(t, FIELD_TYPES, f"缺少字段类型 {t}")

    def test_options_three_shapes(self):
        self.assertEqual(normalize_options([{"value": 1, "label": "甲"}]),
                         [{"value": "1", "label": "甲"}])
        self.assertEqual(normalize_options(["a", "b"]),
                         [{"value": "a", "label": "a"}, {"value": "b", "label": "b"}])
        self.assertEqual(normalize_options({"a": "甲"}),
                         [{"value": "a", "label": "甲"}])
        self.assertEqual(normalize_options(None), [])

    def test_shorthand_and_default_type(self):
        f = normalize_field("reply", "textarea")
        self.assertEqual(f["type"], "textarea")
        self.assertEqual(normalize_field("x", {})["type"], "text")
        self.assertEqual(normalize_field("x", {"type": "TEXT"})["type"], "text")

    def test_unknown_type_raises(self):
        with self.assertRaises(SchemaError):
            normalize_field("x", {"type": "richtext"})

    def test_select_requires_options(self):
        with self.assertRaises(SchemaError):
            normalize_field("mode", {"type": "select"})
        f = normalize_field("mode", {"type": "select", "options": ["a"]})
        self.assertEqual(f["options"], [{"value": "a", "label": "a"}])

    def test_list_item_type_validation(self):
        f = normalize_field("kw", {"type": "list", "item_type": "number"})
        self.assertEqual(f["item_type"], "number")
        self.assertEqual(normalize_field("kw", {"type": "list"})["item_type"], "text")
        with self.assertRaises(SchemaError):
            normalize_field("kw", {"type": "list", "item_type": "object"})

    def test_password_secret_defaults_true(self):
        """规范：password 默认就是密文（secret: True）"""
        self.assertTrue(normalize_field("k", {"type": "password"})["secret"])
        self.assertFalse(normalize_field("k", {"type": "password",
                                               "secret": False})["secret"])

    def test_group_select_multiple_default_true(self):
        self.assertTrue(normalize_field("g", {"type": "group_select"})["multiple"])
        self.assertFalse(normalize_field("g", {"type": "group_select",
                                               "multiple": False})["multiple"])

    def test_extra_fields_kept_per_type(self):
        f = normalize_field("a", {"type": "text", "maxlength": 20, "placeholder": "啊"})
        self.assertEqual((f["maxlength"], f["placeholder"]), (20, "啊"))
        f = normalize_field("n", {"type": "number", "min": 0, "max": 10, "step": 2,
                                  "unit": "秒"})
        self.assertEqual((f["min"], f["max"], f["step"], f["unit"]), (0, 10, 2, "秒"))
        f = normalize_field("t", {"type": "textarea", "rows": 8})
        self.assertEqual(f["rows"], 8)
        f = normalize_field("fp", {"type": "file", "accept": ".json"})
        self.assertEqual(f["accept"], ".json")

    def test_common_metadata(self):
        f = normalize_field("x", {"type": "text", "label": "标题", "help": "说明",
                                  "required": True, "advanced": True, "group": "高级"})
        self.assertEqual(f["label"], "标题")
        self.assertTrue(f["required"])
        self.assertTrue(f["advanced"])
        self.assertEqual(f["group"], "高级")
        self.assertEqual(normalize_field("k", {"type": "text"})["label"], "k")

    def test_unknown_declaration_keys_are_dropped_not_kept(self):
        """拼错字段名要能发现，且不能原样带进前端（否则看起来像生效了）"""
        f = normalize_field("x", {"type": "text", "maxlenght": 5})
        self.assertNotIn("maxlenght", f)

    def test_bad_number_extra_raises(self):
        with self.assertRaises(SchemaError):
            normalize_field("x", {"type": "number", "max": "很多"})

    def test_normalize_schema_skips_bad_field_keeps_rest(self):
        """单个字段写错不该让整页打不开"""
        fields, errors = normalize_schema({
            "good": {"type": "text"},
            "bad": {"type": "wat"},
            "also_good": {"type": "bool"},
        })
        self.assertEqual(set(fields), {"good", "also_good"})
        self.assertEqual(len(errors), 1)
        self.assertIn("bad", errors[0])

    def test_normalize_schema_non_dict(self):
        fields, errors = normalize_schema(["x"])
        self.assertEqual(fields, {})
        self.assertEqual(len(errors), 1)
        self.assertEqual(normalize_schema(None), ({}, []))

    def test_declaration_order_is_preserved(self):
        """表单顺序 = 声明顺序（插件作者能控制阅读顺序）"""
        fields, _ = normalize_schema({"z": "text", "a": "bool", "m": "number"})
        self.assertEqual(list(fields), ["z", "a", "m"])


class InferTest(unittest.TestCase):
    """没声明 schema 的插件也要有表单（否则"通吃"就是空话）"""

    def test_scalar_types(self):
        self.assertEqual(infer_field("a", True)["type"], "bool")
        self.assertEqual(infer_field("a", 3)["type"], "number")
        self.assertEqual(infer_field("a", 1.5)["type"], "number")
        self.assertEqual(infer_field("a", "hi")["type"], "text")
        self.assertEqual(infer_field("a", {"k": 1})["type"], "json")

    def test_long_string_becomes_textarea(self):
        self.assertEqual(infer_field("a", "x" * 200)["type"], "textarea")
        self.assertEqual(infer_field("a", "行1\n行2")["type"], "textarea")

    def test_secret_names_become_password(self):
        for name in ("api_key", "apiKey", "token", "access_token", "password",
                     "bili_cookie", "client_secret", "credential"):
            with self.subTest(name=name):
                self.assertTrue(is_secret_key(name), name)
                self.assertEqual(infer_field(name, "abc")["type"], "password")
        for name in ("name", "reply", "keywords", "count"):
            self.assertFalse(is_secret_key(name), name)

    def test_list_item_type_from_first_element(self):
        self.assertEqual(infer_field("a", ["x"])["item_type"], "text")
        self.assertEqual(infer_field("a", [1, 2])["item_type"], "number")
        self.assertEqual(infer_field("a", [True])["item_type"], "bool")
        self.assertEqual(infer_field("a", [{"k": 1}])["item_type"], "json")
        self.assertEqual(infer_field("a", [])["item_type"], "text")

    def test_infer_schema_over_dict(self):
        fields = infer_schema({"enabled": True, "n": 1, "kw": ["a"], "api_key": "x"})
        self.assertEqual([f["type"] for f in fields.values()],
                         ["bool", "number", "list", "password"])


class CoerceTest(unittest.TestCase):

    def f(self, **kw):
        return normalize_field("x", kw)

    def test_bool_from_various(self):
        f = self.f(type="bool")
        for v, want in ((True, True), (1, True), ("true", True), ("是", True),
                        (0, False), ("false", False), ("", False)):
            with self.subTest(v=v):
                ok, val, _ = coerce_value(f, v)
                self.assertTrue(ok)
                self.assertEqual(val, want)
        self.assertFalse(coerce_value(f, "大概吧")[0])

    def test_number_range_and_type(self):
        f = self.f(type="number", min=0, max=10)
        self.assertEqual(coerce_value(f, "5")[1], 5)
        self.assertEqual(coerce_value(f, 5.0)[1], 5, "整数应存成 int，别退化成 5.0")
        self.assertFalse(coerce_value(f, 11)[0])
        self.assertFalse(coerce_value(f, -1)[0])
        self.assertFalse(coerce_value(f, "五")[0])
        self.assertFalse(coerce_value(f, True)[0], "bool 不是数字")

    def test_integral_number_stays_int_even_with_step(self):
        """配置里出现 55.0 很别扭：整数值一律存 int（step=5/0.5 都一样）"""
        for step in (None, 1, 5, 0.5):
            with self.subTest(step=step):
                f = self.f(type="number", **({"step": step} if step else {}))
                ok, val, _ = coerce_value(f, "55")
                self.assertTrue(ok)
                self.assertEqual(val, 55)
                self.assertIsInstance(val, int)
        f2 = self.f(type="number", step=0.5)
        self.assertEqual(coerce_value(f2, "1.5")[1], 1.5, "真小数要保留")

    def test_text_maxlength(self):
        f = self.f(type="text", maxlength=3)
        self.assertTrue(coerce_value(f, "abc")[0])
        ok, _, err = coerce_value(f, "abcd")
        self.assertFalse(ok)
        self.assertIn("3", err)

    def test_json_accepts_text_and_object(self):
        f = self.f(type="json")
        self.assertEqual(coerce_value(f, '{"a":1}')[1], {"a": 1})
        self.assertEqual(coerce_value(f, {"a": 1})[1], {"a": 1})
        self.assertEqual(coerce_value(f, "")[1], {})
        ok, _, err = coerce_value(f, "{坏}")
        self.assertFalse(ok)
        self.assertIn("JSON", err)

    def test_list_items_coerced(self):
        f = self.f(type="list", item_type="number")
        self.assertEqual(coerce_value(f, ["1", 2])[1], [1, 2])
        ok, _, err = coerce_value(f, ["1", "x"])
        self.assertFalse(ok)
        self.assertIn("第 2 项", err, "要指出是第几项，否则用户找不到")

    def test_list_limits(self):
        f = self.f(type="list", item_type="text", min_items=1, max_items=2)
        self.assertFalse(coerce_value(f, [])[0])
        self.assertTrue(coerce_value(f, ["a", "b"])[0])
        self.assertFalse(coerce_value(f, ["a", "b", "c"])[0])

    def test_multiselect_and_select(self):
        f = self.f(type="multiselect", options=["a", "b"])
        self.assertEqual(coerce_value(f, ["a", 1])[1], ["a", "1"])
        self.assertFalse(coerce_value(f, "a")[0], "多选必须是列表")
        self.assertEqual(coerce_value(self.f(type="select", options=["a"]), 1)[1], "1")

    def test_group_select_multiple_is_a_list_not_a_string(self):
        """真缺陷回归：多选群号曾被 str() 成 "['123']" 存进配置"""
        multi = self.f(type="group_select")
        ok, val, _ = coerce_value(multi, [315471269, "123"])
        self.assertTrue(ok)
        self.assertEqual(val, ["315471269", "123"])
        self.assertIsInstance(val, list)
        single = self.f(type="group_select", multiple=False)
        self.assertEqual(coerce_value(single, ["123"])[1], "123")
        self.assertEqual(coerce_value(single, "123")[1], "123")
        self.assertFalse(coerce_value(multi, "123")[0], "多选必须是列表")


class MergeTest(unittest.TestCase):
    """保存时的三条铁律：不丢未声明键 / 只在声明时严格校验 / 错误精确到字段"""

    SCHEMA = {
        "reply": {"type": "text", "label": "回复"},
        "count": {"type": "number", "min": 0, "label": "次数"},
        "mode": {"type": "select", "options": ["a", "b"], "label": "模式"},
    }

    def test_preserves_undeclared_keys(self):
        fields, _ = normalize_schema(self.SCHEMA)
        ok, merged, errs = validate_and_merge(
            fields, {"reply": "hi", "用户手写的": {"深层": 1}}, {"reply": "yo"})
        self.assertTrue(ok)
        self.assertEqual(merged["reply"], "yo")
        self.assertEqual(merged["用户手写的"], {"深层": 1}, "未声明的键绝不能丢")
        self.assertEqual(errs, {})

    def test_coerces_declared_values(self):
        fields, _ = normalize_schema(self.SCHEMA)
        ok, merged, _ = validate_and_merge(fields, {}, {"reply": 5, "count": "3"})
        self.assertTrue(ok)
        self.assertEqual(merged["reply"], "5")
        self.assertEqual(merged["count"], 3)

    def test_field_errors_keyed_by_name(self):
        fields, _ = normalize_schema(self.SCHEMA)
        # schema 里 count 只声明了 min=0，所以用越下界来触发
        ok, _, errs = validate_and_merge(fields, {}, {"count": -5})
        self.assertFalse(ok)
        self.assertIn("count", errs)
        self.assertIn("不能小于", errs["count"])

    def test_required_missing(self):
        fields, _ = normalize_schema({"k": {"type": "text", "required": True,
                                            "label": "密钥"}})
        ok, _, errs = validate_and_merge(fields, {}, {"k": "   "})
        self.assertFalse(ok)
        self.assertIn("必填", errs["k"])

    def test_readonly_not_overwritten(self):
        fields, _ = normalize_schema({"v": {"type": "text", "readonly": True}})
        _, merged, _ = validate_and_merge(fields, {"v": "原值"}, {"v": "篡改"})
        self.assertEqual(merged["v"], "原值")

    def test_non_strict_passes_through(self):
        """推断出来的 schema 只用于渲染，不能拿它当法律改写用户数据"""
        fields, _ = normalize_schema({"count": {"type": "number", "min": 0}})
        ok, merged, errs = validate_and_merge(fields, {}, {"count": -5}, strict=False)
        self.assertTrue(ok)
        self.assertEqual(merged["count"], -5)
        self.assertEqual(errs, {})

    def test_non_dict_submission_rejected(self):
        ok, _, errs = validate_and_merge({}, {}, ["not", "dict"])
        self.assertFalse(ok)
        self.assertIn("_", errs)


class PayloadTest(unittest.TestCase):

    def test_declared_payload(self):
        p = build_payload({"reply": {"type": "text", "label": "回复"}},
                          {"reply": "hi", "other": 1})
        self.assertTrue(p["declared"])
        self.assertEqual(len(p["fields"]), 1)
        self.assertEqual(p["fields"][0]["value"], "hi")
        self.assertEqual(p["values"], {"reply": "hi", "other": 1})

    def test_missing_by_declared_falls_back_to_inference(self):
        """声明了但一条都解析不出来 → 退回推断并如实标注，而不是给个空页面"""
        p = build_payload({"bad": {"type": "nope"}}, {"real": True})
        self.assertFalse(p["declared"])
        self.assertEqual([f["name"] for f in p["fields"]], ["real"])
        self.assertTrue(p["schema_errors"], "schema 自身的问题要带出来给人看")

    def test_undeclared_uses_inference(self):
        p = build_payload(None, {"enabled": True, "kw": ["a"]})
        self.assertFalse(p["declared"])
        self.assertEqual([f["name"] for f in p["fields"]], ["enabled", "kw"])

    def test_default_value_used_when_missing(self):
        p = build_payload({"n": {"type": "number", "default": 7}}, {})
        self.assertEqual(p["fields"][0]["value"], 7)

    def test_empty_config_and_no_schema(self):
        p = build_payload(None, {})
        self.assertEqual(p["fields"], [])
        self.assertFalse(p["declared"])


if __name__ == "__main__":
    unittest.main()
