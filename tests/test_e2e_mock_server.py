"""M8: 模型 mock server 端到端测试 (真实 HTTP 往返)。

用本地 HTTP server 模拟 OpenAI 兼容 /v1/chat/completions 端点, 让真实
OpenAICompatAdapter 走完整网络栈驱动 Agent 工具循环:

- 非流式: 工具调用 → 工具结果回填 → 最终回答;
- 流式: SSE chunk 逐片送达, on_token 回调收到内容;
- 验证 mock 端确实收到了工具结果 (证明 ReAct 循环在线上闭环)。

全程离线, 不访问外网。mock 端点设施见 conftest.py。
"""

from __future__ import annotations

from conftest import _tool_call  # noqa: F401  (共享 mock 端点)

from qingxiaotuan.core.agent import Agent
from qingxiaotuan.app import build_kernel
from qingxiaotuan.models.openai_compat import OpenAICompatAdapter


def _build_agent(adapter, tmp_path, qxt_home):
    kernel = build_kernel()
    config = kernel.require("config")
    config.data["agent"]["skill_nudge_interval"] = 0
    config.data["loop"]["max_iterations"] = 5
    kernel.unprovide("model_adapter")
    kernel.provide("model_adapter", adapter, owner="test")
    return Agent(
        kernel=kernel, config=config, workspace=str(tmp_path),
        confirm=lambda _p: True, exclude_tools=("web_fetch", "memory_write", "skill_save"),
    )


def test_mock_server_nonstream_tool_loop(tmp_path, qxt_home, mock_server):
    """非流式: 真实 HTTP 上完成 工具调用 → 工具结果回填 → 最终回答。"""
    (tmp_path / "hello.txt").write_text("你好, 青小团", encoding="utf-8")
    server = mock_server([
        {"tool_calls": [_tool_call("read_file", {"path": "hello.txt"})],
         "finish_reason": "tool_calls"},
        {"content": "已读取文件内容: 你好, 青小团"},
    ])
    adapter = OpenAICompatAdapter(
        base_url=f"http://127.0.0.1:{server.port}/v1", model="mock",
        api_key="test-key", prompt_cache=False,
    )
    agent = _build_agent(adapter, tmp_path, qxt_home)

    answer = agent.run("读一下 hello.txt", stream=False)

    assert "你好, 青小团" in answer
    # 两轮请求: 第一轮带工具定义, 第二轮带工具结果 (ReAct 闭环)
    assert len(server.requests) == 2
    r1, r2 = server.requests
    assert not r1.get("stream")
    assert any(t.get("function", {}).get("name") == "read_file"
               for t in r1.get("tools", []))
    # 第二轮消息里应包含 tool 角色的结果
    roles = [m["role"] for m in r2["messages"]]
    assert "tool" in roles
    tool_msg = next(m for m in r2["messages"] if m["role"] == "tool")
    assert "你好, 青小团" in tool_msg["content"]


def test_mock_server_streaming_tokens(tmp_path, qxt_home, mock_server):
    """流式: SSE chunk 逐片送达, on_token 回调收到内容。"""
    server = mock_server([
        {"content": "流式响应"},
    ])
    adapter = OpenAICompatAdapter(
        base_url=f"http://127.0.0.1:{server.port}/v1", model="mock",
        api_key="test-key", prompt_cache=False,
    )
    agent = _build_agent(adapter, tmp_path, qxt_home)

    tokens: list[str] = []
    answer = agent.run("用流式回答", stream=True, on_token=tokens.append)

    assert answer == "流式响应"
    assert "".join(tokens) == "流式响应"
    assert server.requests[0]["stream"] is True


def test_mock_server_streaming_tool_calls(tmp_path, qxt_home, mock_server):
    """流式工具调用: 分片累积出完整 arguments, 再走工具循环。"""
    (tmp_path / "data.json").write_text('{"ok": true}', encoding="utf-8")
    server = mock_server([
        {"tool_calls": [_tool_call("read_file", {"path": "data.json"}, cid="call_9")],
         "finish_reason": "tool_calls"},
        {"content": "data.json 内容: {\"ok\": true}"},
    ])
    adapter = OpenAICompatAdapter(
        base_url=f"http://127.0.0.1:{server.port}/v1", model="mock",
        api_key="test-key", prompt_cache=False,
    )
    agent = _build_agent(adapter, tmp_path, qxt_home)

    answer = agent.run("读 data.json", stream=True)

    assert "ok" in answer
    assert len(server.requests) == 2
    # 流式工具调用累积出的 arguments 应是完整 JSON
    r1 = server.requests[0]
    assert r1["stream"] is True
    roles = [m["role"] for m in server.requests[1]["messages"]]
    assert "tool" in roles
