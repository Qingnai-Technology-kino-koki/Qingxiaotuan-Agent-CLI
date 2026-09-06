"""工具结果缓存回归: 验证缓存键纳入工作区, 防止跨工作区陈旧结果。"""
from qingxiaotuan.tools.cache import ToolResultCache


def test_same_workspace_cache_hit():
    c = ToolResultCache(ttl=60)
    assert c.get("read_file", '{"path":"a"}', workspace="/w") is None
    c.put("read_file", '{"path":"a"}', "content-of-a", workspace="/w")
    hit = c.get("read_file", '{"path":"a"}', workspace="/w")
    assert hit is not None and hit.endswith("content-of-a")


def test_different_workspace_not_served_stale():
    """同一参数在不同工作区必须视为不同键, 不返回上一工作区的陈旧结果。"""
    c = ToolResultCache(ttl=60)
    c.put("read_file", '{"path":"a"}', "content-in-w1", workspace="/w1")
    # 工作区不同 -> 未命中, 不会返回 w1 的内容
    assert c.get("read_file", '{"path":"a"}', workspace="/w2") is None
    # 参数顺序无关化仍应生效 (同一工作区内)
    c.put("read_file", '{"path":"b"}', "content-in-w2", workspace="/w2")
    hit2 = c.get("read_file", '{"b":"path"}'.replace('"b":"path"', '"path":"b"'), workspace="/w2")
    assert hit2 is not None and hit2.endswith("content-in-w2")


def test_dangerous_never_cached():
    c = ToolResultCache(ttl=60)
    c.put("run_shell", '{"command":"ls"}', "out", dangerous=True, workspace="/w")
    assert c.get("run_shell", '{"command":"ls"}', dangerous=True, workspace="/w") is None
