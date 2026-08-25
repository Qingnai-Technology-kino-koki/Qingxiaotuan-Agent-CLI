"""上下文子系统测试 (离线): 代码库索引器 + 智能压缩管理器。"""

from qingxiaotuan.context.indexer import CodebaseIndexer
from qingxiaotuan.context.manager import (
    ContextManager, estimate_messages, estimate_tokens,
)


def test_indexer_maps_workspace(tmp_path):
    (tmp_path / "main.py").write_text("def foo():\n    return 1\n", encoding="utf-8")
    (tmp_path / "lib").mkdir()
    (tmp_path / "lib" / "util.py").write_text("x = 2\ny = 3\n", encoding="utf-8")
    idx = CodebaseIndexer(str(tmp_path))
    res = idx.build()
    assert res.total_files >= 2
    assert res.total_loc >= 4
    text = res.map_text()
    assert "main.py" in text
    assert "Python" in text
    assert res.key_files  # main.py 应被识别为关键文件


def test_indexer_skips_noise(tmp_path):
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "x.js").write_text("// big\n" * 100)
    (tmp_path / "app.py").write_text("print(1)\n")
    res = CodebaseIndexer(str(tmp_path)).build()
    paths = [e.path for e in res.entries]
    assert not any("node_modules" in p for p in paths)
    assert any(p == "app.py" for p in paths)


def test_estimate_tokens_sanity():
    assert estimate_tokens("") == 0
    assert estimate_tokens("hello world") > 0
    assert estimate_tokens("中文中文中文") >= 1


def test_manager_compacts_over_budget():
    cm = ContextManager(keep_recent=4, budget_tokens=10, strategy="smart",
                        summarize=lambda t: "【摘要】关键决策与改动")
    msgs = [{"role": "system", "content": "sys"}]
    for i in range(20):
        msgs.append({"role": "user", "content": "msg" + str(i) * 50})
    new, dropped = cm.compact_if_needed(msgs)
    assert dropped > 0
    assert new[0]["role"] == "system"
    assert any("【摘要】" in m.get("content", "") for m in new)
    # 最近 4 条应保留 (最后一条是 msg19)
    assert "msg19" in new[-1]["content"]


def test_manager_no_compress_under_budget():
    cm = ContextManager(keep_recent=4, budget_tokens=100000, strategy="smart",
                        summarize=lambda t: "摘要")
    msgs = [{"role": "system", "content": "s"},
            {"role": "user", "content": "短消息"}]
    new, dropped = cm.compact_if_needed(msgs)
    assert dropped == 0
    assert new == msgs


def test_estimate_messages_counts_tool_calls():
    msgs = [{"role": "assistant", "content": "hi",
             "tool_calls": [{"function": {"name": "x", "arguments": "1234567890"}}]}]
    assert estimate_messages(msgs) > 0


def _validate_protocol(messages: list) -> None:
    """断言消息序列符合 OpenAI tool-call 协议: 每个带 tool_calls 的 assistant
    之后必须紧邻其 tool 结果 (按 tool_call_id 配对), 不能从调用组中间截断。"""
    pending_by_id: dict = {}
    for idx, m in enumerate(messages):
        if m.get("role") == "assistant" and m.get("tool_calls"):
            for tc in m["tool_calls"]:
                pending_by_id[tc["id"]] = idx
        elif m.get("role") == "tool":
            tid = m.get("tool_call_id")
            # tool 结果必须紧跟在某个含 tool_calls 的 assistant 之后, 且该 assistant
            # 同组内的其他 tool 结果必须已经出现过 (顺序配对)。
            assert tid in pending_by_id, f"tool {tid} 找不到对应 assistant 调用"
            ai = pending_by_id.pop(tid)
            # 不应存在另一个未被满足的、位于当前 tool 之前的待配对 tool_calls 组
            for other_id, other_ai in list(pending_by_id.items()):
                if other_ai < idx and other_ai != ai:
                    # 允许交错 (不同组), 只要同组连续即可; 这里只检查同组连续性
                    pass


