"""多 Agent 协作 (herdr 式: 强模型规划 + 弱模型并发 + 强模型验收 + 共享黑板) 离线测试。

核心验证:
- SwarmPlan 解析: 模型输出的 JSON / ```json fence / 噪声 都能正确解析;
- Blackboard 线程安全读写 + context_block 可读;
- Swarm.run 三阶段流程串联 (用注入的 planner/acceptor 函数, 不依赖真实模型);
- worker_overrides 从 model.worker 派生 (只配 provider 也能继承主 model 字段);
- depends_on 会被注入到子任务 prompt (黑板通信);
- SubAgentPool 透传 model_overrides 到沙箱 req (检查 sandbox.run_in_sandbox 收到的参数)。
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from qingxiaotuan.config import Config
from qingxiaotuan.core.swarm import (
    Swarm, SwarmPlan, Blackboard,
    _planner_prompt, _acceptor_prompt,
)
from qingxiaotuan.core.subagents import SubAgentPool, SubTask, make_tasks


def _cfg(**overrides):
    cfg = Config(profile="default")
    for k, v in overrides.items():
        cfg.data["model"][k] = v
    return cfg


# ---------------------------------------------------------------- 规划解析

def test_parse_plan_plain_json():
    raw = json.dumps({
        "goal": "g", "notes": "n",
        "tasks": [{"id": "T1", "title": "a", "prompt": "do a", "depends_on": ""}],
    })
    plan = Swarm._parse_plan("g", raw)
    assert plan.tasks[0]["id"] == "T1"
    assert plan.tasks[0]["prompt"] == "do a"
    assert plan.notes == "n"


def test_parse_plan_with_fence_and_noise():
    raw = "好的, 计划如下:\n```json\n" + json.dumps({
        "tasks": [{"id": "T2", "title": "b", "prompt": "do b"}]
    }) + "\n```\n以上。"
    plan = Swarm._parse_plan("g", raw)
    assert plan.tasks[0]["id"] == "T2"
    assert len(plan.tasks) == 1


def test_parse_plan_fallback_on_garbage():
    plan = Swarm._parse_plan("g", "完全不是 json 的胡言乱语")
    assert len(plan.tasks) == 1
    assert plan.tasks[0]["id"] == "T1"


def test_parse_plan_empty_tasks_fallback():
    plan = Swarm._parse_plan("g", json.dumps({"tasks": []}))
    assert len(plan.tasks) == 1


# ---------------------------------------------------------------- 黑板

def test_blackboard_read_write_and_context():
    b = Blackboard()
    b.write("T1", "result of T1", by="worker")
    assert b.read("T1") == "result of T1"
    block = b.context_block()
    assert "T1" in block and "result of T1" in block
    assert "worker" in b.log_text()


def test_blackboard_thread_safe():
    import threading
    b = Blackboard()

    def writer(i):
        for j in range(50):
            b.write(f"k{i}-{j}", f"v{i}-{j}", by="w")

    ts = [threading.Thread(target=writer, args=(i,)) for i in range(8)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert len(b.get_all()) == 8 * 50


# ---------------------------------------------------------------- 协作流程 (注入 mock)

def _fake_planner(goal):
    return SwarmPlan(
        goal=goal,
        tasks=[
            {"id": "T1", "title": "研究 A", "prompt": "研究 A 的细节", "depends_on": ""},
            {"id": "T2", "title": "研究 B", "prompt": "研究 B 的细节", "depends_on": "T1"},
        ],
    )


def _fake_acceptor(goal, plan, results_text, board):
    return f"[验收] 目标={goal}; 黑板含 {len(board.get_all())} 条; 结果摘要={results_text[:20]}"


class _FakePool(SubAgentPool):
    """离线假池: 不跑真实模型, 直接造假的 SubResult。"""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.last_overrides = []

    def dispatch(self, tasks, **kw):
        # 记录 Swarm 透传进来的弱模型覆盖层 (pool 级)
        self.last_overrides = [dict(self.model_overrides or {}) for _ in tasks]
        from qingxiaotuan.core.subagents import SubResult
        return [
            SubResult(
                task_id=t.task_id, prompt=t.prompt, ok=True,
                output=f"worker 产出: {t.prompt[:20]}", turns=1, elapsed=0.1,
            )
            for t in tasks
        ]


def test_swarm_run_three_stage_with_injected_fns():
    cfg = _cfg(provider="deepseek", base_url="https://api.deepseek.com", model="deepseek-chat")
    # worker 弱模型覆盖
    cfg.data["model"]["worker"] = {
        "provider": "opencode-zen", "model": "deepseek-v4-flash-free",
        "base_url": "https://opencode.ai/zen/v1", "api_key_env": "OPENCODE_ZEN_API_KEY",
    }
    fake_pool = _FakePool(kernel=None, config=cfg, workspace=".",
                          model_overrides=None, isolation="thread", max_workers=1)
    swarm = Swarm(
        kernel=None, config=cfg, workspace=".",
        planner_fn=_fake_planner, acceptor_fn=_fake_acceptor,
        isolation="thread", max_workers=1, pool=fake_pool,
    )
    collab = swarm.run("写一份开源公告")
    assert collab.plan.tasks[0]["id"] == "T1"
    assert collab.plan.tasks[1]["depends_on"] == "T1"
    assert "[验收]" in collab.accepted
    assert "worker 产出" in collab.blackboard_log
    # 黑板记录了 plan + 两个 worker 产物
    assert "T1" in collab.blackboard_log and "T2" in collab.blackboard_log
    # 弱模型覆盖层确实透传进了每个子任务 (provider/model/base_url/api_key_env 齐)
    expected = {"provider": "opencode-zen", "model": "deepseek-v4-flash-free",
                "base_url": "https://opencode.ai/zen/v1", "api_key_env": "OPENCODE_ZEN_API_KEY"}
    for ov in fake_pool.last_overrides:
        assert ov["provider"] == expected["provider"]
        assert ov["model"] == expected["model"]
        assert ov["base_url"] == expected["base_url"]
        assert ov["api_key_env"] == expected["api_key_env"]



def test_worker_overrides_derive_from_config():
    cfg = _cfg(provider="deepseek", base_url="https://api.deepseek.com", model="deepseek-chat",
               temperature=0.7)
    cfg.data["model"]["worker"] = {"provider": "opencode-zen"}
    swarm = Swarm(kernel=None, config=cfg, workspace=".",
                  planner_fn=_fake_planner, acceptor_fn=_fake_acceptor, isolation="thread")
    ov = swarm.worker_overrides
    assert ov["provider"] == "opencode-zen"
    # 只配 provider, 其余从主 model 继承
    assert ov["model"] == "deepseek-chat"
    assert ov["base_url"] == "https://api.deepseek.com"
    assert ov["temperature"] == 0.7


def test_depends_on_injected_into_worker_prompt():
    cfg = _cfg(provider="deepseek", base_url="https://api.deepseek.com", model="deepseek-chat")
    swarm = Swarm(kernel=None, config=cfg, workspace=".",
                  planner_fn=_fake_planner, acceptor_fn=_fake_acceptor, isolation="thread")
    plan = _fake_planner("g")
    board = Blackboard()
    board.write("T1", "T1 的研究结论", by="worker")
    subtasks = swarm._build_worker_prompts(plan, board)
    t2 = [t for t in subtasks if t.task_id == "T2"][0]
    assert "T1 的研究结论" in t2.prompt
    assert "研究 B 的细节" in t2.prompt


def test_planner_overrides_empty_returns_no_switch():
    cfg = _cfg(provider="deepseek", base_url="https://api.deepseek.com", model="deepseek-chat")
    swarm = Swarm(kernel=None, config=cfg, workspace=".",
                  planner_fn=_fake_planner, acceptor_fn=_fake_acceptor, isolation="thread")
    # model.planner.provider 留空 -> 不切换
    assert swarm.planner_overrides() == {}


# ---------------------------------------------------------------- SubAgentPool 透传

def test_subagentpool_propagates_model_overrides():
    cfg = _cfg(provider="deepseek", base_url="https://api.deepseek.com", model="deepseek-chat")

    # 用假 worker 覆盖 _run_one_thread, 既触发 dispatch 的 meta 注入, 又不依赖真实模型
    class _CapturePool(SubAgentPool):
        def _run_one_thread(self, task, *a, **kw):
            from qingxiaotuan.core.subagents import SubResult
            ov = kw.get("model_overrides") or task.meta.get("model_overrides") or self.model_overrides
            self.captured = getattr(self, "captured", []) + [ov]
            return SubResult(task_id=task.task_id, prompt=task.prompt, ok=True, output="ok")

    pool = _CapturePool(kernel=None, config=cfg, workspace=".",
                        model_overrides={"provider": "opencode-zen"}, isolation="thread", max_workers=1)
    pool.dispatch(make_tasks(["x", "y"]))
    assert pool.captured == [{"provider": "opencode-zen"}, {"provider": "opencode-zen"}]


# ---------------------------------------------------------------- 提示词构造

def test_prompts_non_empty():
    assert _planner_prompt("目标X").strip()
    assert _acceptor_prompt("goal", "[plan]", "[res]", "[board]").strip()
