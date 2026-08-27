"""Tests for enhancements: parallel tool execution, /diff, /undo, shell timeout, context compression fix."""

import json
import os
import time
from unittest.mock import MagicMock, patch

import pytest

from qingxiaotuan.context.manager import ContextManager, estimate_messages
from qingxiaotuan.core.agent import Agent


# ======================================================================
# 1. 并行工具执行
# ======================================================================

def _make_fake_agent_for_tools(tmp_path):
    """构造一个最小化的 Agent, 用于测试 _execute_tools 方法。"""
    kernel = MagicMock()
    config = MagicMock()
    config.get = lambda k, d=None: {"agent.max_iterations": 5, "agent.skill_nudge_interval": 0}.get(k, d)
    config.is_yolo = lambda: False

    # mock tool registry with some tools
    registry = MagicMock()
    call_log = []

    def fake_dispatch(name, args_json, ctx):
        call_log.append(name)
        # 模拟只读工具快速返回
        return f"[{name}] result"

    registry.dispatch = fake_dispatch
    registry.tools = []
    kernel.require = lambda s: registry
    kernel.get = lambda s: None

    agent = Agent.__new__(Agent)
    agent.kernel = kernel
    agent.config = config
    agent.workspace = str(tmp_path)
    agent.registry = registry
    agent.exclude_tools = set()
    agent.yolo = False
    agent.plan_mode = False
    agent._cancel_event = __import__("threading").Event()
    agent.turn_count = 0
    agent.total_usage = {}

    from qingxiaotuan.tools.base import ToolContext
    agent.ctx = ToolContext(kernel=kernel, workspace=str(tmp_path))

    agent.messages = []
    agent._session_append = lambda *a, **kw: None

    # Agent 重构后 _execute_tools 委托给 ToolExecutor (core/tool_executor.py)
    from qingxiaotuan.core.tool_executor import ToolExecutor
    agent._tool_executor = ToolExecutor(
        registry=registry,
        messages=agent.messages,
        session_append=agent._session_append,
        tool_content_fn=lambda x: x,
    )

    return agent, call_log


def test_parallel_readonly_tools(tmp_path):
    """多个只读工具应被批量并行执行 (同一批次)。"""
    agent, call_log = _make_fake_agent_for_tools(tmp_path)

    tool_calls = [
        {"id": "1", "function": {"name": "read_file", "arguments": json.dumps({"paths": ["a.py"]})}},
        {"id": "2", "function": {"name": "code_search", "arguments": json.dumps({"pattern": "foo"})}},
        {"id": "3", "function": {"name": "list_directory", "arguments": json.dumps({"path": "."})}},
    ]

    agent._execute_tools(tool_calls)

    # 所有三个只读工具都应被执行
    assert set(call_log) == {"read_file", "code_search", "list_directory"}
    # 应有 3 条 tool 消息 (按顺序)
    tool_msgs = [m for m in agent.messages if m.get("role") == "tool"]
    assert len(tool_msgs) == 3
    # tool_call_id 应与原始顺序一致
    assert [m["tool_call_id"] for m in tool_msgs] == ["1", "2", "3"]


def test_write_tool_blocks_parallel(tmp_path):
    """写工具应串行执行, 不与只读工具并行。"""
    agent, call_log = _make_fake_agent_for_tools(tmp_path)

    tool_calls = [
        {"id": "1", "function": {"name": "read_file", "arguments": json.dumps({"paths": ["a.py"]})}},
        {"id": "2", "function": {"name": "write_file", "arguments": json.dumps({"path": "b.py", "content": "x=1", "instructions": "test"})}},
        {"id": "3", "function": {"name": "code_search", "arguments": json.dumps({"pattern": "bar"})}},
    ]

    agent._execute_tools(tool_calls)

    # 所有工具都应被执行
    assert set(call_log) == {"read_file", "write_file", "code_search"}
    # 顺序应保持: read_file, write_file, code_search
    assert call_log == ["read_file", "write_file", "code_search"]


# ======================================================================
# 2. 上下文压缩 bug 修复验证
# ======================================================================

def test_compact_summarize_receives_list_not_string():
    """确保 _compact_once 传给 _summarize 的是 List[Dict], 而非序列化字符串。"""
    received_args = []

    def capture_summarize(messages):
        received_args.append(messages)
        return "摘要内容"

    cm = ContextManager(keep_recent=2, budget_tokens=50, strategy="smart",
                        summarize=capture_summarize)
    msgs = [{"role": "system", "content": "sys"}]
    for i in range(20):
        msgs.append({"role": "user", "content": f"msg{i}" + "x" * 80})

    new, dropped = cm.compact_if_needed(msgs)
    assert dropped > 0
    # _summarize 应该收到 list 而不是 string
    assert len(received_args) >= 1
    assert isinstance(received_args[0], list), f"Expected list, got {type(received_args[0])}"
    assert isinstance(received_args[0][0], dict), f"Expected dict, got {type(received_args[0][0])}"


