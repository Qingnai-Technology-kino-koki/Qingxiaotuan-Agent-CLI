"""测试: Plan 模式 (只读) —— 对标 Claude Code 的 Plan Mode。

覆盖:
- 修改类工具在 Plan 模式下被拦截, 只读工具放行
- shell 命令的只读/修改判定
- /plan 斜杠命令切换
- Agent.plan_mode 与 ToolContext.plan_mode 同步
"""

from __future__ import annotations

import json

from qingxiaotuan.core.agent import Agent
from qingxiaotuan.app import build_kernel
from qingxiaotuan.models.base import ModelAdapter
from qingxiaotuan.tools.base import ToolContext, Tool, ToolRegistry
from qingxiaotuan.tools.shell import _is_readonly_command, run_shell


class _FakeKernel:
    def get(self, _):
        return None


def _ctx(plan_mode: bool = False) -> ToolContext:
    return ToolContext(kernel=_FakeKernel(), workspace=".", plan_mode=plan_mode)


def _reg() -> ToolRegistry:
    reg = ToolRegistry()
    reg.register(Tool(name="read_tool", description="d",
                      parameters={"type": "object", "properties": {}},
                      handler=lambda ctx: "read-ok", read_only=True))
    reg.register(Tool(name="write_tool", description="d",
                      parameters={"type": "object", "properties": {}},
                      handler=lambda ctx: "write-ok"))
    return reg


# ----------------------------------------------------------------- dispatch 拦截

def test_plan_mode_blocks_write_tool():
    reg = _reg()
    result = reg.dispatch_result("write_tool", "{}", _ctx(plan_mode=True))
    assert result.status == "denied"
    assert "Plan 模式" in result.content
    assert "write_tool" in result.content


def test_plan_mode_allows_readonly_tool():
    reg = _reg()
    result = reg.dispatch_result("read_tool", "{}", _ctx(plan_mode=True))
    assert result.status == "ok"
    assert result.content == "read-ok"


def test_normal_mode_allows_write_tool():
    reg = _reg()
    result = reg.dispatch_result("write_tool", "{}", _ctx(plan_mode=False))
    assert result.status == "ok"
    assert result.content == "write-ok"


# ----------------------------------------------------------------- shell 命令判定

def test_readonly_command_detection():
    assert _is_readonly_command("ls -la") is True
    assert _is_readonly_command("cat file.txt") is True
    assert _is_readonly_command("git status") is True
    assert _is_readonly_command("git diff") is True
    assert _is_readonly_command("grep foo bar.py") is True
    assert _is_readonly_command("echo hello") is True
    assert _is_readonly_command("") is True


def test_write_command_detection():
    assert _is_readonly_command("rm -rf build") is False
    assert _is_readonly_command("echo hi > out.txt") is False
    assert _is_readonly_command("git add .") is False
    assert _is_readonly_command("git commit -m x") is False
    assert _is_readonly_command("pip install requests") is False
    assert _is_readonly_command("mkdir newdir") is False
    assert _is_readonly_command("cp a b") is False
    # fail-closed: 无法识别的命令一律视为修改类 (宁可多拦不误放)
    assert _is_readonly_command("node script.js") is False
    assert _is_readonly_command("./deploy.sh --prod") is False
    assert _is_readonly_command("sed -i 's/a/b/' f.txt") is False


def test_run_shell_blocked_in_plan_mode():
    out = run_shell(_ctx(plan_mode=True), "rm -rf build")
    assert "Plan 模式" in out
    assert "已阻止修改类命令" in out


def test_run_shell_readonly_passes_in_plan_mode():
    # 只读命令在 Plan 模式放行 (执行真实命令, 验证没被拦截)
    out = run_shell(_ctx(plan_mode=True), "echo plan-ok")
    assert "plan-ok" in out


# ----------------------------------------------------------------- /plan 斜杠命令

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


def test_slash_plan_toggles(tmp_path, qxt_home, capsys):
    from qingxiaotuan.cli.commands import _handle_slash
    agent = _build_agent(tmp_path, qxt_home)
    config = agent.config

    assert agent.plan_mode is False
    assert _handle_slash("/plan", agent, config, str(tmp_path)) is True
    assert agent.plan_mode is True
    assert agent.ctx.plan_mode is True
    out = capsys.readouterr().out
    assert "Plan 模式已开启" in out

    assert _handle_slash("/plan", agent, config, str(tmp_path)) is True
    assert agent.plan_mode is False
    assert agent.ctx.plan_mode is False
    out = capsys.readouterr().out
    assert "Plan 模式已关闭" in out


def test_slash_plan_explicit_on_off(tmp_path, qxt_home, capsys):
    from qingxiaotuan.cli.commands import _handle_slash
    agent = _build_agent(tmp_path, qxt_home)
    config = agent.config

    assert _handle_slash("/plan on", agent, config, str(tmp_path)) is True
    assert agent.plan_mode is True
    assert _handle_slash("/plan off", agent, config, str(tmp_path)) is True
    assert agent.plan_mode is False


# ----------------------------------------------------------------- Agent 同步

def test_agent_plan_mode_syncs_to_ctx(tmp_path, qxt_home):
    agent = _build_agent(tmp_path, qxt_home)
    assert agent.ctx.plan_mode is False
    agent.plan_mode = True
    agent.ctx.plan_mode = agent.plan_mode
    assert agent.ctx.plan_mode is True