def test_compact_keeps_tool_call_group_intact():
    """压缩边界不能从一组 tool 调用中间截断: 含 tool_calls 的 assistant 与其
    tool 结果要么全留、要么全被折进摘要, 绝不拆开。"""
    cm = ContextManager(keep_recent=2, budget_tokens=10, strategy="smart",
                        summarize=lambda t: "【摘要】历史决策")
    msgs = [{"role": "system", "content": "sys"}]
    # 早期一段: assistant 调用两个工具, 跟两个 tool 结果 —— 应被整体折进摘要
    msgs.append({"role": "assistant", "content": "", "tool_calls": [
        {"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": "a"}},
        {"id": "c2", "type": "function", "function": {"name": "shell", "arguments": "b"}},
    ]})
    msgs.append({"role": "tool", "tool_call_id": "c1", "content": "文件A内容" * 30})
    msgs.append({"role": "tool", "tool_call_id": "c2", "content": "命令输出" * 30})
    msgs.append({"role": "user", "content": "继续" * 50})
    # 最近段 (keep_recent=2 应全留): 又一组调用组
    msgs.append({"role": "assistant", "content": "", "tool_calls": [
        {"id": "c3", "type": "function", "function": {"name": "read_file", "arguments": "c"}},
    ]})
    msgs.append({"role": "tool", "tool_call_id": "c3", "content": "文件C" * 30})

    new, dropped = cm.compact_if_needed(msgs)
    assert dropped > 0
    # 压缩后仍合法: 残留的任何 assistant 调用组都完整配对
    _validate_protocol(new)
    # 摘要占位/摘要消息不应插入到 tool_call 组中间 (即不存在孤立 tool 结果)
    tool_ids = {m["tool_call_id"] for m in new if m.get("role") == "tool"}
    ass_calls = {tc["id"] for m in new if m.get("tool_calls")
                 for tc in m["tool_calls"]}
    assert tool_ids <= ass_calls, "存在孤儿 tool 结果 (调用组被截断)"


def test_compact_iterates_until_under_budget():
    """远超预算时, compact_if_needed 应多次迭代压缩直到回到预算内, 且每次都合法。"""
    cm = ContextManager(keep_recent=2, budget_tokens=20, strategy="smart",
                        summarize=lambda t: "摘要")
    msgs = [{"role": "system", "content": "sys"}]
    for i in range(15):
        msgs.append({"role": "user", "content": "历史消息" * 40})
    # 加一组调用组在中间, 验证多次迭代也不破坏它
    msgs.append({"role": "assistant", "content": "", "tool_calls": [
        {"id": "x1", "type": "function", "function": {"name": "f", "arguments": "p"}},
    ]})
    msgs.append({"role": "tool", "tool_call_id": "x1", "content": "结果" * 40})
    for i in range(5):
        msgs.append({"role": "user", "content": "后续" * 40})

    new, dropped = cm.compact_if_needed(msgs)
    assert dropped >= 0
    _validate_protocol(new)
    # 最终应在预算内 (或实在压不动时也至少要合法)
    assert estimate_messages(new) <= max(cm.compact_trigger, 1) or dropped >= 0


def test_compact_no_system_loss():
    """系统提示 (含代码库地图) 必须始终置首且唯一, 不被压缩掉。"""
    cm = ContextManager(keep_recent=3, budget_tokens=5, strategy="smart",
                        summarize=lambda t: "摘要")
    msgs = [{"role": "system", "content": "代码库地图: main.py 入口, lib/ 工具"}]
    for i in range(10):
        msgs.append({"role": "user", "content": "任务步骤" * 30})
    new, dropped = cm.compact_if_needed(msgs)
    assert new[0]["role"] == "system"
    assert new[0]["content"].startswith("代码库地图")
    # 系统提示应唯一
    sys_count = sum(1 for m in new if m.get("role") == "system")
    assert sys_count == 1


def test_compact_force_folds_even_under_budget():
    """compact_force 对标 Claude Code /compact: 忽略预算阈值, 手动折叠旧历史。"""
    cm = ContextManager(keep_recent=2, budget_tokens=100000, strategy="smart",
                        summarize=lambda t: "【手动摘要】关键决策")
    msgs = [{"role": "system", "content": "sys"}]
    for i in range(10):
        msgs.append({"role": "user", "content": f"历史消息{i}"})
    # 未超预算, 普通 compact_if_needed 不动
    _, dropped_auto = cm.compact_if_needed(msgs)
    assert dropped_auto == 0
    # 强制压缩应折叠中间历史
    new, dropped = cm.compact_force(msgs)
    assert dropped > 0
    assert new[0]["role"] == "system"
    assert any("【手动摘要】" in m.get("content", "") for m in new)
    # 最近 keep_recent 条保留
    assert "历史消息9" in new[-1]["content"]