# ======================================================================
# 3. Shell 超时处理
# ======================================================================

def test_run_shell_timeout_returns_friendly_error(qxt_home):
    """shell 命令超时应返回友好错误而非抛异常。"""
    from qingxiaotuan.tools.shell import run_shell
    from qingxiaotuan.tools.base import ToolContext

    kernel = MagicMock()
    config = MagicMock()
    config.get = lambda k, d=None: {"tools.shell.timeout": 1, "tools.shell.enabled": True}.get(k, d)
    kernel.get = lambda s: config if s == "config" else None

    ctx = ToolContext(kernel=kernel, workspace=".", yolo=False)
    # 一个必定超时的命令
    result = run_shell(ctx, "python -c \"import time; time.sleep(30)\"", timeout=1)
    assert "超时" in result or "exit=-1" in result


def test_run_shell_normal_command(qxt_home):
    """正常命令应正常返回。"""
    from qingxiaotuan.tools.shell import run_shell
    from qingxiaotuan.tools.base import ToolContext

    kernel = MagicMock()
    config = MagicMock()
    config.get = lambda k, d=None: {"tools.shell.timeout": 10, "tools.shell.enabled": True}.get(k, d)
    kernel.get = lambda s: config if s == "config" else None

    ctx = ToolContext(kernel=kernel, workspace=".", yolo=False)
    result = run_shell(ctx, "echo hello", timeout=5)
    assert "hello" in result
    assert "exit=0" in result


# ======================================================================
# 4. /diff 命令
# ======================================================================

def test_cmd_diff_clean_workspace(tmp_path):
    """干净工作区应报告无变更。"""
    from qingxiaotuan.cli.commands import _cmd_diff
    from unittest.mock import patch as mock_patch

    with mock_patch("qingxiaotuan.cli.commands.ui") as mock_ui:
        # 初始化 git 仓库
        os.system(f"cd {tmp_path} && git init -q && git commit -q --allow-empty -m init")
        _cmd_diff(str(tmp_path), "")
        # 应该调用 info 表示无变更
        calls = [str(c) for c in mock_ui.info.call_args_list]
        assert any("干净" in c or "没有变更" in c for c in calls)


# ======================================================================
# 5. /undo 命令
# ======================================================================

def test_cmd_undo_safe_stash(tmp_path):
    """--safe 应该 stash 变更。"""
    from qingxiaotuan.cli.commands import _cmd_undo
    from unittest.mock import patch as mock_patch, MagicMock

    # 初始化 git 仓库
    os.system(f"cd {tmp_path} && git init -q && git commit -q --allow-empty -m init")

    # 创建一个变更文件
    (tmp_path / "test.txt").write_text("hello", encoding="utf-8")

    agent = MagicMock()
    agent.ctx.confirm = lambda p: True  # 自动确认

    with mock_patch("qingxiaotuan.cli.commands.ui") as mock_ui:
        _cmd_undo(str(tmp_path), "--safe", agent)
        calls = [str(c) for c in mock_ui.success.call_args_list]
        assert any("stash" in c for c in calls)


# ======================================================================
# 6. Agent._summarize 正确处理消息列表
# ======================================================================

def test_agent_summarize_extracts_info():
    """Agent._summarize 应能从消息列表中提取结构化信息。"""
    from qingxiaotuan.core.agent import Agent
    from qingxiaotuan.tools.base import ToolContext

    # 构造最小 agent
    agent = Agent.__new__(Agent)
    agent.workspace = "."

    messages = [
        {"role": "user", "content": "请帮我修改 main.py 的 foo 函数"},
        {"role": "assistant", "content": "好的, 我来修改", "tool_calls": [
            {"function": {"name": "read_file", "arguments": json.dumps({"paths": ["main.py"]})}}
        ]},
        {"role": "tool", "content": "def foo(): pass"},
        {"role": "assistant", "content": "已修改完成, foo 函数现在返回 42"},
    ]

    summary = agent._summarize(messages)
    assert isinstance(summary, str)
    assert len(summary) > 0
    # 应该提取到用户目标
    assert "main.py" in summary or "foo" in summary
