"""并发子 Agent + 后台自主 + dispatch 工具测试 (Mock 模型, 全程离线)。

验证:
- SubAgentPool: 并发执行、结果顺序一致、上下文隔离、失败不传染;
- dispatch_tasks 工具: 经工具注册表并发派发并汇总;
- BackgroundRunner: 提交即返回不阻塞、进度写会话流、可查询 tail。
"""

import json
import time

from qingxiaotuan.app import build_kernel
from qingxiaotuan.core.agent import Agent
from qingxiaotuan.core.subagents import SubAgentPool, SubTask, make_tasks
from qingxiaotuan.core.background import BackgroundRunner
from qingxiaotuan.models.base import ModelAdapter, ModelResponse, ToolCall


class EchoModel(ModelAdapter):
    """每个子 Agent 跑一轮: 直接把 prompt 回声作为答案 (不调工具)。"""
    name = "echo"

    def __init__(self):
        self.calls = []

    def chat(self, messages, tools=None, stream=False, on_token=None, **kwargs):
        self.calls.append(messages)
        # 取最后一条 user 消息当"任务"
        last_user = ""
        for m in reversed(messages):
            if m.get("role") == "user" and m.get("content"):
                last_user = m["content"]
                break
        resp = ModelResponse(content=f"回声: {last_user[:50]}")
        if stream and on_token:
            on_token(resp.content)
        return resp


def _pool(tmp_path, qxt_home, max_workers=2, default_timeout=30.0):
    kernel = build_kernel()
    config = kernel.require("config")
    # 用 EchoModel 替换真实模型适配器
    kernel.unprovide("model_adapter")
    kernel.provide("model_adapter", EchoModel(), owner="test")
    pool = SubAgentPool(
        kernel=kernel, config=config, workspace=str(tmp_path),
        max_workers=max_workers, default_timeout=default_timeout,
        isolation="thread",  # 单测用线程模式 (mock 模型在进程内), 不拉真实子进程
    )
    return pool


def test_make_tasks_order_and_ids():
    tasks = make_tasks(["调研A", "调研B", "调研C"])
    assert [t.task_id for t in tasks] == ["T1", "T2", "T3"]
    assert tasks[0].prompt == "调研A"


def test_subagent_pool_runs_concurrent_and_ordered(tmp_path, qxt_home):
    pool = _pool(tmp_path, qxt_home, max_workers=3)
    tasks = make_tasks([f"子任务{i}" for i in range(5)])
    results = pool.dispatch(tasks, stream=False)
    # 顺序与输入一致
    assert [r.task_id for r in results] == [t.task_id for t in tasks]
    assert all(r.ok for r in results)
    # 每个子 Agent 都拿到了自己的 prompt (隔离: 不会串味)
    for r in results:
        assert "回声:" in r.output
        assert r.turns >= 1
        assert r.elapsed >= 0


def test_subagent_pool_context_isolation(tmp_path, qxt_home):
    """隔离性: 两个子 Agent 各自独立 messages, 互不影响。"""
    pool = _pool(tmp_path, qxt_home, max_workers=2)
    tasks = [
        SubTask(task_id="A", prompt="只做A的事"),
        SubTask(task_id="B", prompt="只做B的事"),
    ]
    results = pool.dispatch(tasks, stream=False)
    by_id = {r.task_id: r for r in results}
    # A 的输出只含 A 的回显, 不应出现 B 的内容
    assert "只做A的事" in by_id["A"].output
    assert "只做B的事" not in by_id["A"].output


def test_subagent_pool_failure_isolated(tmp_path, qxt_home):
    """单个子任务失败不应拖垮其它 (失败隔离)。"""
    kernel = build_kernel()
    config = kernel.require("config")
    # 用会抛错的模型
    class BoomModel(ModelAdapter):
        name = "boom"
        def chat(self, messages, tools=None, stream=False, on_token=None, **kwargs):
            raise RuntimeError("boom on purpose")
    kernel.unprovide("model_adapter")
    kernel.provide("model_adapter", BoomModel(), owner="test")
    pool = SubAgentPool(kernel=kernel, config=config, workspace=str(tmp_path),
                        max_workers=2, isolation="thread")  # mock 模型在进程内, 用线程模式
    tasks = make_tasks(["ok1", "ok2", "ok3"])
    results = pool.dispatch(tasks, stream=False)
    assert len(results) == 3
    assert all(not r.ok for r in results)
    # 每个都带上错误且顺序保留
    assert results[0].task_id == "T1"
    assert "boom" in (results[0].error or "")


