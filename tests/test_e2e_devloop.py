"""端到端集成测试 (离线): 真实工具 + FakeModel 驱动 DevLoop 完整链路。

验证: Agent.run 能真正调用 write_file / run_tests, 真实写入文件并运行真实 pytest,
DevLoop 收到【已完成】后在检查点停止。这条链路把 agent/loop/context/tools/code 全串起来。
"""

import json
import sys
from pathlib import Path

from qingxiaotuan.core.agent import Agent
from qingxiaotuan.app import build_kernel, create_agent
from qingxiaotuan.core.devloop import DevLoop
from qingxiaotuan.models.base import ModelAdapter, ModelResponse, ToolCall


class ScriptedModel(ModelAdapter):
    """按预设脚本逐轮返回; 支持根据上一轮工具结果调整 (此处用固定脚本即可)。"""
    name = "scripted"

    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def chat(self, messages, tools=None, stream=False, on_token=None, **kwargs):
        self.calls.append(messages)
        return self.script.pop(0)


def _build(tmp_path, script):
    kernel = build_kernel()
    config = kernel.require("config")
    config.data["agent"]["skill_nudge_interval"] = 0
    config.data["loop"]["ask_every"] = 1
    config.data["loop"]["max_iterations"] = 5
    kernel.unprovide("model_adapter")
    model = ScriptedModel(script)
    kernel.provide("model_adapter", model, owner="test")
    # 排除可能访问网络的工具, 只保留文件 + 代码自测
    agent = Agent(
        kernel=kernel, config=config, workspace=str(tmp_path),
        confirm=lambda _p: True, exclude_tools=("web_fetch", "memory_write", "skill_save"),
    )
    return agent, config, model


def test_e2e_devloop_writes_file_and_runs_real_tests(tmp_path, qxt_home):
    ws = tmp_path
    # 预置一个会失败的测试, 让 Agent 先修复它
    (ws / "mathlib.py").write_text("def add(a, b):\n    return a - b  # bug\n")
    (ws / "test_mathlib.py").write_text(
        "from mathlib import add\n\ndef test_add():\n    assert add(2, 3) == 5\n"
    )

    script = [
        # 第 1 轮: 读测试 -> 发现 bug -> 用 edit_file 修复
        ModelResponse(
            content="先读测试定位问题",
            tool_calls=[ToolCall(id="c1", name="read_file",
                                  arguments=json.dumps({"path": "mathlib.py"}))],
        ),
        # 第 2 轮: 修复 bug
        ModelResponse(
            content="修复 add 的减号 bug",
            tool_calls=[ToolCall(id="c2", name="edit_file",
                                  arguments=json.dumps({
                                      "path": "mathlib.py",
                                      "old_string": "return a - b  # bug",
                                      "new_string": "return a + b",
                                  }))],
        ),
        # 第 3 轮: 跑测试 (显式用当前解释器, 保证 pytest 可用)
        ModelResponse(
            content="运行测试验证",
            tool_calls=[ToolCall(id="c3", name="run_tests",
                                  arguments=json.dumps({"command": f'"{sys.executable}" -m pytest -q'}))],
        ),
        # 第 4 轮: 汇报完成
        ModelResponse(content="已修复 add 并跑通测试\n【已完成】mathlib.add 现返回正确和, 测试通过。"),
    ]

    agent, config, model = _build(ws, script)
    loop = DevLoop(agent, config, on_checkpoint=lambda r: "done")
    out = loop.run("修复 mathlib.add 让其返回和", stream=False)

    # 1) 文件真被改了
    assert "return a + b" in (ws / "mathlib.py").read_text(encoding="utf-8")
    # 2) 测试真跑通了 (run_tests 工具产出 passed) — 去掉 ANSI 色码后匹配
    import re as _re
    tool_results = " ".join(
        m.get("content", "") for m in agent.messages if m.get("role") == "tool"
    )
    clean = _re.sub(r"\x1b\[[0-9;]*m", "", tool_results)
    assert "passed" in clean, f"run_tests 未通过: {tool_results[:400]}"
    # 3) DevLoop 正确收敛
    assert "用户确认完成" in out
    # 4) 真实调用了 edit_file 与 run_tests (从模型记录的 messages 里提取 tool_calls)
    names = [tc["function"]["name"] for msgs in model.calls for m in msgs
             if m.get("role") == "assistant" for tc in (m.get("tool_calls") or [])]
    assert "edit_file" in names
    assert "run_tests" in names


def test_e2e_context_preserves_codebase_map(tmp_path, qxt_home):
    """验证大上下文: 系统提示钉入代码库地图, 且长会话压缩后地图不丢。"""
    ws = tmp_path
    (ws / "app.py").write_text("def main():\n    pass\n")
    # 走真实 create_agent 路径 (自动构建 indexer 并钉入地图)
    kernel = build_kernel()
    config = kernel.require("config")
    config.data["context"]["keep_recent"] = 4
    config.data["context"]["budget_tokens"] = 200  # 强制触发压缩
    config.data["context"]["compact_trigger"] = 200  # 触发阈值同步调低
    agent = create_agent(kernel, str(ws), confirm=lambda _p: True)

    # 系统提示应含代码库地图 (大上下文机制核心不变量)
    assert "代码库地图" in agent._system_prompt

    # 塞入长历史
    agent.messages.append({"role": "system", "content": agent._system_prompt})
    for i in range(12):
        agent.messages.append({"role": "user", "content": f"历史消息 {i} " + "x" * 50})
    before_len = len(agent.messages)
    agent._compress_if_needed()
    # 压缩后系统提示(含代码库地图)应仍在最前
    assert agent.messages[0]["role"] == "system"
    assert "代码库地图" in agent.messages[0]["content"]
    # 最近消息保留
    assert any("历史消息 11" in m.get("content", "") for m in agent.messages)
    assert len(agent.messages) < before_len
