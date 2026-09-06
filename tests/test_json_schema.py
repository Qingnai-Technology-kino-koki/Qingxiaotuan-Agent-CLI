"""--json-schema 结构化输出测试 (对标 Claude Code structured/JSON-mode 输出)。"""

from __future__ import annotations

import json

import pytest

from qingxiaotuan.core.json_schema import (
    build_instruction, extract_json, load_schema, produce_structured,
    dispatch_builtin, match_oneof_branch, repair_answer, run_structured,
    summarize_errors, untag_fence, validate_against,
)

BOOK = {
    "type": "object",
    "properties": {
        "title": {"type": "string", "minLength": 1},
        "pages": {"type": "integer", "minimum": 0},
        "tags": {"type": "array", "items": {"type": "string"}},
        "genre": {"enum": ["fiction", "nonfiction"]},
        "optional_note": {"type": "string"},
    },
    "required": ["title", "pages"],
    "additionalProperties": False,
}


# ---------------------------------------------------------------- schema 加载

def test_load_inline_object():
    s, err = load_schema('{"type":"object","properties":{"a":{"type":"string"}}}')
    assert err is None
    assert s["properties"]["a"]["type"] == "string"


def test_load_file_schema(tmp_path):
    p = tmp_path / "s.json"
    p.write_text('{"type":"string"}', encoding="utf-8")
    s, err = load_schema(str(p))
    assert err is None and s == {"type": "string"}
    s2, err2 = load_schema("@" + str(p))
    assert err2 is None and s2 == {"type": "string"}


def test_load_invalid_json():
    _, err = load_schema("{not json")
    assert err and "不是合法 JSON" in err


def test_load_requires_type():
    _, err = load_schema('{"foo": 1}')
    assert err and "type" in err


# ---------------------------------------------------------------- 指令与围栏

def test_build_instruction_contains_schema():
    text = build_instruction(BOOK)
    assert "JSON Schema" in text
    assert '"title"' in text


def test_untag_fence():
    assert untag_fence("```json\n{\"a\":1}\n```") == '{"a":1}'
    assert untag_fence('{"a":1}') == '{"a":1}'


# ---------------------------------------------------------------- 抽取

def test_extract_plain_json():
    v, e = extract_json('{"a": 1}')
    assert e is None and v == {"a": 1}


def test_extract_fenced_json():
    v, e = extract_json('结果: ```json\n{"ok": true}\n``` 完毕')
    assert e is None and v == {"ok": True}


def test_extract_with_prefix_suffix_prose():
    v, e = extract_json('好的, 结果是 {"title": "x", "pages": 3}, 希望对你有用.')
    assert e is None and v == {"title": "x", "pages": 3}


def test_extract_array():
    v, e = extract_json('返回列表: [1, 2, 3] 如上')
    assert e is None and v == [1, 2, 3]


def test_extract_empty_or_garbage():
    _, e = extract_json("")
    assert e
    _, e2 = extract_json("没有 JSON")
    assert e2


# ---------------------------------------------------------------- 校验

def test_validate_pass():
    assert validate_against({"title": "G", "pages": 2, "tags": ["a"]}, BOOK) == []


def test_validate_missing_required():
    errs = validate_against({"title": "G"}, BOOK)
    assert any("pages" in e for e in errs)


def test_validate_wrong_type():
    errs = validate_against({"title": "G", "pages": "many"}, BOOK)
    assert any("pages" in e and "integer" in e for e in errs)


def test_validate_additional_properties():
    errs = validate_against({"title": "G", "pages": 1, "extra": 2}, BOOK)
    assert any("extra" in e for e in errs)


def test_validate_enum_and_min():
    errs = validate_against({"title": "G", "pages": 1, "genre": "sci-fi"}, BOOK)
    assert any("枚举" in e for e in errs)
    errs2 = validate_against({"title": "G", "pages": -1}, BOOK)
    assert any("minimum" in e for e in errs2)


