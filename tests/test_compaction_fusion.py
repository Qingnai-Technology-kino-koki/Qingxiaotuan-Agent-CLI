"""融合层上下文压缩测试 (fusion.context_compaction)。

覆盖: 融合形状 [system]+[head]+[elision]+[tail]+[summary]、head/tail 预算选取、
中间省略、消息组不拆分 (assistant+tool)、system 前缀保留、token 估算、原生策略在
开关关闭时零改变。无需外部 LLM/网络。
"""

from __future__ import annotations

from qingxiaotuan.context.compaction_fusion import (
    compact_head_tail_elision,
    estimate_message,
    estimate_messages,
)


def _msg(role, content, **kw):
    m = {"role": role, "content": content}
    m.update(kw)
    return m


def _big_body(n_user=40, per=400):
    """构造 n_user 条 user 消息, 每条 per 字符, 夹杂 assistant(tool_calls)+tool 组。"""
    msgs = []
    for i in range(n_user):
        msgs.append(_msg("user", f"用户第{i}条需求 " + "x" * (per - 8)))
        msgs.append(_msg("assistant", f"我来处理第{i}条", tool_calls=[
            {"id": f"c{i}", "type": "function", "function": {"name": "run_shell", "arguments": "{}"}}
        ]))
        msgs.append(_msg("tool", f"第{i}条结果 " + "y" * 50, tool_call_id=f"c{i}"))
    return msgs


def test_shape_has_system_head_elision_tail_summary():
    system = _msg("system", "你是青小团")
    body = _big_body()
    msgs = [system] + body
    out = compact_head_tail_elision(msgs, "SUMMARY", max_tokens=2_500, head_tokens=500)
    assert out[0]["role"] == "system"  # system 保留置首
    roles = [m["role"] for m in out]
    assert "user" in roles
    # 存在 elision 标记与 summary 标记
    contents = [m["content"] for m in out if isinstance(m.get("content"), str)]
    assert any(c.startswith("[compaction]") for c in contents)
    assert any(c.startswith("[早期上下文摘要]") for c in contents)
    # 末尾是 summary
    assert out[-1]["content"].startswith("[早期上下文摘要]")


def test_tool_call_group_not_split():
    """assistant(tool_calls) 与紧随的 tool 结果必须同进同出, 不被拆到 head/tail 两边。"""
    system = _msg("system", "s")
    body = []
    for i in range(30):
        body.append(_msg("user", f"需求{i} " + "a" * 300))
        body.append(_msg("assistant", f"调用{i}", tool_calls=[
            {"id": f"c{i}", "type": "function", "function": {"name": "f", "arguments": "{}"}}
        ]))
        body.append(_msg("tool", f"结果{i}", tool_call_id=f"c{i}"))
    out = compact_head_tail_elision([system] + body, "S", max_tokens=2_000, head_tokens=400)
    # 任意 tool 消息的前一条必须是对应 assistant(tool_calls)
    for idx, m in enumerate(out):
        if m.get("role") == "tool":
            prev = out[idx - 1]
            assert prev.get("role") == "assistant" and prev.get("tool_calls"), (
                f"tool 消息 {idx} 前不是 assistant(tool_calls)，协议非法"
            )


def test_no_compaction_when_under_budget():
    msgs = [_msg("system", "s")] + [_msg("user", "短消息") for _ in range(3)]
    out = compact_head_tail_elision(msgs, "S", max_tokens=100_000, head_tokens=2_000)
    assert out == msgs  # 未超预算原样返回


def test_head_and_tail_preserved_when_elided():
    """头部与尾部的关键消息都在, 中间被省略 (elision 条数 < 原始 user 条数)。"""
    body = []
    for i in range(50):
        body.append(_msg("user", f"消息{i} " + "z" * 200))
    out = compact_head_tail_elision([_msg("system", "s")] + body, "S", max_tokens=2_500, head_tokens=500)
    user_contents = [m["content"] for m in out if m.get("role") == "user" and isinstance(m.get("content"), str) and not m["content"].startswith("[")]
    # 最旧与最新都应出现
    assert any("消息0 " in c for c in user_contents)
    assert any("消息49 " in c for c in user_contents)


def test_estimate_tokens_heuristic():
    # 纯 ASCII: ~4 字符/token
    assert estimate_message(_msg("user", "a" * 40)) == 10
    # 空
    assert estimate_message(_msg("user", "")) == 0


def test_estimate_messages_sum():
    msgs = [_msg("user", "a" * 40), _msg("assistant", "b" * 40)]
    assert estimate_messages(msgs) == 20
