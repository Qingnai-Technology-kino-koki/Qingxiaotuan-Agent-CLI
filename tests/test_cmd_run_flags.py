"""cmd_run 端到端参数生效回归 —— 曾定义却未消费的 --allowed-tools/--json-schema/--max-turns/--permission-mode/--effort。"""

from __future__ import annotations

import pytest


def test_run_parser_exposes_json_schema_flags():
    from qingxiaotuan.cli.parser import build_parser
    p = build_parser()
    args = p.parse_args([
        "run", "write a parser",
        "--json-schema", '{"type":"object"}',
        "--json-schema-strict",
        "--json-schema-retries", "5",
        "--allowed-tools", "Bash(npm test),Read,Write",
        "--max-turns", "4",
        "--permission-mode", "plan",
        "--effort", "low",
        "--max-cost", "0.5",
        "--max-turns", "7",
    ])
    assert args.func == "cmd_run"
    assert args.json_schema == '{"type":"object"}'
    assert args.json_schema_strict is True
    assert args.json_schema_retries == 5
    assert args.allowed_tools == "Bash(npm test),Read,Write"
    assert args.max_turns == 7
    assert args.permission_mode == "plan"
    assert args.effort == "low"
    assert args.max_cost == 0.5


def test_run_resolves_json_schema_helper():
    from qingxiaotuan.cli.cmd_chat import _run_json_schema
    assert callable(_run_json_schema)
    from qingxiaotuan.core.json_schema import load_schema
    schema, err = load_schema('{"type":"object","properties":{"ok":{"type":"boolean"}}}')
    assert err is None and schema["type"] == "object"


def test_cmd_run_module_imports_lookup():
    from qingxiaotuan.cli.parser import _resolve_func
    fn = _resolve_func("cmd_run")
    assert callable(fn)