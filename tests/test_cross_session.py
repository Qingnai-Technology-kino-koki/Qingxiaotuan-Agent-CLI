"""跨会话引用 (@session / @#) 测试 —— 分叉会话 + 消息引用解析与注入。"""

from __future__ import annotations

from qingxiaotuan.core.cross_session import (
    CrossSessionResolver, Mention, resolve_mentions,
)
from qingxiaotuan.memory.sessions import SessionStore


def _seed_session(home, sid):
    """手工造一个会话文件 (供解析器引用)。"""
    store = SessionStore(home)
    store.session_id = sid
    store.file = store.dir / f"{sid}.jsonl"
    store.append("session.meta", {"task": "重构登录模块"})
    store.append("user", {"message": {"role": "user", "content": "帮我重构登录模块"}})
    store.append("assistant", {"message": {"role": "assistant",
                                           "content": "好的, 我先分析现有代码。"}})
    return store


# ---------------------------------------------------------------- 解析

def test_find_session_mention():
    text = "请参考 @session:20240101-ab12cd 的结论"
    res = CrossSessionResolver()
    ms = res.find_mentions(text)
    assert len(ms) == 1
    assert ms[0].kind == "session"
    assert ms[0].target == "20240101-ab12cd"


def test_find_message_mention():
    text = "把 @#3 的代码搬过来"
    res = CrossSessionResolver()
    ms = res.find_mentions(text)
    assert len(ms) == 1
    assert ms[0].kind == "message"
    assert ms[0].target == "3"


def test_ignore_plain_at():
    text = "联系 @zhang.san 和 foo@bar.com 确认"
    res = CrossSessionResolver()
    assert res.find_mentions(text) == []


def test_max_blocks():
    text = "@session:a @session:b @session:c @session:d @session:e "
    text += "@session:f @session:g @session:h"
    res = CrossSessionResolver()
    ms = res.find_mentions(text)
    assert len(ms) == 6  # 上限 6


# ---------------------------------------------------------------- 会话引用解析

def test_resolve_session_ok(tmp_path):
    _seed_session(tmp_path, "20240101-ab12cd")
    res = CrossSessionResolver(home=tmp_path)
    m = Mention(kind="session", target="20240101-ab12cd")
    res.resolve(m)
    assert m.resolved
    assert "重构登录模块" in m.summary   # 任务出现在注入内容里
    assert "好的, 我先分析现有代码" in m.summary


def test_resolve_session_missing(tmp_path):
    res = CrossSessionResolver(home=tmp_path)
    m = Mention(kind="session", target="nope-not-exist")
    res.resolve(m)
    assert not m.resolved
    assert "找不到会话" in m.error


def test_resolve_fork_lineage(tmp_path):
    parent = _seed_session(tmp_path, "20240101-parent00")
    # 从父会话分叉
    child = parent.fork()
    child_meta = child.read_meta(child.file) or {}
    assert child_meta.get("kind") == "fork"
    assert child_meta.get("parent") == "20240101-parent00"

    res = CrossSessionResolver(home=tmp_path)
    m = Mention(kind="session", target=child.session_id)
    res.resolve(m)
    assert m.resolved
    # 谱系信息被注入
    assert "分叉自" in m.summary or "谱系" in m.summary


# ---------------------------------------------------------------- 消息引用解析

def test_resolve_message_ok(tmp_path):
    store = _seed_session(tmp_path, "20240101-msg0000")
    store.append("assistant", {"message": {"role": "assistant",
                                           "content": "这是第 2 条助手消息"}})
    res = CrossSessionResolver(home=tmp_path, session_store=store)
    m = Mention(kind="message", target="3")
    res.resolve(m)
    assert m.resolved
    assert "[当前会话 assistant 第3条]" in m.summary
    assert "这是第 2 条助手消息" in m.summary


def test_resolve_message_out_of_range(tmp_path):
    store = _seed_session(tmp_path, "20240101-msg9999")
    res = CrossSessionResolver(home=tmp_path, session_store=store)
    m = Mention(kind="message", target="99")
    res.resolve(m)
    assert not m.resolved
    assert "没有第 99 条" in m.error


