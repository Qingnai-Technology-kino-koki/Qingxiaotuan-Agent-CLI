"""task / background_status 工具 + AgentType 类型注册表测试 (Mock 模型, 全程离线)。

验证 (对标 Claude Code 的 Task 工具):
- AgentType 注册表与 readonly_tool_names 计算;
- system_extra 注入子代理系统提示;
- task 前台委派 (thread 软隔离) 与类型校验;
- 只读类型物理阻断写入 (exclude_tools 生效), general-purpose 对照可写;
- run_in_background 后台模式 + background_status 查询闭环。
"""

import json
import time

from qingxiaotuan.app import build_kernel
from qingxiaotuan.core.agent import Agent
from qingxiaotuan.core.agent_types import (
    get_agent_type,
    readonly_tool_names,
    render_types_for_prompt,
    type_names,
)
from qingxiaotuan.models.base import ModelAdapter, ModelResponse, ToolCall
from qingxiaotuan.tools.base import Tool, ToolRegistry


class EchoModel(ModelAdapter):
    """单轮模型: 把最后一条 user 消息回声作为答案 (不调工具)。"""
    name = "echo"

    def chat(self, messages, tools=None, stream=False, on_token=None, **kwargs):
        last_user = ""
        for m in reversed(messages):
            if m.get("role") == "user" and m.get("content"):
                last_user = m["content"]
                break
        resp = ModelResponse(content=f"回声: {last_user[:50]}")
        if stream and on_token:
            on_token(resp.content)
        return resp


class WriteOnceModel(ModelAdapter):
    """奇数轮硬调 write_file (无视 schema), 偶数轮复述上一条工具观察后收尾。

    用于验证只读类型的物理阻断: explore 下写入被 exclude_tools 拦下,
    观察文本含「已禁用」; general-purpose 对照下同一调用能真实落盘。
    """
    name = "write-once"

    def __init__(self):
        self.n = 0

    def chat(self, messages, tools=None, stream=False, on_token=None, **kwargs):
        self.n += 1
        if self.n % 2 == 1:
            return ModelResponse(tool_calls=[ToolCall(
                id=f"c{self.n}", name="write_file",
                arguments=json.dumps({"path": "t.txt", "content": "x"}),
            )])
        obs = ""
        for m in reversed(messages):
            if m.get("role") == "tool":
                obs = str(m.get("content", ""))
                break
        return ModelResponse(content=f"观察: {obs}")


def _make_ctx(tmp_path, model):
    """构造共享内核 + 主 Agent ctx (thread 软隔离, mock 模型进程内)。"""
    kernel = build_kernel()
    config = kernel.require("config")
    config.set_user("agent.subagent_isolation", "thread")
    kernel.unprovide("model_adapter")
    kernel.provide("model_adapter", model, owner="test")
    agent = Agent(kernel=kernel, config=config,
                  workspace=str(tmp_path), confirm=lambda _p: True)
    return kernel, agent.ctx


# ------------------------------------------------------------------ 类型注册表

def test_agent_type_registry():
    names = type_names()
    for n in ("general-purpose", "explore", "plan", "coder"):
        assert n in names
        t = get_agent_type(n)
        assert t is not None and t.name == n and t.description
    assert get_agent_type("explore").read_only
    assert get_agent_type("plan").read_only
    assert not get_agent_type("general-purpose").read_only
    assert not get_agent_type("coder").read_only
    assert get_agent_type("no-such-type") is None
    # 清单可渲染给模型看
    text = render_types_for_prompt()
    assert "- explore" in text and "只读" in text


def test_readonly_tool_names_computation():
    reg = ToolRegistry()
    reg.register(Tool(name="ro_a", description="", parameters={},
                      handler=lambda c: "", read_only=True))
    reg.register(Tool(name="rw_b", description="", parameters={}, handler=lambda c: ""))
    names = readonly_tool_names(reg)
    assert names == ["rw_b"]


