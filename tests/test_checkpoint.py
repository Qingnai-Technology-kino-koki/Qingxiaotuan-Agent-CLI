"""会话检查点 (checkpoint/rewind) 测试。

覆盖:
- MutationLedger.mark / undo_since: 只回滚标记之后的变更
- checkpoint 工具: save/list/restore 基本流 + 无检查点 + 未知 id
- 截断的协议合法边界 (结尾不悬着未回应的 tool_calls)
- 全循环集成: 写文件 -> save -> 再改 -> restore, 文件与对话同时回退
"""

import json

from qingxiaotuan.core.agent import Agent
from qingxiaotuan.core.ledger import MutationLedger
from qingxiaotuan.app import build_kernel
from qingxiaotuan.models.base import ModelAdapter, ModelResponse, ToolCall
from qingxiaotuan.tools.base import ToolContext
from qingxiaotuan.tools.checkpoint import checkpoint_tool


# ------------------------------------------------------------------ ledger 单元

def test_ledger_mark_and_undo_since(tmp_path):
    led = MutationLedger(str(tmp_path))
    f = tmp_path / "a.txt"
    f.write_text("v1", encoding="utf-8")
    mark = led.mark()
    # 模拟一次被账本记录的变更 (与 dispatcher 的 snapshot/record 流程一致)
    snaps = led.snapshot(["a.txt"])
    f.write_text("v2", encoding="utf-8")
    led.record("write_file", ["a.txt"], snaps)
    assert led.mark() == mark + 1
    undone = led.undo_since(mark)
    assert len(undone) == 1
    assert f.read_text(encoding="utf-8") == "v1"
    assert led.mark() == mark


# ------------------------------------------------------------------ handler 单元

def _ctx(**kw) -> ToolContext:
    return ToolContext(kernel=None, workspace=".", **kw)


def test_restore_without_checkpoints():
    out = checkpoint_tool(_ctx(), "restore")
    assert "没有可恢复" in out


def test_save_list_restore_basic():
    ctx = _ctx(conversation=[{"role": "system", "content": "s"},
                             {"role": "user", "content": "hi"}])
    out = checkpoint_tool(ctx, "save", note="改动前")
    assert "cp1" in out
    assert "没有已保存" not in checkpoint_tool(ctx, "list")
    # 对话继续增长后恢复到 cp1
    ctx.conversation.append({"role": "assistant", "content": "x"})
    out = checkpoint_tool(ctx, "restore", checkpoint_id="cp1")
    assert "cp1" in out
    assert len(ctx.conversation) == 2
    assert ctx.checkpoints == []  # 时间线重写, 检查点一并丢弃


def test_unknown_checkpoint_id():
    ctx = _ctx(conversation=[], checkpoints=[{"id": "cp9", "msg_len": 0, "mark": -1, "note": ""}])
    out = checkpoint_tool(ctx, "restore", checkpoint_id="nope")
    assert "未找到" in out
    assert "cp9" in out


def test_truncate_walks_back_over_dangling_tool_calls():
    # 截断点若落在带 tool_calls 的 assistant 之后, 必须回退到协议合法边界
    msgs = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "u1"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "c1"}]},
        {"role": "tool", "content": "r"},
    ]
    ctx = _ctx(conversation=msgs,
               checkpoints=[{"id": "cp1", "msg_len": 3, "mark": -1, "note": ""}])
    out = checkpoint_tool(ctx, "restore")
    assert "截回" in out
    assert len(msgs) == 2  # 从 3 回退到 2, 不悬着未回应的调用


# ------------------------------------------------------------------ 全循环集成

class MockModel(ModelAdapter):
    name = "mock"

    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def chat(self, messages, tools=None, stream=False, on_token=None, **kwargs):
        self.calls.append(messages)
        return self.script.pop(0)


def test_checkpoint_roundtrip_in_agent_loop(tmp_path, qxt_home):
    kernel = build_kernel()
    config = kernel.require("config")
    config.data["agent"]["skill_nudge_interval"] = 0
    kernel.unprovide("model_adapter")
    model = MockModel([
        # 回合1: 写初版
        ModelResponse(tool_calls=[ToolCall(id="c1", name="write_file", arguments=json.dumps(
            {"path": "doc.txt", "content": "v1"}))]),
        ModelResponse(content="初版写好"),
        # 回合2: 打检查点
        ModelResponse(tool_calls=[ToolCall(id="c2", name="checkpoint", arguments='{"action":"save","note":"改动前"}')]),
        ModelResponse(content="已保存"),
        # 回合3: 改坏它 + 新增文件
        ModelResponse(tool_calls=[ToolCall(id="c3", name="write_file", arguments=json.dumps(
            {"path": "doc.txt", "content": "v2-broken"})),
            ToolCall(id="c4", name="write_file", arguments=json.dumps(
                {"path": "extra.txt", "content": "junk"}))]),
        ModelResponse(content="改完了"),
        # 回合4: 一键回滚
        ModelResponse(tool_calls=[ToolCall(id="c5", name="checkpoint", arguments='{"action":"restore"}')]),
        ModelResponse(content="已回滚"),
    ])
    kernel.provide("model_adapter", model, owner="test")
    agent = Agent(kernel=kernel, config=config, workspace=str(tmp_path),
                  confirm=lambda _p: True)
    agent.ctx.ledger = MutationLedger(str(tmp_path), config)

    agent.run("写初版", stream=False)
    agent.run("打检查点", stream=False)
    agent.run("改坏它", stream=False)
    assert (tmp_path / "doc.txt").read_text(encoding="utf-8") == "v2-broken"

    answer = agent.run("回滚", stream=False)
    assert answer == "已回滚"
    # 文件系统回到检查点状态
    assert (tmp_path / "doc.txt").read_text(encoding="utf-8") == "v1"
    assert not (tmp_path / "extra.txt").exists()
    # 对话流截回检查点: 改坏回合与回滚指令本身都从历史中消失
    # (restore 在工具执行期截断, 本回合收尾的 tool 结果与最终回复仍会正常追加)
    user_texts = [m.get("content", "") for m in agent.messages if m.get("role") == "user"]
    assert user_texts == ["写初版", "打检查点"]
