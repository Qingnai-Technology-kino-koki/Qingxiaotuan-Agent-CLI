"""CronRunner: 任务执行 + 异常隔离 + 结果落会话流 (离线, 不跑真实模型)。"""
import json
import time
from pathlib import Path

from qingxiaotuan.cron.runner import CronRunner, _write_output, run_due_jobs
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


# ------------------------------------------------------------------ 结果落盘 (_write_output)

def test_write_output_success(tmp_path):
    out_file = tmp_path / "sub" / "result.md"
    job = {"id": "abc123", "name": "日报", "output": str(out_file)}
    _write_output(job, "这是任务产出内容", None)
    assert out_file.exists()
    content = out_file.read_text(encoding="utf-8")
    assert "这是任务产出内容" in content
    assert "日报" in content  # 头部含任务名


def test_write_output_failure(tmp_path):
    out_file = tmp_path / "result.md"
    job = {"id": "abc123", "name": "日报", "output": str(out_file)}
    _write_output(job, "", "模型超时")
    content = out_file.read_text(encoding="utf-8")
    assert "[失败] 模型超时" in content


def test_write_output_no_target_is_noop(tmp_path):
    _write_output({"id": "x", "name": "y"}, "内容", None)  # 无 output, 不应抛异常


def test_write_output_bad_path_silent(tmp_path):
    # 非法路径 (如系统盘根目录只读区) 应静默, 不抛异常
    _write_output({"id": "x", "output": "Z:\\no\\such\\dir\\out.md"}, "内容", None)


def test_run_job_writes_output_file(tmp_path):
    import qingxiaotuan.cron.runner as runner_mod
    out_file = tmp_path / "report.md"
    store, job, kernel = _make(tmp_path, "今日完成 A/B/C")
    store.update(job["id"], output=str(out_file))
    job = store.get(job["id"])
    runner = CronRunner(kernel, config=None, workspace=str(tmp_path), home=tmp_path)
    res = runner.run_job(job, stream=False)
    assert res["ok"] is True
    assert out_file.exists()
    assert "今日完成" in out_file.read_text(encoding="utf-8")


# ------------------------------------------------------------------ 墙钟超时 + 调度互斥


class _Cfg:
    """极简 config 桩: 只支持 .get(key, default)。"""

    def __init__(self, d):
        self.d = dict(d)

    def get(self, key, default=None):
        return self.d.get(key, default)


def test_run_job_timeout_abandons_and_marks_run(tmp_path, monkeypatch):
    """卡死的任务到墙钟上限应强判失败返回, 不拖垮调度; 且 mark_run 防热循环重触发。"""
    import qingxiaotuan.cron.runner as runner_mod

    class _Slow:
        def run(self, prompt, stream=False):
            time.sleep(4)
            return "迟到的结果"

    monkeypatch.setattr(runner_mod, "Agent", lambda *a, **k: _Slow())
    store = CronStore(tmp_path)
    job = store.add("慢任务", "会卡住", interval_minutes=60)
    jobs = store._read()
    for x in jobs:
        if x["id"] == job["id"]:
            x["last_run"] = 0
    store._write(jobs)
    kernel = _FakeKernel(store, "")
    runner = CronRunner(kernel, _Cfg({"cron.job_timeout": 1, "cron.notify": False}),
                        workspace=str(tmp_path), home=tmp_path)
    t0 = time.time()
    res = runner.run_job(job, stream=False)
    elapsed = time.time() - t0
    assert res["ok"] is False
    assert "超时" in (res["error"] or "")
    assert elapsed < 3.5  # 未等满 4 秒即返回
    # 超时也算执行过: last_run 已推进, 下个周期才重试 (不会每 tick 热循环重 firing)
    assert all(j["last_run"] > 0 for j in store.list())
    # 失败写进会话流 job.error
    sess = SessionStore(tmp_path)
    assert any("job.error" in f.read_text(encoding="utf-8") for f in sess.list_sessions())


def test_run_due_jobs_skips_while_another_sweep_holds_lock(tmp_path, monkeypatch):
    """已有调度进程持锁时, 第二个调度方应整体跳过, 而不是重复执行同一批任务。"""
    import qingxiaotuan.cron.runner as runner_mod
    monkeypatch.setattr(runner_mod, "Agent", lambda *a, **k: _FakeAgent("x"))
    store = CronStore(tmp_path)
    j = store.add("任务A", "p", interval_minutes=60)
    jobs = store._read()
    for x in jobs:
        if x["id"] == j["id"]:
            x["last_run"] = 0
    store._write(jobs)
    kernel = _FakeKernel(store, "x")

    with runner_mod._sweep_lock(tmp_path) as got:
        assert got is True  # 第一把锁应成功获取
        n = run_due_jobs(kernel, config=None, workspace=str(tmp_path), home=tmp_path)
        assert n == 0  # 锁被占用: 本轮整体跳过
        assert all(x["last_run"] == 0 for x in store.list())  # 一个都没被执行

    # 锁释放后恢复正常执行
    n = run_due_jobs(kernel, config=None, workspace=str(tmp_path), home=tmp_path)
    assert n == 1
