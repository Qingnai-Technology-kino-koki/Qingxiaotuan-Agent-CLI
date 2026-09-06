"""M8: 模型 mock server 端到端测试 (真实 HTTP 往返)。

用本地 HTTP server 模拟 OpenAI 兼容 /v1/chat/completions 端点, 让真实
OpenAICompatAdapter 走完整网络栈驱动 Agent 工具循环:

- 非流式: 工具调用 → 工具结果回填 → 最终回答;
- 流式: SSE chunk 逐片送达, on_token 回调收到内容;
- run_shell + 安全护栏: 良性命令 (echo/pytest 等) 必须穿透安全引擎, 不被误杀;
- 5xx 重试: 端点前几次返回 503, Agent 的 RetryPolicy 应自动退避重试直到成功;
- 验证 mock 端确实收到了工具结果 (证明 ReAct 循环在线上闭环)。

全程离线, 不访问外网。mock 端点设施见 conftest.py。
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from conftest import _tool_call, _spec_to_payload  # noqa: F401  (共享 mock 端点)

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


def test_mock_server_run_shell_benign_passes_safety(tmp_path, qxt_home, mock_server):
    """核心链路: qxt → 工具执行 → 安全评估。

    良性 shell 命令 (echo) 必须穿透安全护栏正常执行, 不被误判为高风险而拦截。
    """
    server = mock_server([
        {"tool_calls": [_tool_call("run_shell", {"command": "echo SAFE_OK"}, cid="call_shell")],
         "finish_reason": "tool_calls"},
        {"content": "shell 执行成功"},
    ])
    adapter = OpenAICompatAdapter(
        base_url=f"http://127.0.0.1:{server.port}/v1", model="mock",
        api_key="test-key", prompt_cache=False,
    )
    agent = _build_agent(adapter, tmp_path, qxt_home)

    answer = agent.run("执行 echo SAFE_OK", stream=False)

    # 助手最终回答
    assert "shell 执行成功" in answer
    # 两轮: 首轮工具调用, 次轮带工具结果 (证明 run_shell 真跑通且未误杀)
    assert len(server.requests) == 2
    tool_msg = next(m for m in server.requests[1]["messages"] if m["role"] == "tool")
    assert "SAFE_OK" in tool_msg["content"]
    assert "[已拦截]" not in tool_msg["content"]


class _FlakyServer:
    """前 fail_times 次返回 503, 之后按脚本正常返回。

    计数器存在 server 实例上 (而非 per-request handler), 保证多次请求共享同一计数。
    """

    def __init__(self, fail_times, script):
        self.fail_remaining = fail_times
        self.script = list(script)
        self.requests: list = []
        self._lock = threading.Lock()
        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), self._make_handler())
        self.port = self._httpd.server_address[1]

    def _make_handler(self):
        server = self

        class _H(BaseHTTPRequestHandler):
            def log_message(self, *args):  # 静默
                pass

            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length)
                with server._lock:
                    server.requests.append(json.loads(body))
                    if server.fail_remaining > 0:
                        server.fail_remaining -= 1
                        self.send_response(503)
                        self.end_headers()
                        self.wfile.write(b'{"error":"service unavailable"}')
                        return
                    spec = server.script.pop(0)
                data = json.dumps(_spec_to_payload(spec)).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        return _H

    def start(self):
        threading.Thread(target=self._httpd.serve_forever, daemon=True).start()
        return self

    def shutdown(self):
        self._httpd.shutdown()
        self._httpd.server_close()


def test_mock_server_retry_on_5xx(tmp_path, qxt_home):
    """Agent 韧性: 端点前 2 次 503, RetryPolicy 退避重试后应在第 3 次成功。

    验证 provider-agnostic 链路在瞬时服务端错误下仍能跑通 (不依赖具体模型)。
    """
    srv = _FlakyServer(fail_times=2, script=[{"content": "重试后成功"}]).start()
    try:
        adapter = OpenAICompatAdapter(
            base_url=f"http://127.0.0.1:{srv.port}/v1", model="mock",
            api_key="test-key", prompt_cache=False,
        )
        agent = _build_agent(adapter, tmp_path, qxt_home)
        answer = agent.run("重试一下", stream=False)
        assert answer == "重试后成功"
        # 恰好 3 次请求: 2 次 503 + 1 次 200
        assert len(srv.requests) == 3
    finally:
        srv.shutdown()

