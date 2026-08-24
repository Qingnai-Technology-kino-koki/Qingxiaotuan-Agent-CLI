"""CronRunner: 任务执行 + 异常隔离 + 结果落会话流 (离线, 不跑真实模型)。"""
import json
from pathlib import Path

from qingxiaotuan.cron.runner import CronRunner, run_due_jobs
from qingxiaotuan.cron.store import CronStore
from qingxiaotuan.memory.sessions import SessionStore


class _FakeAgent:
    def __init__(self, out="cron 产出"):
        self.out = out

    def run(self, prompt, stream=False):
        return self.out


class _FakeKernel:
    def __init__(self, store, agent_out):
        self._store = store
        self._agent_out = agent_out
        self._agent = None

    def get(self, name):
        if name == "cron_store":
            return self._store
        if name == "model_adapter":
            return object()
        return None

    def require(self, name):
        return self.get(name)

    def register(self, *a, **k):
        pass

    def activate_all(self):
        pass


def _make(tmp_path, agent_out="周期性报告"):
    store = CronStore(tmp_path)
    job = store.add("日报", "写今日日报", interval_minutes=60)
    job["last_run"] = 0  # 确保到期
    # 重写 last_run 到磁盘
    jobs = store._read()
    for j in jobs:
        if j["id"] == job["id"]:
            j["last_run"] = 0
    store._write(jobs)

    kernel = _FakeKernel(store, agent_out)

    # 用 monkeypatch 替换 Agent 构造
    import qingxiaotuan.cron.runner as runner_mod
    runner_mod.Agent = lambda *a, **k: _FakeAgent(agent_out)
    return store, job, kernel


def test_run_job_writes_session_and_marks_run(tmp_path):
    import qingxiaotuan.cron.runner as runner_mod
    store, job, kernel = _make(tmp_path, "今日完成 A/B/C")
    runner = CronRunner(kernel, config=None, workspace=str(tmp_path), home=tmp_path)
    res = runner.run_job(job, stream=False)
    assert res["ok"] is True
    assert "今日完成" in res["output"]
    # 结果落进会话流
    sess = SessionStore(tmp_path)
    files = sess.list_sessions()
    assert files, "应生成 cron 会话流"
    content = files[0].read_text(encoding="utf-8")
    assert "今日完成" in content
    assert job["id"] in content
    # mark_run 已写回
    assert store.due() == [] or all(j["id"] != job["id"] or j["last_run"] for j in store.list())


def test_run_job_isolates_exception(tmp_path):
    import qingxiaotuan.cron.runner as runner_mod

    class _Boom:
        def run(self, prompt, stream=False):
            raise RuntimeError("agent exploded")

    runner_mod.Agent = lambda *a, **k: _Boom()
    store = CronStore(tmp_path)
    job = store.add("坏任务", "会崩", interval_minutes=60)
    jobs = store._read()
    for j in jobs:
        if j["id"] == job["id"]:
            j["last_run"] = 0
    store._write(jobs)
    kernel = _FakeKernel(store, "")
    runner = CronRunner(kernel, config=None, workspace=str(tmp_path), home=tmp_path)
    res = runner.run_job(job, stream=False)
    assert res["ok"] is False
    assert "agent exploded" in res["error"]
    # 失败也写了 job.error 事件, 不向上抛
    sess = SessionStore(tmp_path)
    assert any("job.error" in f.read_text(encoding="utf-8") for f in sess.list_sessions())


def test_run_due_jobs_counts(tmp_path):
    import qingxiaotuan.cron.runner as runner_mod
    runner_mod.Agent = lambda *a, **k: _FakeAgent("ok")
    store = CronStore(tmp_path)
    for i in range(2):
        j = store.add(f"任务{i}", f"prompt{i}", interval_minutes=60)
        jobs = store._read()
        for x in jobs:
            if x["id"] == j["id"]:
                x["last_run"] = 0
        store._write(jobs)
    kernel = _FakeKernel(store, "ok")
    n = run_due_jobs(kernel, config=None, workspace=str(tmp_path), home=tmp_path)
    assert n == 2
