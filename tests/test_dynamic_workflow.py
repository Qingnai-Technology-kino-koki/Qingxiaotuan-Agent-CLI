"""Dynamic Workflows 并行后台代理编排测试 —— 时序编排 + 动态加步骤 + 持久化。"""

from __future__ import annotations

import time

import pytest

from qingxiaotuan.core.dynamic_workflow import DynamicWorkflowEngine
from qingxiaotuan.core.subagents import SubResult, SubTask


class FakePool:
    """注入的假子代理池: 记录被派发的任务, 返回可控结果。"""

    def __init__(self, worker):
        self.worker = worker  # callable(task) -> (ok, output, error)

    def dispatch(self, tasks, stream=False, **kw):
        out = []
        for t in tasks:
            ok, output, error = self.worker(t)
            out.append(SubResult(task_id=t.task_id, prompt=t.prompt, ok=ok,
                                 output=output, error=error, turns=1, elapsed=0.01))
        return out


def _always_ok(t):
    return True, "完成: " + t.task_id, None


def _always_fail(t):
    return False, "", "模拟失败"


def _wait_status(engine, wf_id, statuses, timeout=5.0):
    """等待工作流进入 statuses 中的任一状态。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        st = engine.status(wf_id)["status"]
        if st in statuses:
            return st
        time.sleep(0.02)
    return engine.status(wf_id)["status"]


# ---------------------------------------------------------------- 创建与编排

def test_create_returns_immediately(tmp_path):
    eng = DynamicWorkflowEngine(tmp_path, pool_factory=lambda e: FakePool(_always_ok))
    wf_id = eng.create("调研", [[{"title": "A", "prompt": "a"}]])
    assert wf_id.startswith("wf-")
    # 立即返回 (异步执行)
    assert eng.status(wf_id)["workflow_id"] == wf_id


def test_two_steps_run_sequentially_and_done(tmp_path):
    eng = DynamicWorkflowEngine(tmp_path, pool_factory=lambda e: FakePool(_always_ok))
    wf_id = eng.create("两步", [
        [{"title": "任务1", "prompt": "p1"}],
        [{"title": "任务2", "prompt": "p2"}],
    ])
    assert _wait_status(eng, wf_id, {"done"}) == "done"
    st = eng.status(wf_id)
    assert st["n_steps"] == 2
    assert all(s["status"] == "done" for s in st["steps"])
    res = eng.result(wf_id)
    assert res["ok_tasks"] == 2
    assert "任务1" in res["summary"] and "任务2" in res["summary"]


def test_parallel_step_dispatches_all_tasks(tmp_path):
    seen = []

    def pool_factory(eng):
        def _worker(t):
            seen.append(t.task_id)
            return _always_ok(t)
        return FakePool(_worker)

    eng = DynamicWorkflowEngine(tmp_path, pool_factory=pool_factory)
    wf_id = eng.create("并行", [[{"title": "a", "prompt": "p"},
                                 {"title": "b", "prompt": "p"},
                                 {"title": "c", "prompt": "p"}]])
    assert _wait_status(eng, wf_id, {"done"}) == "done"
    assert eng.result(wf_id)["ok_tasks"] == 3
    assert len(seen) == 3


def test_step_failure_marks_workflow_failed(tmp_path):
    eng = DynamicWorkflowEngine(tmp_path, pool_factory=lambda e: FakePool(_always_fail))
    wf_id = eng.create("会失败", [[{"title": "x", "prompt": "p"}]])
    assert _wait_status(eng, wf_id, {"failed"}) == "failed"
    res = eng.result(wf_id)
    assert res["ok_tasks"] == 0
    assert "模拟失败" in res["summary"]


def test_one_failure_isolated_from_siblings(tmp_path):
    fail_t2 = {"n": 1}  # T2 只失败一次, 重试后成功

    def worker(t):
        if t.task_id.endswith("T2") and fail_t2["n"] > 0:
            fail_t2["n"] -= 1
            return False, "", "只有这个失败"
        return _always_ok(t)

    eng = DynamicWorkflowEngine(tmp_path, pool_factory=lambda e: FakePool(worker))
    wf_id = eng.create("隔离", [[{"title": "a", "prompt": "p"},
                                 {"title": "b", "prompt": "p"},
                                 {"title": "c", "prompt": "p"}]])
    assert _wait_status(eng, wf_id, {"failed"}) == "failed"
    res = eng.result(wf_id)
    assert res["ok_tasks"] == 2  # 两个成功仍在

    # 重试失败任务后全部成功 (worker 这次全成功)
    assert eng.retry_failed(wf_id)
    assert _wait_status(eng, wf_id, {"done"}) == "done"
    assert eng.result(wf_id)["ok_tasks"] == 3


# ---------------------------------------------------------------- 动态加步骤

def test_add_step_during_running(tmp_path):
    entered = []

    class SlowPool(FakePool):
        def dispatch(self, tasks, **kw):
            entered.append(True)
            time.sleep(0.15)  # 放慢首步, 给 add_step 插入窗口
            return super().dispatch(tasks, **kw)

    eng = DynamicWorkflowEngine(tmp_path, pool_factory=lambda e: SlowPool(_always_ok))
    wf_id = eng.create("动态", [[{"title": "初始", "prompt": "p"}]])
    time.sleep(0.02)
    assert eng.add_step(wf_id, [{"title": "追加", "prompt": "p2"}]) == 1
    assert _wait_status(eng, wf_id, {"done"}) == "done"
    st = eng.status(wf_id)
    assert st["n_steps"] == 2
    assert all(s["status"] == "done" for s in st["steps"])
    res = eng.result(wf_id)
    assert "初始" in res["summary"] and "追加" in res["summary"]


def test_add_step_task_ids_unique_across_rapid_adds(tmp_path):
    eng = DynamicWorkflowEngine(tmp_path, pool_factory=lambda e: FakePool(_always_ok))
    wf_id = eng.create("碰撞", [[{"title": "a", "prompt": "p"}]])
    seen: set = set()
    # 同一瞬时连续多次追加, task_id 不能碰撞 (曾用 int(time.time()) 前缀, 同秒必撞)
    for _ in range(3):
        eng._read(wf_id)["status"] = "running"  # 让 add_step 通过结束判定
        eng.add_step(wf_id, [{"title": "t", "prompt": "p"}, {"title": "u", "prompt": "q"}])
    for st in eng._read(wf_id)["steps"]:
        for t in st["tasks"]:
            assert t["task_id"] not in seen, f"task_id 碰撞: {t['task_id']}"
            seen.add(t["task_id"])


def test_add_step_after_done_raises(tmp_path):
    eng = DynamicWorkflowEngine(tmp_path, pool_factory=lambda e: FakePool(_always_ok))
    wf_id = eng.create("已结束", [[{"title": "a", "prompt": "p"}]])
    assert _wait_status(eng, wf_id, {"done"}) == "done"
    with pytest.raises(RuntimeError):
        eng.add_step(wf_id, [{"title": "晚了", "prompt": "p"}])


# ---------------------------------------------------------------- 状态 / 持久化

def test_persisted_across_engine_instances(tmp_path):
    eng1 = DynamicWorkflowEngine(tmp_path, pool_factory=lambda e: FakePool(_always_ok))
    wf_id = eng1.create("持久", [[{"title": "a", "prompt": "p"}]])
    assert _wait_status(eng1, wf_id, {"done"}) == "done"

    # 新的引擎实例 (模拟进程重启) 也能读到落盘状态
    eng2 = DynamicWorkflowEngine(tmp_path)
    st = eng2.status(wf_id)
    assert st["status"] == "done"
    assert st["name"] == "持久"


def test_list_workflows(tmp_path):
    eng = DynamicWorkflowEngine(tmp_path, pool_factory=lambda e: FakePool(_always_ok))
    eng.create("W1", [[{"title": "a", "prompt": "p"}]])
    eng.create("W2", [[{"title": "b", "prompt": "p"}]])
    # 落盘异步 (Windows 目录写/读竞态偶现): 轮询直到两条都在, 避免 flaky
    deadline = time.time() + 3
    names: set = set()
    while time.time() < deadline:
        items = eng.list()
        names = {x["name"] for x in items}
        if {"W1", "W2"} <= names:
            break
        time.sleep(0.02)
    assert {"W1", "W2"} <= names, f"应能列出 W1、W2, 实际 {names}"


def test_cancel_stops(tmp_path):
    class BlockingPool(FakePool):
        def dispatch(self, tasks, **kw):
            time.sleep(5)
            return super().dispatch(tasks, **kw)

    eng = DynamicWorkflowEngine(tmp_path, pool_factory=lambda e: BlockingPool(_always_ok))
    wf_id = eng.create("取消", [[{"title": "卡住", "prompt": "p"}]])
    time.sleep(0.02)
    assert eng.cancel(wf_id) is True
    _wait_status(eng, wf_id, {"cancelled", "pending"})


def test_empty_steps_finishes_done(tmp_path):
    eng = DynamicWorkflowEngine(tmp_path, pool_factory=lambda e: FakePool(_always_ok))
    wf_id = eng.create("空", [])
    assert _wait_status(eng, wf_id, {"done"}) == "done"


def test_no_pool_factory_raises_on_execute(tmp_path):
    eng = DynamicWorkflowEngine(tmp_path)  # 无 pool_factory
    wf_id = eng.create("无池", [[{"title": "a", "prompt": "p"}]])
    # 池初始化失败 → 整步 failed
    assert _wait_status(eng, wf_id, {"failed"}) == "failed"


# ---------------------------------------------------------------- 第二轮迭代: 串行 / 并发分批 / 运行期异常

def test_parallel_false_runs_tasks_serially(tmp_path):
    inflight = {"n": 0, "max": 0}

    class SerialPool(FakePool):
        def dispatch(self, tasks, **kw):
            inflight["n"] += 1
            inflight["max"] = max(inflight["max"], inflight["n"])
            time.sleep(0.02)
            inflight["n"] -= 1
            assert len(tasks) == 1  # 串行模式下每次只派一个
            return super().dispatch(tasks, **kw)

    eng = DynamicWorkflowEngine(tmp_path, pool_factory=lambda e: SerialPool(_always_ok))
    wf_id = eng.create("串行", [
        [{"title": "d", "prompt": "p"}, {"title": "e", "prompt": "p"},
         {"title": "f", "prompt": "p"}],
    ], parallel=False)
    assert _wait_status(eng, wf_id, {"done"}) == "done"
    # 串行: 并发度恒为 1
    assert inflight["max"] == 1
    assert eng.result(wf_id)["ok_tasks"] == 3


def test_parallel_batches_to_max_parallel(tmp_path):
    inflight = {"n": 0, "max": 0}

    class BatchPool(FakePool):
        def dispatch(self, tasks, **kw):
            inflight["n"] += len(tasks)
            inflight["max"] = max(inflight["max"], len(tasks))
            time.sleep(0.01)
            inflight["n"] -= len(tasks)
            return super().dispatch(tasks, **kw)

    eng = DynamicWorkflowEngine(tmp_path, pool_factory=lambda e: BatchPool(_always_ok))
    eng.max_parallel = 2
    tasks = [{"title": f"k{i}", "prompt": "p"} for i in range(7)]
    wf_id = eng.create("分批", [tasks])
    assert _wait_status(eng, wf_id, {"done"}) == "done"
    assert inflight["max"] <= 2  # 单批每次都 <= max_parallel
    assert eng.result(wf_id)["ok_tasks"] == 7


def test_runtime_pool_exception_marks_tasks_failed(tmp_path):
    class BoomPool(FakePool):
        def dispatch(self, tasks, **kw):
            for t in tasks:
                if t.task_id.endswith("T1"):
                    raise RuntimeError("池子炸了")
            return super().dispatch(tasks, **kw)

    eng = DynamicWorkflowEngine(tmp_path, pool_factory=lambda e: BoomPool(_always_ok))
    wf_id = eng.create("炸", [[{"title": "x", "prompt": "p"},
                               {"title": "y", "prompt": "p"}]])
    _wait_status(eng, wf_id, {"failed", "done"})
    res = eng.result(wf_id)
    assert "执行中断" in res["summary"] or "池子炸了" in res["summary"]


# ---------------------------------------------------------------- 第三轮迭代: 崩溃续跑 (fx4)

def _make_running_workflow(store, wf_id, step_status, task_states, name="崩溃遗留"):
    """直接写一个卡在 running 的工作流 JSON, 模拟崩溃瞬间的磁盘状态。"""
    steps = [{
        "step_idx": 0,
        "status": step_status,
        "tasks": [
            {"task_id": tid, "title": tid, "prompt": "p", "role": "general-purpose",
             "status": st, "result": "旧结果" if st == "done" else "", "error": ""}
            for tid, st in task_states
        ],
    }]
    store.write({
        "workflow_id": wf_id, "name": name, "created_at": time.time() - 10,
        "updated_at": time.time(), "note": "", "parallel": True,
        "status": "running", "steps": steps,
    })


def test_resume_all_recycles_running_step_and_reruns(tmp_path):
    """崩溃后 running 步骤被回收为 pending, resume 后重跑至 done。"""
    eng = DynamicWorkflowEngine(tmp_path, pool_factory=lambda e: FakePool(_always_ok))
    wf_id = "wf-crash1"
    _make_running_workflow(eng.store, wf_id, "running", [("S1T1", "running")])

    resumed = eng.resume_all()
    assert wf_id in resumed
    # running 任务被复位成 pending, 步骤回到 pending
    rec = eng.store.get(wf_id)
    assert rec["steps"][0]["status"] == "pending"
    assert rec["steps"][0]["tasks"][0]["status"] == "pending"

    assert _wait_status(eng, wf_id, {"done"}) == "done"
    assert eng.result(wf_id)["ok_tasks"] == 1


def test_resume_keeps_done_tasks_and_only_reruns_stuck(tmp_path):
    """running 步骤里已完成的任务不重复跑, 只补跑崩溃时卡住的任务。"""
    dispatched = []

    def worker(t):
        dispatched.append(t.task_id)
        return _always_ok(t)

    eng = DynamicWorkflowEngine(tmp_path, pool_factory=lambda e: FakePool(worker))
    wf_id = "wf-crash2"
    # S1T1 已 done; S1T2 崩溃时卡在 running
    _make_running_workflow(eng.store, wf_id, "running",
                           [("S1T1", "done"), ("S1T2", "running")])

    eng.resume_all()
    assert _wait_status(eng, wf_id, {"done"}) == "done"

    # 不重跑已完成任务, 只补跑卡住的那个
    assert dispatched == ["S1T2"], f"应只补跑 S1T2, 实际派发 {dispatched}"
    res = eng.result(wf_id)
    assert res["ok_tasks"] == 2
    assert "S1T1" in res["summary"] and "S1T2" in res["summary"]


def test_resume_ignores_terminated_workflows(tmp_path):
    eng = DynamicWorkflowEngine(tmp_path, pool_factory=lambda e: FakePool(_always_ok))
    wf_id = eng.create("已完成", [[{"title": "a", "prompt": "p"}]])
    assert _wait_status(eng, wf_id, {"done"}) == "done"
    # done 工作流不应被 resume 重新拉起
    assert eng.resume_all() == []
    st = eng.status(wf_id)
    assert st["status"] == "done"


def test_get_engine_auto_resumes_pending_workflow(tmp_path):
    """进程重启后 get_engine 自动拉起未结束的工作流 (无池时走到 failed 而非卡死)。"""
    from qingxiaotuan.core.dynamic_workflow import get_engine, close_engines
    close_engines()
    wf_id = "wf-auto"
    # 先写一个 running 工作流到磁盘 (无 pool → 引擎仍会自动续跑, 池初始化失败应落 failed)
    pre = DynamicWorkflowEngine(tmp_path)
    _make_running_workflow(pre.store, wf_id, "running", [("S1T1", "running")])

    eng = get_engine(tmp_path, auto_resume=True)  # 模拟重启后的首个 get
    # 回收后 kick, 无 pool_factory → 池初始化失败 → failed (证明不再是 running 卡死)
    deadline = time.time() + 5
    st = "running"
    while time.time() < deadline:
        st = eng.status(wf_id)["status"]
        if st in ("failed", "done"):
            break
        time.sleep(0.02)
    assert st in ("failed", "done")
    # 步骤已从 running 回收为 pending/failed
    step_st = eng.store.get(wf_id)["steps"][0]["status"]
    assert step_st != "running"