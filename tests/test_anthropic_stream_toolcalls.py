"""Anthropic 流式 tool_calls 回归测试。

修复前: content_block_start 把 tool_input 预置为 json.dumps(block.input) (恒为 "{}"),
随后 input_json_delta 的 partial_json 拼接在后面, 得到 "{}{...}" 非法 JSON, 导致
tool_calls 参数在下流 json.loads 时损坏/丢失。本测试确保流式路径能正确还原参数。
"""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from qingxiaotuan.models.anthropic import AnthropicAdapter

# 一个 tool_use 块的 SSE 流: input 在 start 时为空, 参数通过两段 input_json_delta 拼出。
SSE_LINES = [
    "event: message_start",
    'data: {"type":"message_start"}',
    "",
    "event: content_block_start",
    'data: {"type":"content_block_start","index":1,'
    '"content_block":{"type":"tool_use","id":"toolu_1","name":"shell","input":{}}}',
    "",
    "event: content_block_delta",
    'data: {"type":"content_block_delta","index":1,'
    '"delta":{"type":"input_json_delta","partial_json":"{\\"command\\": "}}',
    "",
    "event: content_block_delta",
    'data: {"type":"content_block_delta","index":1,'
    '"delta":{"type":"input_json_delta","partial_json":"\\"ls -la\\"}"}}',
    "",
    "event: content_block_stop",
    'data: {"type":"content_block_stop","index":1}',
    "",
    "event: message_delta",
    'data: {"type":"message_delta","delta":{"stop_reason":"tool_use"},"usage":{"output_tokens":10}}',
    "",
    "event: message_stop",
    'data: {"type":"message_stop"}',
    "",
]


class _FakeResp:
    def __init__(self, lines):
        self._lines = lines

    def raise_for_status(self):
        return None

    def iter_lines(self):
        for ln in self._lines:
            yield ln


class _FakeCM:
    def __init__(self, lines):
        self._lines = lines

    def __enter__(self):
        return _FakeResp(self._lines)

    def __exit__(self, *a):
        return False


def _make_adapter():
    client = MagicMock()
    client.stream.return_value = _FakeCM(SSE_LINES)
    adapter = AnthropicAdapter(
        base_url="https://api.anthropic.com", model="claude-3-5-sonnet", api_key="x"
    )
    return adapter, client


def test_stream_tool_call_arguments_roundtrip():
    adapter, client = _make_adapter()
    resp = adapter._stream(client, "https://api.anthropic.com/messages",
                           {"x-api-key": "x"}, {}, None)
    assert len(resp.tool_calls) == 1, "应恰好产出一个 tool_call"
    tc = resp.tool_calls[0]
    assert tc.id == "toolu_1"
    assert tc.name == "shell"
    # 关键断言: arguments 必须是合法 JSON, 且能还原成预期参数 (修复前的代码会得到 "{}{...}")
    args = json.loads(tc.arguments)
    assert args == {"command": "ls -la"}, args


def test_stream_tool_call_args_not_corrupted_with_prefix_brace():
    # 模拟 start 时若误预置 "{}", 拼接后应为非法 JSON; 修复后不应有此前缀。
    adapter, client = _make_adapter()
    resp = adapter._stream(client, "https://api.anthropic.com/messages",
                           {"x-api-key": "x"}, {}, None)
    assert not resp.tool_calls[0].arguments.startswith("{}"), \
        "arguments 不应带有修复前误预置的 '{}' 前缀"
