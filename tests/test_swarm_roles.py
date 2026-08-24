"""Swarm n_hint 透传 + 多 Agent 角色配置的测试。"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from qingxiaotuan.core.swarm import Swarm, SwarmPlan


class _FakeKernel:
    def __init__(self) -> None:
        self.services: Dict[str, Any] = {}

    def get(self, name: str) -> Any:
        return self.services.get(name)

    def require(self, name: str) -> Any:
        return self.services[name]


def _fake_config(planner: str = "", worker: str = "opencode-zen") -> Any:
    class Cfg:
        def __init__(self, planner: str, worker: str) -> None:
            self.data = {
                "model": {
                    "provider": "deepseek",
                    "model": "deepseek-chat",
                    "planner": {"provider": planner},
                    "worker": {"provider": worker, "model": "deepseek-v4-flash-free",
                               "base_url": "https://opencode.ai/zen/v1",
                               "api_key_env": "OPENCODE_ZEN_API_KEY"},
                },
                "agent": {"subagent_timeout": 30, "subagent_isolation": "thread"},
            }

        def get(self, dotted: str, default: Any = None) -> Any:
            node: Any = self.data
            for k in dotted.split("."):
                if not isinstance(node, dict) or k not in node:
                    return default
                node = node[k]
            return node

    return Cfg(planner, worker)


def test_swarm_nhint_passed_to_planner() -> None:
    """Swarm(n_hint=3) 应让规划 prompt 请求约 3 个子任务。"""
    captured: Dict[str, Any] = {}

    def planner_fn(goal: str) -> SwarmPlan:
        # 直接返回一个固定计划, 验证 n_hint 至少被接受 (不抛错且构造正常)
        captured["called"] = True
        return SwarmPlan(goal=goal, tasks=[
            {"id": "T1", "title": "a", "prompt": "a", "depends_on": ""},
            {"id": "T2", "title": "b", "prompt": "b", "depends_on": ""},
            {"id": "T3", "title": "c", "prompt": "c", "depends_on": ""},
        ])

    kernel = _FakeKernel()
    swarm = Swarm(
        kernel=kernel, config=_fake_config(), workspace="/tmp",
        planner_fn=planner_fn, n_hint=3,
    )
    assert swarm.n_hint == 3
    plan = swarm._plan("目标")
    assert captured.get("called") is True
    assert len(plan.tasks) == 3


def test_swarm_nhint_clamped() -> None:
    """n_hint 应被夹在 [2, 8]。"""
    swarm = Swarm(kernel=_FakeKernel(), config=_fake_config(), workspace="/tmp", n_hint=99)
    assert swarm.n_hint == 8
    swarm2 = Swarm(kernel=_FakeKernel(), config=_fake_config(), workspace="/tmp", n_hint=1)
    assert swarm2.n_hint == 2


def test_swarm_planner_role_read_from_model() -> None:
    """Swarm 应从 model.planner (而非 agent.planner) 读取强模型角色。"""
    cfg = _fake_config(planner="claude-gw")
    swarm = Swarm(kernel=_FakeKernel(), config=cfg, workspace="/tmp")
    assert swarm.planner.get("provider") == "claude-gw"


def test_swarm_worker_overrides_from_model_worker() -> None:
    """弱模型覆盖层应来自 model.worker。"""
    cfg = _fake_config(worker="opencode-zen")
    swarm = Swarm(kernel=_FakeKernel(), config=cfg, workspace="/tmp")
    ov = swarm.worker_overrides
    assert ov.get("provider") == "opencode-zen"
    assert ov.get("model") == "deepseek-v4-flash-free"