def test_validate_nested_array_items():
    s = {"type": "object", "properties": {"x": {"items": {"type": "integer"}}}}
    assert validate_against({"x": [1, 2]}, s) == []
    errs = validate_against({"x": [1, "two"]}, s)
    assert any("x[1]" in e for e in errs)


# ------------------------------------------------- Round 2: 增强关键字

def test_validate_format_keywords():
    s = {"type": "string", "format": "uuid"}
    assert validate_against("123e4567-e89b-12d3-a456-426614174000", s) == []
    assert any("format" in e for e in validate_against("not-a-uuid", s))
    s2 = {"type": "string", "format": "email"}
    assert validate_against("a@b.co", s2) == []
    assert any("format" in e for e in validate_against("nope", s2))


def test_validate_unique_items():
    s = {"type": "array", "items": {"type": "object"}, "uniqueItems": True}
    assert validate_against([{"a": 1}, {"b": 2}], s) == []
    errs = validate_against([{"a": 1}, {"a": 1}], s)
    assert any("uniqueItems" in e for e in errs)


def test_validate_prefix_items_tuple():
    s = {"type": "array",
         "items": [{"type": "string"}, {"type": "integer"}]}
    assert validate_against(["x", 1], s) == []
    errs = validate_against(["x", "y"], s)
    assert any("[1]" in e for e in errs)


def test_validate_exclusive_minimum_numeric():
    s = {"type": "number", "exclusiveMinimum": 0}
    assert validate_against(0.5, s) == []
    assert any("exclusiveMinimum" in e for e in validate_against(0, s))


def test_validate_pattern_properties_and_prop_count():
    s = {"type": "object",
         "patternProperties": {"^S_": {"type": "string"}},
         "additionalProperties": False}
    assert validate_against({"S_ok": "v"}, s) == []
    errs = validate_against({"S_ok": 12}, s)
    assert any("S_ok" in e and "string" in e for e in errs)
    errs2 = validate_against({"other": "x"}, s)
    assert any("字段" in e for e in errs2)


def test_validate_dependent_required():
    s = {"type": "object",
         "properties": {"a": {"type": "string"}, "b": {"type": "string"}},
         "dependentRequired": {"a": ["b"]}}
    assert validate_against({"a": "x", "b": "y"}, s) == []
    errs = validate_against({"a": "x"}, s)
    assert any("b" in e for e in errs)


def test_validate_multiple_of():
    s = {"type": "number", "multipleOf": 2}
    assert validate_against(4, s) == []
    assert any("multipleOf" in e for e in validate_against(3, s))


def test_summarize_errors_groups_by_path():
    errs = validate_against({"title": "G"}, BOOK)
    summary = summarize_errors(errs)
    assert summary and ("pages" in summary[0] or "pages" in summary)
    assert len(summary) <= len(errs) * 2  # 不会爆炸式膨胀


# ---------------------------------------------------------------- 顶层编排

def test_produce_structured_ok():
    r = produce_structured(BOOK, '{"title":"T","pages":1,"tags":[]}')
    assert r.ok and r.value["title"] == "T"


def test_produce_structured_bad():
    r = produce_structured(BOOK, '{"title":"T"}')
    assert not r.ok and "pages" in r.error


def test_repair_answer_builds_hint():
    msgs = repair_answer(BOOK, '{"title":"T"}')
    assert len(msgs) == 1
    assert msgs[0]["role"] == "user"
    assert "pages" in msgs[0]["content"]


# ------------------------------------------------- Round 3: 编排/多态分发

def test_run_structured_succeeds_first_try():
    result = run_structured(lambda t: '{"title":"T","pages":1}',
                            BOOK, max_attempts=3)
    assert result.ok and result.attempts == 1
    assert result.value == {"title": "T", "pages": 1}


