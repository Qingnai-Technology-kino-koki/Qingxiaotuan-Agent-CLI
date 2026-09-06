"""Regression test: 主模型调用 (agent.run 循环) 必须按 effective_tool_set() 过滤工具。

背景: 之前 run() 主循环直接调 `registry.schemas()` (无 tool_set), 把 60+ 工具
一股脑塞给模型; plan 模式只在分发层硬拒, 模型仍"看得到"写工具。现在主循环应
走 tools/base.py 的 TOOL_SETS 专用模块 —— standard/yolo=全量, plan=仅只读子集
(且 exit_plan_mode 永远可退出)。

本测试构造最小 Agent + 假模型, 捕获主调用实际传给 schemas 的 tool_set 与返回清单,
证明标准/plan 两种模式都接入了专用模块。
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

from qingxiaotuan.core.agent import Agent
from qingxiaotuan.tools.base import Tool, ToolRegistry
from qingxiaotuan.core.tool_executor import ToolExecutor


def _registry_with_rw():
    reg = ToolRegistry()
    reg.register(Tool(name="read_file", description="r",
                      parameters={"type": "object", "properties": {}, "required": []},
                      handler=lambda ctx: "", read_only=True, group="fs"))
    reg.register(Tool(name="write_file", description="w",
                      parameters={"type": "object", "properties": {}, "required": []},
                      handler=lambda ctx: "", read_only=False, group="fs"))
    reg.register(Tool(name="exit_plan_mode", description="e",
                      parameters={"type": "object", "properties": {}, "required": []},
                      handler=lambda ctx: "", read_only=True, group="session"))
    return reg


def _make_minimal_agent(reg, *, plan_mode=False, yolo=False):
    """构造足以跑完 agent.run 单轮的最小 Agent (不建真实内核/网络)。"""
    agent = Agent.__new__(Agent)
    agent.plan_mode = plan_mode
    agent.yolo = yolo
    agent._goal = None
    agent.exclude_tools = set()
    agent.workspace = "."
    agent.messages = []
    agent.turn_count = 0
    agent.pending_images = []
    agent._system_prompt = "sys"
    agent._cancel_event = __import__("threading").Event()
    agent._obs = SimpleNamespace(
        start_trace=lambda *a, **k: None,
        finish_span=lambda *a, **k: None,
        add_span_event=lambda *a, **k: None,
        start_span=lambda *a, **k: None,
    )
    agent._tool_executor = ToolExecutor(registry=reg, messages=agent.messages)
    agent._resilience = None
    agent.retry_policy = None
    agent._rate_limiter = None
    agent._route_session = None

    class _Cfg:
        def get(self, k, d=None):
            return {"skills.auto_inject": True,
                    "router.budget_limit": 0.0,
                    "agent.max_iterations": None}.get(k, d)
    agent.config = _Cfg()

    kernel = MagicMock()
    kernel.get = lambda k: None
    agent.kernel = kernel
    agent.registry = reg

    agent.context_manager = SimpleNamespace(
        needs_compact=lambda m: False,
        compact_if_needed=lambda m: (m, 0),
    )
    agent._session_append = lambda *a, **k: None
    agent._estimate_total_cost = lambda: 0.0
    agent._accumulate_usage = lambda *a, **k: None
    agent._notify_hook = lambda *a, **k: None
    agent._run_prompt_submit_hooks = lambda x: ""
    agent._maybe_route_model = lambda x: None
    agent._auto = SimpleNamespace(enabled=False, plan_execute=False, escalate=False)

    captured = {}

    class _Model:
        capabilities = SimpleNamespace(vision=False)
        name = "test"
        def chat(self, messages, tools=None, stream=False, on_token=None, on_reason=None):
            captured["tools_arg"] = tools
            res = SimpleNamespace(content="done", usage={}, tool_calls=None)
            return res
    agent.model = _Model()
    agent._captured = captured
    return agent


def test_main_call_uses_standard_tool_set():
    reg = _registry_with_rw()
    captured = {}
    orig = reg.schemas
    def spy(**kw):
        captured.update(kw)
        captured["result"] = orig(**kw)
        return captured["result"]
    reg.schemas = spy

    agent = _make_minimal_agent(reg, plan_mode=False, yolo=False)
    agent.run("hi", stream=False, max_iterations=1)

    assert captured.get("tool_set") == "standard", captured
    names = {s["function"]["name"] for s in captured["result"]}
    assert "write_file" in names and "read_file" in names, names


def test_main_call_uses_plan_tool_set_readonly_subset():
    reg = _registry_with_rw()
    captured = {}
    orig = reg.schemas
    def spy(**kw):
        captured.update(kw)
        captured["result"] = orig(**kw)
        return captured["result"]
    reg.schemas = spy

    agent = _make_minimal_agent(reg, plan_mode=True, yolo=False)
    agent.run("hi", stream=False, max_iterations=1)

    assert captured.get("tool_set") == "plan", captured
    names = {s["function"]["name"] for s in captured["result"]}
    assert "write_file" not in names, names          # 写操作被过滤
    assert "read_file" in names, names               # 只读保留
    assert "exit_plan_mode" in names, names           # 退出 plan 必须可达
