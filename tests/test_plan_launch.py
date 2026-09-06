"""Tests for launch unification + Plan-mode read-only window agent.

背景: 用户要求把 `qxt chat` 统一为单一 `qxt` 启动命令, 保留三种启动形态
`qxt` / `qxt --yolo` / `qxt --plan`; 其中 `--plan` 必须让「窗口 Agent」只读
(对标 Claude Code 的 Plan Mode: 模型只能观察、不能修改任何文件)。
"""

import pytest

from qingxiaotuan.cli.parser import build_parser
from qingxiaotuan.tools.base import Tool, ToolRegistry, ToolContext


# ====================================================================== 启动入口统一
# ======================================================================

def test_bare_qxt_launches_chat():
    """`qxt` (无子命令) 应进入交互对话 (cmd=None 由 fast_chat/cmd_chat 接管)。"""
    args = build_parser().parse_args([])
    assert args.cmd is None


def test_chat_subcommand_removed():
    """`qxt chat` 已删除: 解析应报错 (invalid choice)。"""
    with pytest.raises(SystemExit):
        build_parser().parse_args(["chat"])


def test_plan_flag_parses():
    """`qxt --plan` 解析出 plan=True, 且无子命令 (走交互启动)。"""
    args = build_parser().parse_args(["--plan"])
    assert args.plan is True
    assert args.cmd is None


def test_mode_plan_parses():
    """`qxt --mode plan` 解析出 mode='plan'。"""
    args = build_parser().parse_args(["--mode", "plan"])
    assert args.mode == "plan"


def test_yolo_flag_unchanged():
    """`qxt --yolo` 仍可用。"""
    args = build_parser().parse_args(["--yolo"])
    assert args.yolo is True


# ====================================================================== Plan 模式硬只读
# ======================================================================

def _registry_with_rw():
    reg = ToolRegistry()
    reg.register(Tool(name="read_file", description="r",
                      parameters={"type": "object", "properties": {}, "required": []},
                      handler=lambda ctx: "READ_OK", read_only=True, group="fs"))
    reg.register(Tool(name="write_file", description="w",
                      parameters={"type": "object", "properties": {}, "required": []},
                      handler=lambda ctx: "WRITE_OK", read_only=False, group="fs"))
    return reg


def test_plan_mode_denies_write_tools():
    """Plan 模式下, 任何修改类工具调用在分发层被硬拒 (status=denied)。"""
    reg = _registry_with_rw()
    ctx = ToolContext(kernel=None, workspace=".")
    ctx.plan_mode = True
    res = reg.dispatch_result("write_file", "{}", ctx)
    assert res.status == "denied"
    assert "Plan" in res.content or "只读" in res.content


def test_plan_mode_allows_read_tools():
    """Plan 模式下, 只读工具仍可用。"""
    reg = _registry_with_rw()
    ctx = ToolContext(kernel=None, workspace=".")
    ctx.plan_mode = True
    res = reg.dispatch_result("read_file", "{}", ctx)
    assert res.status == "ok"
    assert res.content == "READ_OK"


def test_standard_mode_allows_write_tools():
    """非 Plan 模式 (standard) 下, 写工具正常执行。"""
    reg = _registry_with_rw()
    ctx = ToolContext(kernel=None, workspace=".")
    ctx.plan_mode = False
    res = reg.dispatch_result("write_file", "{}", ctx)
    assert res.status == "ok"
    assert res.content == "WRITE_OK"


def test_agent_effective_tool_set_plan():
    """agent.plan_mode=True 时, effective_tool_set() 返回 'plan' (模型只看到只读工具)。"""
    from qingxiaotuan.core.agent import Agent
    agent = Agent.__new__(Agent)
    agent.plan_mode = True
    agent.yolo = False
    assert agent.effective_tool_set() == "plan"
    agent.plan_mode = False
    assert agent.effective_tool_set() == "standard"
