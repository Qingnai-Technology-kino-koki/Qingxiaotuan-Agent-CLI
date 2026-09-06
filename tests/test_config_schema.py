"""配置 Schema 一致性测试: 确保 config.schema.json 与 DEFAULT_CONFIG 同步。

不依赖 jsonschema: 仅做轻量类型校验, 防止「改了默认值类型却忘了重新生成 schema」。
重新生成: python scripts/gen_config_schema.py config.schema.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SCHEMA_PATH = ROOT / "config.schema.json"


def _py_type(value) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return "string"


def test_schema_is_valid_json_and_well_formed():
    assert SCHEMA_PATH.exists(), "config.schema.json 缺失, 先运行 scripts/gen_config_schema.py"
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    assert schema.get("$schema", "").startswith("https://json-schema.org/")
    assert schema.get("type") == "object"
    assert isinstance(schema.get("properties"), dict)
    assert len(schema["properties"]) >= 10


def test_default_config_conforms_to_schema_types():
    """DEFAULT_CONFIG 的每个叶子类型必须与该 schema 声明一致。"""
    from qingxiaotuan.config.defaults import DEFAULT_CONFIG

    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

    def check(node_schema, node_cfg, dotted):
        if node_schema.get("type") == "object" and "properties" in node_schema:
            assert isinstance(node_cfg, dict), f"{dotted} 应为 object"
            for k, sub in node_schema["properties"].items():
                if k in node_cfg:
                    check(sub, node_cfg[k], f"{dotted}.{k}")
        else:
            expected = node_schema.get("type")
            actual = _py_type(node_cfg)
            # integer 兼容 number (JSON 里 integer ⊂ number)
            if expected == "number" and actual == "integer":
                return
            assert actual == expected, (
                f"{dotted}: schema 期望 {expected}, DEFAULT_CONFIG 实际 {actual}"
            )

    check(schema, DEFAULT_CONFIG, "root")


def test_schema_leaves_all_typed_and_documented():
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

    def check(props, dotted):
        for k, sub in props.items():
            path = f"{dotted}.{k}"
            if sub.get("type") == "object" and "properties" in sub:
                check(sub["properties"], path)
            else:
                assert "type" in sub, f"{path} 缺 type"
                assert "description" in sub, f"{path} 缺 description"

    check(schema["properties"], "root")
