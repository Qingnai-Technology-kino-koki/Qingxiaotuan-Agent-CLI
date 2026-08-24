"""Agent 主循环测试 (Mock 模型, 全程离线)。

验证: ReAct 循环 -> 工具执行 -> 技能 nudge 蒸馏 -> 会话事件流落盘。
"""

import json

from qingxiaotuan.core.agent import Agent
from qingxiaotuan.app import build_kernel
from qingxiaotuan.models.base import ModelAdapter, ModelResponse, ToolCall


class MockModel(ModelAdapter):
    name = "mock"

    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def chat(self, messages, tools=None, stream=False, on_token=None, **kwargs):
        self.calls.append(messages)
        resp = self.script.pop(0)
        if stream and resp.content and on_token:
            on_token(resp.content)
        return resp


def _build_agent(tmp_path, qxt_home, script, nudge_interval=1):
    kernel = build_kernel()
    config = kernel.require("config")
    config.data["agent"]["skill_nudge_interval"] = nudge_interval
    kernel.unprovide("model_adapter")
    kernel.provide("model_adapter", MockModel(script), owner="test")
    return Agent(kernel=kernel, config=config, workspace=str(tmp_path), confirm=lambda _p: True)


def test_react_loop_executes_tool_and_finishes(tmp_path, qxt_home):
    agent = _build_agent(tmp_path, qxt_home, [
        ModelResponse(tool_calls=[ToolCall(
            id="c1", name="write_file",
            arguments=json.dumps({"path": "out.txt", "content": "hello"}))]),
        ModelResponse(content="文件已写好"),
        ModelResponse(content=""),  # nudge 响应: 无需蒸馏
    ], nudge_interval=0)  # 关闭 nudge, 纯测循环

    answer = agent.run("写一个 hello 文件", stream=False)
    assert answer == "文件已写好"
    assert (tmp_path / "out.txt").read_text(encoding="utf-8") == "hello"
    # messages 里应有 tool 回执
    assert any(m.get("role") == "tool" for m in agent.messages)


def test_skill_nudge_triggers_distillation(tmp_path, qxt_home):
    agent = _build_agent(tmp_path, qxt_home, [
        ModelResponse(content="任务完成"),
        # nudge 轮: 模型决定蒸馏技能
        ModelResponse(tool_calls=[ToolCall(id="s1", name="skill_save", arguments=json.dumps({
            "name": "Greeting File", "description": "创建问候文件的方法",
            "body": "用 write_file 写入问候语"}))]),
    ], nudge_interval=1)

    agent.run("打个招呼", stream=False)

    manager = agent.kernel.require("skill_manager")
    skill = manager.load("greeting-file")
    assert skill is not None
    assert "write_file" in skill.body


def test_session_event_stream_written(tmp_path, qxt_home):
    agent = _build_agent(tmp_path, qxt_home, [
        ModelResponse(tool_calls=[ToolCall(
            id="c1", name="list_dir", arguments="{}")]),
        ModelResponse(content="done"),
    ], nudge_interval=0)
    agent.run("看看目录", stream=False)

    session = agent.kernel.require("session_store")
    lines = session.file.read_text(encoding="utf-8").splitlines()
    types = [json.loads(l)["type"] for l in lines]
    assert "user" in types
    assert "tool_call" in types
    assert "tool" in types
    assert "assistant" in types


def test_dangerous_tool_needs_confirmation(tmp_path, qxt_home):
    denied = []
    kernel = build_kernel()
    config = kernel.require("config")
    config.data["agent"]["skill_nudge_interval"] = 0
    kernel.unprovide("model_adapter")
    kernel.provide("model_adapter", MockModel([
        ModelResponse(tool_calls=[ToolCall(
            id="c1", name="run_shell", arguments=json.dumps({"command": "echo hi"}))]),
        ModelResponse(content="被拒绝了"),
    ]), owner="test")
    agent = Agent(kernel=kernel, config=config, workspace=str(tmp_path),
                  confirm=lambda p: denied.append(p) or False)
    answer = agent.run("执行 echo", stream=False)
    assert answer == "被拒绝了"
    assert denied  # 确认回调被调用过
    tool_msgs = [m for m in agent.messages if m.get("role") == "tool"]
    assert "已拒绝" in tool_msgs[0]["content"]


def test_context_compression(tmp_path, qxt_home):
    agent = _build_agent(tmp_path, qxt_home, [ModelResponse(content="ok")], nudge_interval=0)
    # 用新上下文管理器: 把预算调到极低, 触发智能压缩
    agent.config.data["context"]["budget_tokens"] = 10
    agent.config.data["context"]["keep_recent"] = 4
    agent.context_manager.budget_tokens = 10
    agent.context_manager.compact_trigger = 10
    agent.context_manager.keep_recent = 4
    # 塞入超长历史
    agent.messages.append({"role": "system", "content": "sys"})
    for i in range(20):
        agent.messages.append({"role": "user", "content": f"msg{i}" * 20})
    agent._compress_if_needed()
    assert agent.messages[0]["role"] == "system"
    # 最近 4 条保留 + 一条摘要占位
    assert len(agent.messages) <= 6
    assert any("摘要" in str(m.get("content")) or "上下文" in str(m.get("content")) for m in agent.messages)
