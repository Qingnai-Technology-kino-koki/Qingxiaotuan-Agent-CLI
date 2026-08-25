"""测试: 会话恢复 (--resume / /resume) —— 对标 Claude Code 的 --continue/--resume。

覆盖:
- 从会话文件重建 messages 并补回系统提示
- 按路径 / 会话id前缀 / 编号 / latest 恢复
- 未找到会话时的失败处理
- /resume 斜杠命令列出与恢复
"""

from __future__ import annotations

import json

from qingxiaotuan.core.agent import Agent
from qingxiaotuan.app import build_kernel
from qingxiaotuan.models.base import ModelAdapter
from qingxiaotuan.memory.sessions import SessionStore


class MockModel(ModelAdapter):
    name = "mock"

    def chat(self, messages, tools=None, stream=False, on_token=None, **kwargs):
        return None


def _build_agent(tmp_path, qxt_home):
    kernel = build_kernel()
    config = kernel.require("config")
    config.data["agent"]["skill_nudge_interval"] = 0
    kernel.unprovide("model_adapter")
    kernel.provide("model_adapter", MockModel(), owner="test")
    return Agent(kernel=kernel, config=config, workspace=str(tmp_path), confirm=lambda _p: True)


def _make_session(agent, task: str = "你好") -> SessionStore:
    """用真实 SessionStore 写一个会话文件, 返回 store (其 file 即会话文件)。"""
    store = SessionStore(agent.config.home)
    store.append("session.meta", {"task": task})
    store.append("user", {"message": {"role": "user", "content": task}})
    store.append("assistant", {"message": {"role": "assistant", "content": "收到, 我来处理。"}})
    store.append("tool_call", {"name": "read_file", "arguments": "{}"})
    store.append("tool", {"message": {"role": "tool", "tool_call_id": "c1", "name": "read_file",
                                      "content": "file content"}, "name": "read_file", "content": "file content"})
    return store


def test_load_session_prepends_system_prompt(tmp_path, qxt_home):
    """恢复时 messages = [system] + 历史, 系统提示在最前。"""
    from qingxiaotuan.cli.commands import _load_session_into_agent
    agent = _build_agent(tmp_path, qxt_home)
    store = _make_session(agent)
    name = _load_session_into_agent(agent, store.file)
    assert name == store.file.name
    assert agent.messages[0]["role"] == "system"
    assert agent.messages[0]["content"] == agent._system_prompt
    assert agent.messages[1]["role"] == "user"
    assert agent.messages[1]["content"] == "你好"
    assert agent.messages[2]["role"] == "assistant"
    assert agent.messages[3]["role"] == "tool"


def test_resume_by_path(tmp_path, qxt_home):
    from qingxiaotuan.cli.commands import _resume_session
    agent = _build_agent(tmp_path, qxt_home)
    store = _make_session(agent)
    name = _resume_session(agent, str(store.file))
    assert name == store.file.name
    assert agent.messages[1]["content"] == "你好"


def test_resume_by_id_prefix(tmp_path, qxt_home):
    from qingxiaotuan.cli.commands import _resume_session
    agent = _build_agent(tmp_path, qxt_home)
    store = _make_session(agent)
    prefix = store.file.stem[:8]
    name = _resume_session(agent, prefix)
    assert name == store.file.name


def test_resume_by_index(tmp_path, qxt_home):
    from qingxiaotuan.cli.commands import _resume_session
    agent = _build_agent(tmp_path, qxt_home)
    store = _make_session(agent)
    name = _resume_session(agent, "1")
    assert name == store.file.name


def test_resume_latest(tmp_path, qxt_home):
    from qingxiaotuan.cli.commands import _resume_session
    agent = _build_agent(tmp_path, qxt_home)
    _make_session(agent, "第一个会话")
    store2 = _make_session(agent, "第二个会话")
    name = _resume_session(agent, "latest")
    assert name == store2.file.name
    assert agent.messages[1]["content"] == "第二个会话"


def test_resume_not_found(tmp_path, qxt_home, capsys):
    from qingxiaotuan.cli.commands import _resume_session
    agent = _build_agent(tmp_path, qxt_home)
    assert _resume_session(agent, "no-such-session") is None
    out = capsys.readouterr().out
    assert "未找到会话" in out


def test_resume_index_out_of_range(tmp_path, qxt_home, capsys):
    from qingxiaotuan.cli.commands import _resume_session
    agent = _build_agent(tmp_path, qxt_home)
    _make_session(agent)
    # 越界数字回退到前缀匹配, 找不到时报"未找到会话"
    assert _resume_session(agent, "99") is None
    out = capsys.readouterr().out
    assert "未找到会话" in out


def test_slash_resume_list_and_resume(tmp_path, qxt_home, capsys):
    from qingxiaotuan.cli.commands import _handle_slash
    agent = _build_agent(tmp_path, qxt_home)
    config = agent.config
    store = _make_session(agent, "恢复测试任务")

    # 无参: 列出会话
    assert _handle_slash("/resume", agent, config, str(tmp_path)) is True
    out = capsys.readouterr().out
    assert "恢复测试任务" in out
    assert "/resume <编号" in out

    # 按编号恢复
    assert _handle_slash("/resume 1", agent, config, str(tmp_path)) is True
    assert agent.messages[1]["content"] == "恢复测试任务"
    out = capsys.readouterr().out
    assert "已恢复会话" in out
    assert store.file.name in out
