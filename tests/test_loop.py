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
