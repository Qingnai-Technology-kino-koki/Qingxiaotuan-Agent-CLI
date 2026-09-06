"""E2E 测试基线: 安全拦截 → 审计记录 → Loop 切换 → 模型路由升降级。

设计目标
------
- **离线可跑**: 不依赖网络 / 子进程, 直接驱动五大子系统 (Security / SecurityEventBus /
  LoopRegistry / ModelRouter) 的真实代码路径, 验证「端到端闭环」。
- 四个阶段各自独立断言, 再用 test_e2e_full_chain 串联成完整生命周期。

对应需求: 跑通「安全拦截→审计记录→Loop 切换→模型路由升降级」全链路。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    """隔离 QXT_HOME, 避免污染真实 ~/.qingxiaotuan, 并让黑名单减负测试可确定。"""
    monkeypatch.setenv("QXT_HOME", str(tmp_path))
    from qingxiaotuan.core import blacklist_override
    blacklist_override.reset_cache()
    yield tmp_path
    blacklist_override.reset_cache()


# ---------------------------------------------------------------- 阶段1: 安全拦截

def test_stage_security_interception():
    """危险命令被 is_hard_redline / is_redline 判定为红线; 良性命令不误杀。"""
    from qingxiaotuan.ext import safety_engine as se

    # 硬红线 (OS/文件系统级, YOLO 也不放行)
    assert se.is_hard_redline("rm -rf /") is True
    assert se.is_hard_redline("dd if=/dev/zero of=/dev/sda") is True
    # 综合红线 (含 SQL 破坏性, 走可确认关键级)
    assert se.is_redline("DROP TABLE users;") is True
    assert se.is_redline("TRUNCATE TABLE logs;") is True
    # 良性开发命令不被误杀
    assert se.is_redline("git status") is False
    assert se.is_redline("pytest -q") is False
    assert se.is_redline("pip install requests") is False


# ---------------------------------------------------------------- 阶段2: 审计记录

def test_stage_audit_logging(isolated_home):
    """拦截事件经 SecurityEventBus 落盘 JSONL, 且内存侧可查。"""
    from qingxiaotuan.core.security_bus import SecurityEventBus, SecurityEventType

    audit = isolated_home / "security-audit.jsonl"
    bus = SecurityEventBus(persist_path=audit)

    bus.emit_command_blocked("rm -rf /", "硬红线命中")
    bus.emit_command_blocked("DROP TABLE users;", "综合红线命中")

    # 落盘
    assert audit.exists()
    lines = audit.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2

    evt = json.loads(lines[0])
    assert evt["event_type"] == SecurityEventType.COMMAND_BLOCKED
    assert "rm -rf /" in evt["payload"]["command"]
    assert evt["severity"] == "high"

    # 内存侧可查
    blocked = bus.get_events(event_type=SecurityEventType.COMMAND_BLOCKED)
    assert len(blocked) == 2
    assert bus.get_stats()["total_events"] == 2


# ---------------------------------------------------------------- 阶段3: Loop 切换

def test_stage_loop_switching():
    """LoopRegistry.set_current 在不同 Loop 间热切换。"""
    from qingxiaotuan.core.loop_provider import LoopProvider, LoopRegistry, ReActLoop

    class _FakeLoop(LoopProvider):
        def __init__(self, name: str) -> None:
            self.name = name
            self.description = name

        def run_loop(self, agent, user_input, **kw):  # noqa: ANN001
            return ""

    reg = LoopRegistry()
    reg.register(ReActLoop())          # 默认 react
    reg.register(_FakeLoop("planner"))
    reg.register(_FakeLoop("dev"))

    assert reg.current_name == "react"
    reg.set_current("planner")
    assert reg.current_name == "planner"
    reg.set_current("dev")
    assert reg.current_name == "dev"
    reg.set_current("react")
    assert reg.current_name == "react"

    # 无效名应抛错 (fail-closed)
    with pytest.raises(ValueError):
        reg.set_current("nonexistent")


# ---------------------------------------------------------------- 阶段4a: 路由升级

def test_stage_router_escalation(isolated_home):
    """高难度任务升级到更强 (tier 更高) 的模型。"""
    from qingxiaotuan.models.router import ModelRouter

    router = ModelRouter(default_provider="zhipu", default_model="glm-4-flash")
    decision = router.decide(
        "设计一个分布式强一致性共识算法并证明其正确性, 需考虑网络分区与脑裂",
        current_provider="zhipu",
        current_model="glm-4-flash",
        available_providers=["zhipu", "deepseek", "openai", "anthropic"],
    )
    assert decision["switch"] is True
    assert decision["difficulty"] >= 7
    # 升级目标应为更强的模型 (在已配置密钥的供应商内)
    assert decision["provider"] in ("deepseek", "openai", "anthropic")


# ---------------------------------------------------------------- 阶段4b: 路由降级

def test_stage_router_deescalation(isolated_home):
    """简单任务降级到更便宜的模型以省钱。"""
    from qingxiaotuan.models.router import ModelRouter

    router = ModelRouter(
        default_provider="anthropic", default_model="claude-sonnet-4-20250514"
    )
    decision = router.decide(
        "总结这一段文字",
        current_provider="anthropic",
        current_model="claude-sonnet-4-20250514",
        available_providers=["anthropic", "zhipu", "deepseek", "doubao"],
    )
    assert decision["switch"] is True
    assert decision["difficulty"] <= 4
    # 降级目标比当前便宜 (tier 更低)
    assert decision["provider"] in ("zhipu", "doubao", "deepseek")


# ---------------------------------------------------------------- 阶段4c: 失败保险

def test_stage_router_fail_safe(isolated_home):
    """无可用密钥供应商时, 即使任务很难也保持当前模型, 绝不切到无凭证端点。"""
    from qingxiaotuan.models.router import ModelRouter

    router = ModelRouter(default_provider="zhipu", default_model="glm-4-flash")
    decision = router.decide(
        "设计一个分布式强一致性共识算法并证明其正确性",
        current_provider="zhipu",
        current_model="glm-4-flash",
        available_providers=[],  # 没有任何已配置密钥的供应商
    )
    assert decision["switch"] is False


# ---------------------------------------------------------------- 串联: 全链路

def test_e2e_full_chain(isolated_home):
    """安全拦截 → 审计记录 → Loop 切换 → 模型路由升降级 (端到端)。"""
    from qingxiaotuan.core.loop_provider import LoopProvider, LoopRegistry, ReActLoop
    from qingxiaotuan.core.security_bus import SecurityEventBus, SecurityEventType
    from qingxiaotuan.ext import safety_engine
    from qingxiaotuan.models.router import ModelRouter

    # 1) 安全拦截
    cmd = "DROP TABLE important_data;"
    assert safety_engine.is_redline(cmd) is True

    # 2) 审计记录 (落盘)
    audit = isolated_home / "security-audit.jsonl"
    bus = SecurityEventBus(persist_path=audit)
    bus.emit_command_blocked(cmd, "综合红线命中")
    assert len(bus.get_events(event_type=SecurityEventType.COMMAND_BLOCKED)) == 1
    assert audit.exists() and audit.read_text(encoding="utf-8").strip()

    # 3) Loop 切换
    class _DevLoop(LoopProvider):
        def __init__(self) -> None:
            self.name = "dev"
            self.description = "dev"

        def run_loop(self, agent, user_input, **kw):  # noqa: ANN001
            return ""

    reg = LoopRegistry()
    reg.register(ReActLoop())
    reg.register(_DevLoop())
    reg.set_current("dev")
    assert reg.current_name == "dev"

    # 4) 模型路由升级
    router = ModelRouter(default_provider="zhipu", default_model="glm-4-flash")
    decision = router.decide(
        "实现并压测一个高并发无锁队列",
        current_provider="zhipu",
        current_model="glm-4-flash",
        available_providers=["zhipu", "deepseek", "openai"],
    )
    assert decision["switch"] is True

    # 闭环校验: 审计确实落盘 (JSONL 审计 trails, 不回放内存)
    audit_lines = audit.read_text(encoding="utf-8").strip().splitlines()
    assert len(audit_lines) == 1
    assert json.loads(audit_lines[0])["event_type"] == SecurityEventType.COMMAND_BLOCKED


# ---------------------------------------------------------------- 附加: 本地黑名单减负 (与审计同链)

def test_stage_blacklist_override(isolated_home):
    """用户本地抑制某黑名单模式后, is_redline 不再命中该模式 (硬红线仍不可抑制)。"""
    from qingxiaotuan.core import blacklist_override
    from qingxiaotuan.ext import safety_engine as se

    # 正则模式命中 (format 写盘)
    assert se.is_redline("format c:") is True
    blacklist_override.suppress("format")
    assert se.is_redline("format c:") is False

    # token 化硬红线不受影响 (dd 写原始设备)
    assert se.is_hard_redline("dd if=/dev/zero of=/dev/sda") is True

    # 恢复
    blacklist_override.release("format")
    assert se.is_redline("format c:") is True
