"""可观测层测试: Trajectory 视图 + 每步"模型看到了什么" + 跨会话归因。"""
import pytest

from qingxiaotuan.arch.observability import (
    CrossSessionAttributor,
    StepViewer,
    TrajectoryStore,
)
from qingxiaotuan.arch.security import CryptoVault


def test_trajectory_store_and_tree():
    store = TrajectoryStore(session_id="s1")
    root = store.add_step("main", "decide", summary="plan")
    c1 = store.add_step("sub1", "emit", tool="search", parent_id=root.step_id)
    c2 = store.add_step("sub2", "emit", tool="read", parent_id=root.step_id)
    store.add_step("sub1", "observe", parent_id=c1.step_id, observation="ok")
    tree = store.render_tree()
    assert "Trajectory[s1]" in tree
    assert "search" in tree and "read" in tree
    assert len(store.steps()) == 4


def test_step_viewer_records_and_views_encrypted():
    vault = CryptoVault(raw_key=b"0" * 32)
    store = TrajectoryStore()
    step = store.add_step("main", "decide", summary="decide-step")
    viewer = StepViewer(vault=vault)
    viewer.record(step, model_input={"role": "user", "content": "SECRET-PROMPT"},
                  model_output={"content": "SECRET-RESPONSE"})
    # 存储的是密文, 不在 summary 明文里
    assert "SECRET-PROMPT" not in step.summary
    assert viewer.view_input(step) == '{"role": "user", "content": "SECRET-PROMPT"}'
    assert viewer.view_output(step) == '{"content": "SECRET-RESPONSE"}'


def test_step_viewer_plaintext_fallback():
    store = TrajectoryStore()
    step = store.add_step("main", "decide", summary="x")
    viewer = StepViewer()  # 无 vault -> 明文
    viewer.record(step, model_input="IN", model_output="OUT")
    assert "IN" in viewer.view_input(step)


def test_cross_session_attribution():
    store = TrajectoryStore(session_id="s1")
    root = store.add_step("main", "decide", summary="root")
    sub = store.add_step("workerA", "emit", tool="search", parent_id=root.step_id, observation="r")
    final = store.add_step("main", "terminate", parent_id=sub.step_id, summary="answer")

    attr = CrossSessionAttributor()
    attr.ingest(store)
    result = attr.attribute(final.step_id, store)
    # 回溯链: final -> sub -> root, 覆盖 s1 与 agent main/workerA
    assert any(a.session_id == "s1" for a in result)
    assert any(a.agent_id == "workerA" for a in result)
    # 权重和为 1
    assert abs(sum(a.weight for a in result) - 1.0) < 1e-6
    rendered = attr.render_attribution(final.step_id, store)
    assert "Attribution[" in rendered