def test_subagent_pool_timeout(tmp_path, qxt_home):
    """超时控制: 子任务卡住应在 timeout 内返回失败而非永久阻塞。"""
    kernel = build_kernel()
    config = kernel.require("config")
    class HangModel(ModelAdapter):
        name = "hang"
        def chat(self, messages, tools=None, stream=False, on_token=None, **kwargs):
            time.sleep(5)  # 故意卡住
            return ModelResponse(content="迟到的结果")
    kernel.unprovide("model_adapter")
    kernel.provide("model_adapter", HangModel(), owner="test")
    pool = SubAgentPool(kernel=kernel, config=config, workspace=str(tmp_path),
                        max_workers=1, default_timeout=1.0, isolation="thread")  # 线程模式测超时
    start = time.time()
    results = pool.dispatch(make_tasks(["慢任务"]), stream=False)
    elapsed = time.time() - start
    # 不应等满 5s, 应在 ~1s 超时返回
    assert elapsed < 4.0
    assert not results[0].ok
    assert "超时" in (results[0].error or "")


# ------------------------------------------------------------------ dispatch 工具

def test_dispatch_tasks_tool_registered(tmp_path, qxt_home):
    kernel = build_kernel()
    registry = kernel.require("tool_registry")
    names = {t.name for t in registry.tools}
    assert "dispatch_tasks" in names


def test_dispatch_tasks_tool_invocation(tmp_path, qxt_home):
    kernel = build_kernel()
    config = kernel.require("config")
    config.set_user("agent.subagent_isolation", "thread")  # 单测用线程模式 (mock 模型在进程内)
    kernel.unprovide("model_adapter")
    kernel.provide("model_adapter", EchoModel(), owner="test")
    registry = kernel.require("tool_registry")
    tool = registry.get("dispatch_tasks")
    assert tool is not None
    ctx = Agent(kernel=kernel, config=config, workspace=str(tmp_path), confirm=lambda _p: True).ctx
    out = tool.handler(ctx, tasks=["研究X", "研究Y"])
    assert "子任务汇总" in out
    assert "T1" in out and "T2" in out


def test_dispatch_tasks_rejects_empty(tmp_path, qxt_home):
    kernel = build_kernel()
    config = kernel.require("config")
    config.set_user("agent.subagent_isolation", "thread")
    registry = kernel.require("tool_registry")
    tool = registry.get("dispatch_tasks")
    ctx = Agent(kernel=kernel, config=config, workspace=str(tmp_path)).ctx
    out = tool.handler(ctx, tasks=[])
    assert "错误" in out


# ------------------------------------------------------------------ 后台自主

def test_background_submit_returns_immediately(tmp_path, qxt_home):
    kernel = build_kernel()
    config = kernel.require("config")
    kernel.unprovide("model_adapter")
    kernel.provide("model_adapter", EchoModel(), owner="test")
    runner = BackgroundRunner(kernel, config, str(tmp_path))
    assert runner.enabled
    start = time.time()
    job = runner.submit("后台整理周报", confirm=lambda _p: True)
    # 提交应近乎立刻返回 (不阻塞)
    assert time.time() - start < 3.0
    assert job.job_id.startswith("bg-")
    assert job.status == "running"
    # 等待结束
    job.thread.join(timeout=30)
    assert job.status in ("done", "failed")
    assert job.turns >= 1


def test_background_tail_shows_progress(tmp_path, qxt_home):
    kernel = build_kernel()
    config = kernel.require("config")
    kernel.unprovide("model_adapter")
    kernel.provide("model_adapter", EchoModel(), owner="test")
    runner = BackgroundRunner(kernel, config, str(tmp_path))
    job = runner.submit("后台任务", confirm=lambda _p: True)
    job.thread.join(timeout=30)
    tail = job.tail(10)
    # 会话流里至少应有任务开始/结束的记录文本
    assert any("后台任务" in line or "青小团" in line or "你" in line for line in tail)
    # job 可查
    assert runner.get(job.job_id) is not None
    assert any(j.job_id == job.job_id for j in runner.list_jobs())
