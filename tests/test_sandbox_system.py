"""系统级沙箱子系统 (4 层滤网) 单元与集成测试。

覆盖:
  - Verdict 决策模型: 严格度合并 / describe / blocks
  - L0 意图滤网: 致命红线 deny-critical; 良性不误拦
  - L1 信任滤网: untrusted 拒 / trusted 放行 / 计划模式写拦截 / unknown 确认
  - L2 资源滤网: 高危写→copy-diff-apply 隔离
  - L3 强隔离滤网: fail-closed (无强后端 + enforce → deny)
  - SandboxFilterChain 逐层收紧 + 短路
  - SandboxManager.assess_tool 全工具统一入口
  - backend 自动探测; isolation 路径限界 + 副本 diff-apply
  - DEFAULT_CONFIG["sandbox"] 默认开启
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from qingxiaotuan.sandbox.verdict import (
    Action, Payload, Verdict, SEV, deny, confirm, isolate,
)
from qingxiaotuan.sandbox.filters import (
    IntentFilter, TrustFilter, ResourceFilter, HardIsolationFilter, SandboxFilterChain,
)
from qingxiaotuan.sandbox.backends import pick_backend, detect_backends
from qingxiaotuan.sandbox.isolation import (
    resolve_in_workspace, isolate_workdir, plan_isolation,
)
from qingxiaotuan.sandbox.manager import SandboxManager, json_deep_merge
from qingxiaotuan.core.security_bus import SecurityEventBus, SecurityEventType
from qingxiaotuan.config import defaults as DEFAULTS


# ================================================================ 决策模型
class TestVerdict:
    def test_deny_is_strongest_and_blocks(self):
        v = deny("intent", "hit", SEV["critical"])
        assert v.action == Action.DENY
        assert v.blocks is True
        assert v.layer == "intent"

    def test_merged_takes_strictest(self):
        allow = Verdict(Action.ALLOW)
        out = allow.merged(confirm("trust", "needs ack"))
        assert out.action == Action.CONFIRM
        assert out.layer == "trust"

    def test_merged_marks_isolate_and_net(self):
        out = Verdict().merged(Verdict(Action.ALLOW, isolate=True, network=False, layer="resource"))
        assert out.isolate is True
        assert out.network is False

    def test_describe_mentions_layer(self):
        s = deny("intent", "boom").describe()
        assert "intent" in s and "deny" in s


# ================================================================ L0 意图
class TestIntentFilter:
    def test_hard_redline_denied_critical(self):
        v = IntentFilter().check(Payload(text=["rm -rf /"]))
        assert v is not None
        assert v.action == Action.DENY
        assert v.severity == SEV["critical"]
        assert v.layer == "intent"

    def test_benign_never_critical(self):
        for cmd in ("git status", "ls -la", "echo hi", "pytest -q"):
            v = IntentFilter().check(Payload(text=[cmd]))
            # 良性命令绝不致命拒绝 (可能 None=交信任层, 或 ALLOW=良性)
            assert v is None or v.action != Action.DENY


# ================================================================ L1 信任
class TestTrustFilter:
    def _p(self, **kw):
        base = dict(trust_level=None, text=["echo hi"])
        base.update(kw)
        return Payload(**base)

    def test_untrusted_exec_deny(self):
        v = TrustFilter().check(self._p(trust_level="untrusted"))
        assert v is not None and v.action == Action.DENY

    def test_trusted_allow(self):
        assert TrustFilter().check(self._p(trust_level="trusted")) is None

    def test_unknown_confirmed(self):
        v = TrustFilter().check(self._p(trust_level="unknown"))
        assert v is not None and v.action == Action.CONFIRM

    def test_plan_mode_write_deny(self):
        p = Payload(tool_name="write_file", method="write",
                    text=["x"], plan_mode=True)
        v = TrustFilter().check(p)
        assert v is not None and v.action == Action.DENY


# ================================================================ L2 资源
class TestResourceFilter:
    def test_high_severity_copy_diff_apply(self):
        p = Payload(text=["python script.py"], meta={"severity": SEV["high"]})
        v = ResourceFilter(isolate_copy_threshold="high").check(p)
        assert v is not None
        assert v.workspace_mode == "copy_diff_apply"
        assert v.isolate is True

    def test_low_severity_no_isolation(self):
        p = Payload(text=["git status"], meta={"severity": SEV["low"]})
        v = ResourceFilter(isolate_copy_threshold="high").check(p)
        if v is not None:
            assert v.isolate is False

    def test_default_never_deny(self):
        p = Payload(text=["echo hi"], meta={"severity": SEV["none"]})
        v = ResourceFilter().check(p)
        assert v is None or v.action != Action.DENY


# ================================================================ L3 强隔离
class TestHardIsolationFilter:
    def test_no_isolation_no_backend(self):
        base = Verdict()  # 未要求隔离
        assert HardIsolationFilter().check(base, Payload(text=["echo hi"])) is None

    def test_fail_closed_when_enforce_and_no_strong(self, monkeypatch):
        monkeypatch.setattr("qingxiaotuan.sandbox.backends.pick_backend",
                            lambda prefer="auto": ("local", True))
        base = Verdict(isolate=True)
        p = Payload(text=["rm -rf /tmp/x"])
        v = HardIsolationFilter(prefer="auto", enforce_require=True).check(base, p)
        assert v is not None and v.action == Action.DENY

    def test_local_not_strong(self):
        v = Verdict(isolate=True).merged(
            Verdict(layer="hard", backend="local", backend_available=False))
        assert v.isolate is True
        assert v.backend == "local"
        assert v.backend_available is False


# ================================================================ 编排链
class TestChain:
    def test_redline_shortcircuits_to_deny(self):
        chain = SandboxFilterChain()
        v = chain.evaluate(Payload(text=["rm -rf /"], trust_level="trusted"))
        assert v.action == Action.DENY
        assert v.layer == "intent"

    def test_untrusted_write_denied_by_chain(self):
        chain = SandboxFilterChain()
        v = chain.evaluate(Payload(tool_name="write_file", text=["x"],
                                   method="write", trust_level="untrusted"))
        assert v.action == Action.DENY

    def test_trusted_benign_allowed(self):
        chain = SandboxFilterChain()
        v = chain.evaluate(Payload(text=["git status"], trust_level="trusted"))
        assert v.action == Action.ALLOW


# ================================================================ 后端探测
class TestBackends:
    def test_pick_backend_returns_tuple(self):
        name, ok = pick_backend("auto")
        assert name in ("landlock", "docker", "seatbelt", "token-acl", "jobobject", "local")
        assert isinstance(ok, bool)

    def test_local_always_available(self):
        assert pick_backend("auto")[0] in ("landlock", "docker", "seatbelt", "token-acl", "jobobject")
        assert pick_backend("local") == ("local", True)

    def test_detect_backends_sorted_with_local_tail(self):
        lst = detect_backends()
        assert lst[-1]["name"] == "local"
        assert lst[-1]["available"] is True


# ================================================================ 配置节
class TestConfigSection:
    def test_default_enabled(self):
        sb = DEFAULTS.DEFAULT_CONFIG["sandbox"]
        assert sb["enabled"] is True
        assert sb["backend"] == "auto"
        assert sb["enforce_required"] is True
        assert sb["resource"]["isolate_copy_threshold"] == "high"


# ================================================================ 隔离/路径
class TestIsolationPath:
    def test_resolve_within_workspace(self, tmp_path):
        got = resolve_in_workspace(str(tmp_path), "sub/file.txt")
        assert os.path.normcase(got) == os.path.normcase(str(tmp_path / "sub" / "file.txt"))

    def test_resolve_rejects_escape(self, tmp_path):
        with pytest.raises(ValueError):
            # os.path.realpath 会把 ../ 归一化; 越界抛 ValueError
            resolve_in_workspace(str(tmp_path), os.path.join("..", "..", "secret.txt"))

    def test_plan_isolation_by_severity(self):
        assert plan_isolation("low") == "direct"
        assert plan_isolation("high") == "copy_diff_apply"
        assert plan_isolation("critical") == "copy_diff_apply"

    def test_copy_diff_apply_roundtrip(self, tmp_path):
        ws = tmp_path / "ws"
        ws.mkdir()
        (ws / "a.txt").write_text("hello", encoding="utf-8")
        wd = isolate_workdir(str(ws))          # 已固化: base=原始, copy=副本
        copy = Path(wd.path) / "a.txt"
        copy.write_text("hello world", encoding="utf-8")
        (Path(wd.path) / "new.txt").write_text("new", encoding="utf-8")
        diff = wd.diff()
        by = {d["path"]: d["status"] for d in diff}
        assert by.get("a.txt") == "modified"
        assert by.get("new.txt") == "added"
        applied = wd.apply()
        assert set(applied) >= {"a.txt", "new.txt"}
        assert (ws / "a.txt").read_text(encoding="utf-8") == "hello world"
        wd.cleanup()

    def test_manager_execute_isolated_runs(self, tmp_path):
        m = SandboxManager({"enabled": True, "enforce_required": False})
        v = Verdict(isolate=True, backend="local", network=True,
                    enforced=False, backend_available=False)
        res = m.execute_isolated("echo sandbox-ok", v,
                                 type("ctx", (), {"workspace": str(tmp_path)})(), timeout=10)
        assert "sandbox-ok" in res.stdout


# ================================================================ 工具级集成
class TestAssessTool:
    def test_write_in_untrusted_denied(self):
        m = SandboxManager({"enabled": True})
        ctx = type("ctx", (), {
            "workspace": "/tmp/ws", "workspace_trust_level": "untrusted",
            "plan_mode": False, "yolo": False, "safety_severity": None,
        })()
        denied, msg = m.assess_tool("write_file", {"path": "/tmp/ws/x", "content": "rm -rf /"}, ctx)
        assert denied is True

    def test_benign_read_allowed(self):
        m = SandboxManager({"enabled": True})
        ctx = type("ctx", (), {
            "workspace": "/tmp/ws", "workspace_trust_level": "trusted",
            "plan_mode": False, "yolo": False, "safety_severity": None,
        })()
        denied, msg = m.assess_tool("read_file", {"path": "/tmp/ws/a.py"}, ctx)
        assert denied is False

    def test_disabled_shortcircuits(self):
        m = SandboxManager({"enabled": False})
        ctx = type("ctx", (), {
            "workspace": "/tmp/ws", "workspace_trust_level": "untrusted",
            "plan_mode": False, "yolo": False, "safety_severity": None,
        })()
        denied, msg = m.assess_tool("write_file", {"content": "rm -rf /"}, ctx)
        assert denied is False


# ================================================================ 深度合并
class TestMerge:
    def test_deep_merge_preserves_base(self):
        out = json_deep_merge({"a": {"x": 1, "y": 2}, "b": 1}, {"a": {"y": 3}})
        assert out["a"] == {"x": 1, "y": 3}
        assert out["b"] == 1

    def test_manager_policy_defaults(self):
        # 子系统内部默认 enforce_required=False (fail-soft); 配置节(DEFAULT_CONFIG)
        # 才把 enforce_required 提为 True, 经 from_kernel 生效。
        assert SandboxManager({"enabled": True}).policy["enabled"] is True
        assert SandboxManager({"enforce_required": True}).policy["enforce_required"] is True
        assert DEFAULTS.DEFAULT_CONFIG["sandbox"]["enforce_required"] is True


# ================================================================ 审计闭环
class TestAuditClosure:
    def _manager(self, bus, auditor):
        return SandboxManager({"enabled": True}, auditor=auditor, bus=bus)

    def test_deny_emits_sandbox_blocked_event(self):
        bus = SecurityEventBus()
        seen = []
        bus.on("*", lambda e: seen.append(e))
        m = self._manager(bus, None)
        m.assess(Payload(text=["rm -rf /"], trust_level="trusted"))
        assert any(e.event_type == SecurityEventType.SANDBOX_BLOCKED for e in seen)

    def test_benign_allowed_emits_nothing(self):
        bus = SecurityEventBus()
        seen = []
        bus.on("*", lambda e: seen.append(e))
        m = self._manager(bus, None)
        m.assess(Payload(text=["git status"], trust_level="trusted"))
        assert not seen  # 良性放行不入审计, 避免噪音

    def test_deny_written_to_auditor(self, tmp_path):
        from qingxiaotuan.core.security_auditor import SecurityAuditor
        auditor = SecurityAuditor(home=tmp_path / "home", passphrase="test")
        m = self._manager(None, auditor)
        m.assess(Payload(text=["rm -rf /"], trust_level="trusted"))
        recs = auditor.query(module="sandbox", action="deny")
        assert recs and recs[0].severity == "critical"
        assert recs[0].context.get("layer") == "intent"

    def test_bus_event_reaches_auditor_via_subscribe(self, tmp_path):
        from qingxiaotuan.core.security_auditor import SecurityAuditor
        bus = SecurityEventBus()
        auditor = SecurityAuditor(home=tmp_path / "home", passphrase="test")
        auditor.subscribe_bus(bus)
        m = self._manager(bus, None)
        m.assess(Payload(text=["format c: /q"], trust_level="trusted"))
        recs = auditor.query(module="sandbox", action="deny")
        assert recs and recs[0].input_summary == "format c: /q"

    def test_audit_failure_never_breaks_assess(self):
        class BoomBus:
            def emit_sandbox_blocked(self, *a, **k):
                raise RuntimeError("bus boom")
        m = SandboxManager({"enabled": True}, bus=BoomBus())
        v = m.assess(Payload(text=["rm -rf /"], trust_level="trusted"))
        assert v.blocks is True  # 审计炸了沙箱仍正常裁决