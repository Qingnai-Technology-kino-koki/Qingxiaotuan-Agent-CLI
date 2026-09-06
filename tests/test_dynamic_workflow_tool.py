"""Dynamic Workflow 工具层测试 —— dynamic_workflow / workflow_status 处理逻辑。"""

from __future__ import annotations

import re
import time

import pytest

from qingxiaotuan.tools.base import ToolContext
from qingxiaotuan.core.dynamic_workflow import get_engine, close_engines
from qingxiaotuan.core.subagents import SubResult
import qingxiaotuan.tools.dynamic_workflow_tool as dw
import qingxiaotuan.config as _cfg


class FakeConfig:
    def __init__(self, home):
        self.home = home

    def get(self, key, default=None):
        return default


class FakeKernel:
    def __init__(self, config):
        self._config = config

    def get(self, key):
        return self._config if key == "config" else None

    def require(self, key):
        return self._config if key == "config" else None

    def emit(self, *a, **k):
        pass


class _FakePool:
    def __init__(self, worker):
        self.worker = worker

    def dispatch(self, tasks, **kw):
        out = []
        for t in tasks:
            out.extend(self.worker(t) if callable(self.worker)
                       else [SubResult(task_id=t.task_id, prompt=t.prompt, ok=True,
                                       output="OK", error=None)])
        return out


@pytest.fixture(autouse=True)
def _clean():
    yield
    close_engines()


def _ctx(tmp_path):
    return ToolContext(kernel=FakeKernel(FakeConfig(tmp_path)), workspace=str(tmp_path))


def _wire(home):
    """把临时 home 的共享引擎配上假池, 并让 home_dir 指向临时目录。"""
    eng = get_engine(home)
    eng.configure(kernel=FakeKernel(FakeConfig(home)), config=FakeConfig(home),
                  workspace=str(home), main_agent=None)
    eng.pool_factory = lambda e: _FakePool(None)
    return eng


@pytest.fixture
def env(monkeypatch, tmp_path):
    _wire(tmp_path)
    monkeypatch.setattr(_cfg, "home_dir", lambda: tmp_path)
    return tmp_path


# ---------------------------------------------------------------- 工具层

def test_start_workflow_and_status_list(env):
    ctx = _ctx(env)
    assert dw.build_dynamic_workflow_tool().name == "dynamic_workflow"
    assert dw.build_workflow_status_tool().name == "workflow_status"

    # 不传 steps 应报错
    out = dw._start_workflow(ctx)
    assert "需提供 steps" in out

    # 正常启动
    res = dw._start_workflow(
        ctx,
        name="测试流",
        steps=[[{"title": "调研A", "prompt": "pa"}, {"title": "调研B", "prompt": "pb"}]],
    )
    assert res.startswith("已启动动态工作流")
    m = re.search(r"-> (wf-[a-f0-9]+)", res)
    wf_id = m.group(1)

    # status
    st = dw._status_workflow(ctx, action="status", wf_id=wf_id)
    assert "测试流" in st and "1 步" in st

    # list
    lst = dw._status_workflow(ctx, action="list")
    assert "测试流" in lst
    assert wf_id in lst


def test_status_missing_wf(env):
    ctx = _ctx(env)
    out = dw._status_workflow(ctx, action="status", wf_id="wf-none")
    assert "找不到工作流" in out

    out2 = dw._status_workflow(ctx, action="result")
    assert "需提供 wf_id" in out2


def test_add_step_via_tool(env):
    ctx = _ctx(env)
    res = dw._start_workflow(ctx, name="动态", steps=[[{"title": "a", "prompt": "p"}]])
    wf_id = re.search(r"-> (wf-[a-f0-9]+)", res).group(1)

    add = dw._status_workflow(ctx, action="add_step", wf_id=wf_id,
                              steps_json=[{"title": "追加任务", "prompt": "px"}])
    assert "已动态追加步骤 #2" in add

    st = dw._status_workflow(ctx, action="status", wf_id=wf_id)
    assert "2 步" in st


def test_unknown_action(env):
    ctx = _ctx(env)
    out = dw._status_workflow(ctx, action="nope", wf_id="wf-x")
    assert "未知 action" in out


# ---------------------------------------------------------------- 编排端到端 (顺序时序)

def test_engine_full_run_with_dynamic_add(tmp_path):
    from qingxiaotuan.core.dynamic_workflow import DynamicWorkflowEngine

    calls = []

    class Pool(_FakePool):
        def dispatch(self, tasks, **kw):
            calls.append(len(tasks))
            # 放慢让 add_step 有机会插入
            time.sleep(0.15)
            return [SubResult(task_id=t.task_id, prompt=t.prompt, ok=True,
                              output="R:" + t.prompt, error=None) for t in tasks]

    eng = DynamicWorkflowEngine(tmp_path, pool_factory=lambda e: Pool(None))
    wf_id = eng.create("mc", [[{"title": "s1", "prompt": "p1"}]])
    time.sleep(0.02)
    eng.add_step(wf_id, [{"title": "s2", "prompt": "p2"}, {"title": "s2b", "prompt": "p2b"}])
    deadline = time.time() + 4
    while time.time() < deadline and eng.status(wf_id)["status"] != "done":
        time.sleep(0.02)
    st = eng.status(wf_id)
    assert st["status"] == "done"
    assert st["n_steps"] == 2
    # 第一步 1 个任务, 第二步 2 个任务
    assert calls == [1, 2]
    res = eng.result(wf_id)
    assert res["ok_tasks"] == 3
    assert "p1" in res["summary"] and "p2b" in res["summary"]


def test_emit_event_on_complete(tmp_path):
    from qingxiaotuan.core.dynamic_workflow import DynamicWorkflowEngine

    events = []

    class K(FakeKernel):
        def emit(self, event, payload=None):
            events.append((event, payload))

    class Pool(_FakePool):
        def dispatch(self, tasks, **kw):
            return [SubResult(task_id=t.task_id, prompt=t.prompt, ok=True,
                              output="ok", error=None) for t in tasks]

    eng = DynamicWorkflowEngine(tmp_path, pool_factory=lambda e: Pool(None))
    eng.configure(kernel=K(FakeConfig(tmp_path)), config=FakeConfig(tmp_path),
                  workspace=str(tmp_path))
    wf_id = eng.create("evt", [[{"title": "a", "prompt": "p"}]])
    deadline = time.time() + 3
    while time.time() < deadline and eng.status(wf_id)["status"] != "done":
        time.sleep(0.02)
    names = [name for name, _ in events]
    assert "workflow.completed" in names
    ev = next(p for n, p in events if n == "workflow.completed")
    assert ev["workflow_id"] == wf_id
    assert ev["ok_tasks"] == 1


# ---------------------------------------------------------------- 第三轮迭代: 插件注册集成

def test_plugin_registers_tools_and_engine_service(tmp_path):
    from qingxiaotuan.core.kernel import Kernel
    from qingxiaotuan.tools.base import ToolRegistry
    from qingxiaotuan.tools.dynamic_workflow_tool import DynamicWorkflowPlugin

    kernel = Kernel()
    reg = ToolRegistry(cache=None)
    kernel.provide("tool_registry", reg, owner="test")
    kernel.provide("config", FakeConfig(tmp_path), owner="test")
    DynamicWorkflowPlugin().activate(kernel)

    assert reg.get("dynamic_workflow") is not None
    assert reg.get("workflow_status") is not None

    # 引擎作为内核服务被提供
    eng = kernel.get("dynamic_workflow_engine")
    assert eng is not None
    assert eng.store.dir.exists()