# ---------------------------------------------------------------- 注入

def test_inject_expands(tmp_path):
    _seed_session(tmp_path, "20240101-ab12cd")
    res = CrossSessionResolver(home=tmp_path)
    out = res.inject("请参考 @session:20240101-ab12cd")
    assert out != "请参考 @session:20240101-ab12cd"
    assert "引用的其它会话" in out
    assert "重构登录模块" in out


def test_inject_no_mention_unchanged(tmp_path):
    res = CrossSessionResolver(home=tmp_path)
    out = res.inject("普通问题, 无引用")
    assert out == "普通问题, 无引用"


def test_inject_error_appended(tmp_path):
    res = CrossSessionResolver(home=tmp_path)
    out = res.inject("参考 @session:ghost-99 来做")
    assert "引用解析告警" in out
    assert "找不到会话" in out


def test_inject_missing_store_message_mention(tmp_path):
    # 无 session_store 时 @# 应报错而不是崩
    res = CrossSessionResolver(home=tmp_path)
    out = res.inject("看 @#5")
    assert "引用解析告警" in out


def test_convenience_entry(tmp_path):
    _seed_session(tmp_path, "20240101-ab12cd")
    out = resolve_mentions("@session:20240101-ab12cd", home=tmp_path)
    assert "重构登录模块" in out


# ======================= 迭代 2: @session:#n / 谱系 / 目录 =======================

def test_find_session_msg_mention():
    text = "看 @session:abc123#2 的那段代码"
    res = CrossSessionResolver()
    ms = res.find_mentions(text)
    assert len(ms) == 1
    assert ms[0].kind == "session_msg"
    assert ms[0].target == "abc123"
    assert ms[0].index == "2"


def test_resolve_session_msg(tmp_path):
    _seed_session(tmp_path, "20240101-ab12cd")
    # 追加第 3 条消息 (seed 已含 user+assistant 共 2 条)
    store = SessionStore(tmp_path)
    store.session_id = "20240101-ab12cd"
    store.file = store.dir / "20240101-ab12cd.jsonl"
    store.append("assistant", {"message": {"role": "assistant",
                                           "content": "签名方案最终版"}})
    res = CrossSessionResolver(home=tmp_path)
    out = res.inject("@session:20240101-ab12cd#3")
    assert "会话消息" in out
    assert "签名方案最终版" in out


def test_resolve_session_msg_missing(tmp_path):
    res = CrossSessionResolver(home=tmp_path)
    out = res.inject("@session:ghost#2")
    assert "引用解析告警" in out
    assert "找不到会话" in out


def test_sessions_dir_required():
    res = CrossSessionResolver()
    raised = False
    try:
        res.list_sessions()
    except ValueError:
        raised = True
    assert raised


def test_list_sessions_and_lineage(tmp_path):
    parent = _seed_session(tmp_path, "20240101-root000")
    child1 = parent.fork(note="分支 A")
    child2 = parent.fork(note="分支 B")

    res = CrossSessionResolver(home=tmp_path)
    sessions = res.list_sessions()
    ids = {s["session_id"] for s in sessions}
    assert {parent.session_id, child1.session_id, child2.session_id} <= ids

    # lineage: child1 -> root
    ln = res.lineage(child1.session_id)
    assert ln[0]["session_id"] == parent.session_id      # 根祖先在前
    assert ln[-1]["session_id"] == child1.session_id
    assert any(s["session_id"] == parent.session_id for s in ln)

    # root 的后代 = 两个 child
    des = res.descendants(parent.session_id)
    des_ids = {d["session_id"] for d in des}
    assert child1.session_id in des_ids
    assert child2.session_id in des_ids


def test_list_sessions_title(tmp_path):
    _seed_session(tmp_path, "20240101-title00")
    res = CrossSessionResolver(home=tmp_path)
    sessions = res.list_sessions()
    s = next(x for x in sessions if x["session_id"] == "20240101-title00")
    assert "重构登录模块" in s["title"] or "重构登录模块" in s["task"]