def test_run_structured_retries_until_fixed():
    # 第一次输出缺 pages, 第二次补全
    calls = []
    def executor(task):
        calls.append(task)
        if len(calls) == 1:
            return '{"title":"T"}'
        return '{"title":"T","pages":5}'
    result = run_structured(executor, BOOK, max_attempts=3)
    assert result.ok and result.attempts == 2
    assert len(calls) == 2
    assert "修复" in calls[1].split("\n")[0] or "JSON" in calls[1]


def test_run_structured_gives_up_after_max():
    result = run_structured(lambda t: '{"title":"T"}', BOOK, max_attempts=2)
    assert not result.ok and result.attempts == 2


def test_match_oneof_branch_routes():
    s = {"oneOf": [
        {"type": "object", "properties": {"kind": {"const": "a"}},
         "required": ["kind"], "title": "type_a"},
        {"type": "object", "properties": {"kind": {"const": "b"}},
         "required": ["kind"], "title": "type_b"},
    ]}
    assert match_oneof_branch({"kind": "a"}, s) == "type_a"
    assert match_oneof_branch({"kind": "b"}, s) == "type_b"
    assert match_oneof_branch({"kind": "zz"}, s) is None


def test_dispatch_builtin_routes_by_key():
    seen = {}
    def h_a(value):
        seen["a"] = value
        return "handled-a"
    def h_b(value):
        seen["b"] = value
        return "handled-b"
    out = dispatch_builtin({}, {"name": "a"}, {"a": h_a, "b": h_b})
    assert out == "handled-a" and "a" in seen
    out_b = dispatch_builtin({}, {"name": "b"}, {"a": h_a, "b": h_b})
    assert out_b == "handled-b"


def test_dispatch_builtin_unknown():
    out = dispatch_builtin({}, {"name": "zz"}, {"a": lambda v: None})
    assert out["ok"] is False and "zz" in out["error"]


def test_run_structured_with_agent_stream_mocked(tmp_path, qxt_home):
    """端到端: 用 mock 模型走 Agent 循环, 输出需修复一次才符合 schema。"""
    from qingxiaotuan.core.agent import Agent
    from qingxiaotuan.models.base import ModelAdapter
    from qingxiaotuan.app import build_kernel

    class MockModel(ModelAdapter):
        name = "mock-json"
        def __init__(self, script):
            self.script = list(script)
        def chat(self, messages, tools=None, stream=False, on_token=None, **kwargs):
            return self.script.pop(0)

    # 第一次缺 title, 第二次补齐
    bad = '{"pages": 2}'
    good = '{"title": "OK", "pages": 2, "tags": []}'
    model = MockModel([bad, good])

    kernel = build_kernel()
    config = kernel.require("config")
    config.data["agent"]["skill_nudge_interval"] = 0
    kernel.unprovide("model_adapter")
    kernel.provide("model_adapter", model, owner="test")
    agent = Agent(kernel=kernel, config=config, workspace=str(tmp_path),
                  confirm=lambda _p: True)
    agent._system_prompt = "sys"

    # 注入结构化指令后跑 run_structured
    from qingxiaotuan.core.json_schema import build_instruction
    agent.messages = [{"role": "system", "content": agent._system_prompt}]
    agent.messages.insert(1, {"role": "system", "content":
                              build_instruction(BOOK, strict=True)})

    def executor(task):
        return agent.run(task, stream=False) or ""

    result = run_structured(executor, BOOK, max_attempts=3, initial_task="hi")
    assert result.ok and result.value["title"] == "OK"


# ---------------------------------------------------------------- Loader 集成

def test_json_schema_arg_parse():
    from qingxiaotuan.cli.parser import build_parser
    p = build_parser()
    args = p.parse_args(["--print", "--json-schema", '{"type":"object"}'])
    assert args.json_schema == '{"type":"object"}'
    args2 = p.parse_args(["--json-schema", "@s.json", "--json-schema-retries", "5"])
    assert args2.json_schema == "@s.json" and args2.json_schema_retries == 5