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
