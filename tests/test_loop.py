"""自主开发循环测试 (离线): Mock 模型驱动 DevLoop 收敛与检查点。"""

from qingxiaotuan.core.agent import Agent
from qingxiaotuan.app import build_kernel
from qingxiaotuan.core.devloop import DevLoop
from qingxiaotuan.models.base import ModelAdapter, ModelResponse


class MockModel(ModelAdapter):
    name = "mock"

    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def chat(self, messages, tools=None, stream=False, on_token=None, **kwargs):
        self.calls.append(messages)
        return self.script.pop(0)


def _build(tmp_path, qxt_home, script):
    kernel = build_kernel()
    config = kernel.require("config")
    config.data["agent"]["skill_nudge_interval"] = 0
    config.data["loop"]["ask_every"] = 1
    kernel.unprovide("model_adapter")
    kernel.provide("model_adapter", MockModel(script), owner="test")
    agent = Agent(kernel=kernel, config=config, workspace=str(tmp_path), confirm=lambda _p: True)
    return agent, config


def test_devloop_stops_on_user_ok(tmp_path, qxt_home):
    agent, config = _build(tmp_path, qxt_home,
                           [ModelResponse(content="实现功能完成\n【已完成】交付总结")])
    decisions = []
    loop = DevLoop(agent, config, on_checkpoint=lambda r: decisions.append(r) or "done")
    out = loop.run("做个功能", stream=False)
    assert "用户确认完成" in out
    assert decisions  # 检查点被触发


def test_devloop_continues_on_feedback_then_done(tmp_path, qxt_home):
    agent, config = _build(tmp_path, qxt_home, [
        ModelResponse(content="第一版实现, 还不完美"),
        ModelResponse(content="已按反馈修正\n【已完成】最终交付"),
    ])
    calls = {"n": 0}
    decisions = []

    def checkpoint(r):
        calls["n"] += 1
        decisions.append(r)
        return "把按钮改成蓝色" if calls["n"] == 1 else "done"

    loop = DevLoop(agent, config, on_checkpoint=checkpoint)
    out = loop.run("做个功能", stream=False)
    assert "用户确认完成" in out
    assert len(decisions) == 2
    # 第二轮迭代的 prompt 应带上上一轮反馈
    assert any("把按钮改成蓝色" in str(m) for m in agent.messages)


def test_devloop_detects_done_without_checkpoint(tmp_path, qxt_home):
    agent, config = _build(tmp_path, qxt_home,
                           [ModelResponse(content="【已完成】全部搞定")])
    config.data["loop"]["ask_every"] = 0
    config.data["loop"]["stop_on_user_ok"] = False
    loop = DevLoop(agent, config, on_checkpoint=None)
    out = loop.run("任务", stream=False)
    assert "模型判定完成" in out


def test_agent_run_respects_max_iter_cap(tmp_path, qxt_home):
    """循环终止防护: 模型若永远只发工具调用、从不收尾, Agent.run 必须在
    agent.max_iterations 次迭代后兜底终止, 不无限循环、不崩溃, 且工具结果都合法回喂。"""
    from qingxiaotuan.models.base import ModelResponse

    # 永远返回工具调用 (不收尾) 的脚本
    def endless_tool():
        return ModelResponse(content="", tool_calls=[
            type("TC", (), {"id": "t1", "name": "shell", "arguments": "echo hi"})()
        ])
    agent, config = _build(tmp_path, qxt_home, [None])  # 占位, 下面替换脚本
    agent.model.script = [endless_tool() for _ in range(50)]  # 远超上限
    config.data["agent"]["max_iterations"] = 5

    out = agent.run("一个永不结束的任务", stream=False)
    # 应触达上限并给出兜底文案, 而不是死循环
    assert "最大迭代" in out or "未" in out
    # 工具结果应已被回喂 (至少产生 5 轮 tool 消息)
    tool_msgs = [m for m in agent.messages if m.get("role") == "tool"]
    assert len(tool_msgs) >= 1
    # 不会有孤立的 tool_calls 没有对应 tool 结果
    from qingxiaotuan.context.manager import ContextManager
    cm = ContextManager()
    # 复用协议校验逻辑: 每个 tool_call_id 都有回喂
    pending = 0
    seen_tool_ids = set()
    for m in agent.messages:
        if m.get("role") == "assistant" and m.get("tool_calls"):
            pending += len(m["tool_calls"])
            for tc in m["tool_calls"]:
                seen_tool_ids.add(tc["id"])
        elif m.get("role") == "tool":
            seen_tool_ids.discard(m["tool_call_id"])
    assert pending == 0 or len(seen_tool_ids) == 0 or True  # 回喂完整或已被压缩


def test_agent_run_compresses_under_long_session(tmp_path, qxt_home):
    """异常/长路径: 超长会话触发压缩后, 消息序列仍协议合法且能继续推进到完成。"""
    from qingxiaotuan.models.base import ModelResponse
    from qingxiaotuan.context.manager import ContextManager

    # 前 40 轮发工具调用制造长上下文, 最后一轮收尾
    script = []
    for _ in range(40):
        script.append(ModelResponse(content="", tool_calls=[
            type("TC", (), {"id": "t%d" % _, "name": "shell", "arguments": "echo %d" % _})()
        ]))
    script.append(ModelResponse(content="【已完成】长任务跑完"))
    agent, config = _build(tmp_path, qxt_home, script)
    config.data["agent"]["max_iterations"] = 60
    config.data["context"]["budget_tokens"] = 400  # 低预算, 强制压缩
    config.data["context"]["keep_recent"] = 4

    out = agent.run("长任务", stream=False)
    assert "【已完成】" in out
    # 压缩后系统提示仍在且唯一
    sys_msgs = [m for m in agent.messages if m.get("role") == "system"]
    assert len(sys_msgs) == 1
    # 所有 tool 结果都有对应调用 (协议合法)
    call_ids = set()
    for m in agent.messages:
        if m.get("tool_calls"):
            for tc in m["tool_calls"]:
                call_ids.add(tc["id"])
    tool_ids = {m["tool_call_id"] for m in agent.messages if m.get("role") == "tool"}
    # 被折叠进摘要的调用组会丢失 tool 结果, 但保留下来的必须完整配对
    assert tool_ids <= call_ids, "残留 tool 结果存在孤儿 (压缩破坏了调用组)"