def test_system_extra_injected_into_system_prompt(tmp_path, qxt_home):
    kernel = build_kernel()
    config = kernel.require("config")
    kernel.unprovide("model_adapter")
    kernel.provide("model_adapter", EchoModel(), owner="test")
    plain = Agent(kernel=kernel, config=config, workspace=str(tmp_path))
    marked = Agent(kernel=kernel, config=config, workspace=str(tmp_path),
                   system_extra="你是专属角色X。")
    # 注入进初始系统提示; 且重复构建幂等 (不重复追加、不影响稳定提示结构)
    assert "专属角色X" in marked._system_prompt
    assert marked._build_system() == marked._system_prompt
    assert "专属角色X" not in plain._system_prompt


# ------------------------------------------------------------------ 工具注册与前台委派

def test_task_tools_registered(qxt_home):
    kernel = build_kernel()
    registry = kernel.require("tool_registry")
    names = {t.name for t in registry.tools}
    assert "task" in names and "background_status" in names


def test_task_foreground_dispatch(tmp_path, qxt_home):
    kernel, ctx = _make_ctx(tmp_path, EchoModel())
    tool = kernel.require("tool_registry").get("task")
    out = tool.handler(ctx, description="前台调研", prompt="前台任务X",
                       subagent_type="general-purpose")
    # 子代理结果以汇总块回灌 (状态头 + 回声正文)
    assert "✅完成" in out
    assert "回声:" in out and "前台任务X" in out


def test_task_rejects_unknown_type(tmp_path, qxt_home):
    kernel, ctx = _make_ctx(tmp_path, EchoModel())
    tool = kernel.require("tool_registry").get("task")
    out = tool.handler(ctx, description="x", prompt="y", subagent_type="wizard")
    assert "错误" in out and "wizard" in out and "general-purpose" in out


# ------------------------------------------------------------------ 只读类型物理阻断

def test_task_readonly_type_blocks_writes(tmp_path, qxt_home):
    kernel, ctx = _make_ctx(tmp_path, WriteOnceModel())
    tool = kernel.require("tool_registry").get("task")
    # explore (只读): write_file 被 exclude_tools 物理拦下, 文件不得落盘
    out = tool.handler(ctx, description="探索", prompt="找入口",
                       subagent_type="explore")
    assert not (tmp_path / "t.txt").exists()
    assert "已禁用" in out
    # general-purpose 对照: 同一个模型同样的调用能真实写入
    out2 = tool.handler(ctx, description="落地", prompt="写文件",
                        subagent_type="general-purpose")
    assert (tmp_path / "t.txt").exists()


# ------------------------------------------------------------------ 后台模式 + 状态查询

def test_task_background_and_status(tmp_path, qxt_home):
    kernel, ctx = _make_ctx(tmp_path, EchoModel())
    registry = kernel.require("tool_registry")

    # 干净 ctx 上查询: 尚无任何后台任务
    fresh_ctx = Agent(kernel=kernel, config=kernel.require("config"),
                      workspace=str(tmp_path)).ctx
    empty = registry.get("background_status").handler(fresh_ctx)
    assert "没有" in empty

    tool = registry.get("task")
    out = tool.handler(ctx, description="后台整理", prompt="后台任务Y",
                       run_in_background=True)
    assert "job_id: bg-" in out
    job_id = next(ln.split()[-1] for ln in out.splitlines() if ln.startswith("job_id:"))

    status_tool = registry.get("background_status")
    # 轮询到任务结束 (EchoModel 单轮即完成)
    deadline = time.time() + 15
    final = ""
    while time.time() < deadline:
        final = status_tool.handler(ctx, job_id=job_id)
        if "[done]" in final or "[failed]" in final:
            break
        time.sleep(0.2)
    assert "[done]" in final
    assert "回声" in final

    # 列出全部任务 (不带 job_id)
    listing = status_tool.handler(ctx)
    assert job_id in listing

    # 收尾: 等 worker 线程退出, 防止跨测试泄漏
    job = ctx.background_runner.get(job_id)
    if job is not None and job.thread is not None:
        job.thread.join(timeout=10)
