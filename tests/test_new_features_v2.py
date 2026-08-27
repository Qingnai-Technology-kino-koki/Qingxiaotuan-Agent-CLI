"""Tests for Auto Mode, Cross-Session Messaging, Workspace Trust, Managed Settings, Remote Control, and Telemetry."""

import json
import os
import time
from pathlib import Path

import pytest

from qingxiaotuan.core.auto_mode import (
    AutoModeClassifier,
    RiskAssessment,
    RiskLevel,
    assess_tool_risk,
    is_auto_approvable,
    TOOL_BASELINE_RISK,
    SHELL_RISK_PATTERNS,
)
from qingxiaotuan.core.messaging import (
    MessageBus,
    PeerSession,
    CrossMessage,
)
from qingxiaotuan.core.workspace_trust import (
    WorkspaceTrust,
    TrustLevel,
    TrustRecord,
)
from qingxiaotuan.config.managed import (
    ManagedSettings,
    SettingsSource,
)
from qingxiaotuan.core.remote_control import (
    RemoteControl,
    RemoteSession,
)
from qingxiaotuan.core.telemetry import (
    TelemetryCollector,
    Span,
)


# ================================================================ Auto Mode

class TestAutoModeClassifier:
    def test_safe_tools_auto_approve(self):
        classifier = AutoModeClassifier()
        for tool in ("read_file", "read_files", "code_search", "glob", "list_directory"):
            result = classifier.assess(tool, {"path": "test.py"})
            assert result.auto_approve is True, f"{tool} should be auto-approvable"

    def test_shell_dangerous_rm_rf(self):
        classifier = AutoModeClassifier()
        result = classifier.assess("run_shell", {"command": "rm -rf /"})
        assert result.level == RiskLevel.CRITICAL
        assert result.needs_confirm is True

    def test_shell_dangerous_git_force_push(self):
        classifier = AutoModeClassifier()
        result = classifier.assess("run_shell", {"command": "git push --force"})
        assert result.level == RiskLevel.CRITICAL
        assert result.needs_confirm is True

    def test_shell_dangerous_sudo(self):
        classifier = AutoModeClassifier()
        result = classifier.assess("run_shell", {"command": "sudo apt install python"})
        assert result.level == RiskLevel.HIGH
        assert result.needs_confirm is True

    def test_shell_safe_command(self):
        classifier = AutoModeClassifier()
        result = classifier.assess("run_shell", {"command": "ls -la"})
        assert result.auto_approve is True
        assert result.needs_confirm is False

    def test_shell_drop_table(self):
        classifier = AutoModeClassifier()
        result = classifier.assess("run_shell", {"command": "DROP TABLE users"})
        assert result.level == RiskLevel.CRITICAL

    def test_user_custom_rules(self):
        config = {"permissions.auto_mode_rules": [
            {"tool": "write_file", "action": "allow", "description": "允许写文件"},
            {"tool": "run_shell", "action": "deny", "description": "禁止 shell"},
        ]}
        classifier = AutoModeClassifier(config)
        result = classifier.assess("run_shell", {"command": "ls"})
        assert result.level == RiskLevel.CRITICAL
        assert result.auto_approve is False

    def test_session_history_dedup(self):
        classifier = AutoModeClassifier()
        # 第一次: 需要确认
        result1 = classifier.assess("delete_file", {"path": "test.txt"})
        assert result1.needs_confirm is True
        # 标记已确认
        classifier.confirm_tool("delete_file")
        # 第二次: 自动批准
        result2 = classifier.assess("delete_file", {"path": "test.txt"})
        assert result2.auto_approve is True

    def test_assess_tool_risk_function(self):
        result = assess_tool_risk("read_file", {"path": "test.py"})
        assert result.auto_approve is True

    def test_is_auto_approvable_function(self):
        assert is_auto_approvable("read_file", {"path": "test.py"}) is True
        assert is_auto_approvable("run_shell", {"command": "rm -rf /"}) is False


# ================================================================ Cross-Session Messaging

class TestMessageBus:
    def test_register_and_list(self, tmp_path):
        bus = MessageBus(tmp_path)
        bus.register("s1", "session-1", "/workspace", pid=100)
        bus.register("s2", "session-2", "/workspace2", pid=200)
        agents = bus.list_agents(include_self=True)
        assert len(agents) == 2

    def test_send_receive(self, tmp_path):
        bus = MessageBus(tmp_path)
        bus.register("s1", "sender", "/ws1")
        bus.register("s2", "receiver", "/ws2")
        msg = bus.send("s1", "s2", "Hello!")
        assert msg.content == "Hello!"
        assert msg.from_name == "sender"

        messages = bus.receive("s2")
        assert len(messages) == 1
        assert messages[0].content == "Hello!"

    def test_receive_marks_read(self, tmp_path):
        bus = MessageBus(tmp_path)
        bus.register("s1", "a", "/a")
        bus.register("s2", "b", "/b")
        bus.send("s1", "s2", "msg1")
        bus.send("s1", "s2", "msg2")

        # 第一次接收: 2 条未读
        msgs = bus.receive("s2")
        assert len(msgs) == 2

        # 第二次接收: 0 条未读
        msgs = bus.receive("s2")
        assert len(msgs) == 0

    def test_unread_count(self, tmp_path):
        bus = MessageBus(tmp_path)
        bus.register("s1", "a", "/a")
        bus.register("s2", "b", "/b")
        bus.send("s1", "s2", "msg1")
        bus.send("s1", "s2", "msg2")
        assert bus.unread_count("s2") == 2

    def test_unregister(self, tmp_path):
        bus = MessageBus(tmp_path)
        bus.register("s1", "a", "/a")
        bus.unregister("s1")
        agents = bus.list_agents(include_self=True)
        assert len(agents) == 0

    def test_discard_messages(self, tmp_path):
        bus = MessageBus(tmp_path)
        bus.register("s1", "a", "/a")
        bus.register("s2", "b", "/b")
        bus.send("s1", "s2", "msg1")
        bus.send("s1", "s2", "msg2")
        count = bus.discard_messages("s2")
        assert count == 2
        assert bus.unread_count("s2") == 0


# ================================================================ Workspace Trust

class TestWorkspaceTrust:
    def test_unknown_by_default(self, tmp_path):
        trust = WorkspaceTrust(tmp_path)
        assert trust.check_trust("/some/path") == TrustLevel.UNKNOWN

    def test_set_and_check(self, tmp_path):
        trust = WorkspaceTrust(tmp_path)
        trust.set_trust("/my/project", TrustLevel.TRUSTED, project_name="My Project")
        assert trust.check_trust("/my/project") == TrustLevel.TRUSTED
        assert trust.is_trusted("/my/project") is True

    def test_limited_trust(self, tmp_path):
        trust = WorkspaceTrust(tmp_path)
        trust.set_trust("/my/project", TrustLevel.LIMITED)
        assert trust.check_trust("/my/project") == TrustLevel.LIMITED
        assert trust.is_trusted("/my/project") is False

    def test_untrusted(self, tmp_path):
        trust = WorkspaceTrust(tmp_path)
        trust.set_trust("/my/project", TrustLevel.UNTRUSTED)
        assert trust.is_readonly("/my/project") is True

    def test_list_trusted(self, tmp_path):
        trust = WorkspaceTrust(tmp_path)
        trust.set_trust("/a", TrustLevel.TRUSTED)
        trust.set_trust("/b", TrustLevel.LIMITED)
        trust.set_trust("/c", TrustLevel.TRUSTED)
        trusted = trust.list_trusted()
        assert len(trusted) == 2

    def test_remove(self, tmp_path):
        trust = WorkspaceTrust(tmp_path)
        trust.set_trust("/my/project", TrustLevel.TRUSTED)
        assert trust.remove("/my/project") is True
        assert trust.check_trust("/my/project") == TrustLevel.UNKNOWN

    def test_persistence(self, tmp_path):
        trust1 = WorkspaceTrust(tmp_path)
        trust1.set_trust("/my/project", TrustLevel.TRUSTED)
        # 重新加载
        trust2 = WorkspaceTrust(tmp_path)
        assert trust2.check_trust("/my/project") == TrustLevel.TRUSTED

    def test_get_project_info(self, tmp_path):
        trust = WorkspaceTrust(tmp_path)
        trust.set_trust("/my/project", TrustLevel.TRUSTED, project_name="Test")
        info = trust.get_project_info("/my/project")
        assert info["trust_level"] == TrustLevel.TRUSTED
        assert info["project_name"] == "Test"
        assert info["needs_confirmation"] is False

    def test_unknown_needs_confirmation(self, tmp_path):
        trust = WorkspaceTrust(tmp_path)
        info = trust.get_project_info("/unknown/path")
        assert info["needs_confirmation"] is True


# ================================================================ Managed Settings

class TestManagedSettings:
    def test_merge_defaults(self, tmp_path):
        ms = ManagedSettings(tmp_path)
        config = ms.merge()
        assert isinstance(config, dict)

    def test_user_settings_override(self, tmp_path):
        # 写入用户级设置
        settings = tmp_path / "settings.json"
        settings.write_text(json.dumps({"model": {"provider": "test-provider"}}))
        ms = ManagedSettings(tmp_path)
        config = ms.merge()
        assert config.get("model", {}).get("provider") == "test-provider"

    def test_project_settings_override(self, tmp_path):
        project = tmp_path / "project"
        project.mkdir()
        project_settings = project / ".qingxiaotuan"
        project_settings.mkdir()
        (project_settings / "settings.json").write_text(json.dumps({
            "model": {"provider": "project-provider"}
        }))
        ms = ManagedSettings(tmp_path)
        config = ms.merge(project_dir=project)
        assert config.get("model", {}).get("provider") == "project-provider"

    def test_env_settings(self, tmp_path):
        os.environ["QXT_MODEL_PROVIDER"] = "env-provider"
        try:
            ms = ManagedSettings(tmp_path)
            config = ms.merge()
            assert config.get("model", {}).get("provider") == "env-provider"
        finally:
            del os.environ["QXT_MODEL_PROVIDER"]

    def test_get_dotted_path(self, tmp_path):
        ms = ManagedSettings(tmp_path)
        # defaults should have some keys
        val = ms.get("language", "")
        assert isinstance(val, str)

    def test_list_sources(self, tmp_path):
        ms = ManagedSettings(tmp_path)
        sources = ms.list_sources()
        assert len(sources) >= 1  # at least defaults


# ================================================================ Remote Control

class TestRemoteControl:
    def test_start_pairing(self, tmp_path):
        rc = RemoteControl(tmp_path, host="localhost", port=8080)
        pairing = rc.start_pairing("session-1")
        assert "url" in pairing
        assert "token" in pairing
        assert pairing["session_id"] == "session-1"

    def test_confirm_pairing(self, tmp_path):
        rc = RemoteControl(tmp_path)
        pairing = rc.start_pairing("session-1")
        ok = rc.confirm_pairing(pairing["token"], device_name="iPhone")
        assert ok is True

        sessions = rc.list_sessions()
        assert len(sessions) == 1
        assert sessions[0]["connected"] is True
        assert sessions[0]["device_name"] == "iPhone"

    def test_invalid_token(self, tmp_path):
        rc = RemoteControl(tmp_path)
        ok = rc.confirm_pairing("invalid-token")
        assert ok is False

    def test_disconnect(self, tmp_path):
        rc = RemoteControl(tmp_path)
        pairing = rc.start_pairing("session-1")
        rc.confirm_pairing(pairing["token"])
        rc.disconnect("session-1")
        sessions = rc.list_sessions()
        assert sessions[0]["connected"] is False

    def test_get_status(self, tmp_path):
        rc = RemoteControl(tmp_path)
        status = rc.get_status("nonexistent")
        assert status["enabled"] is False

    def test_pairing_expires(self, tmp_path):
        rc = RemoteControl(tmp_path)
        pairing = rc.start_pairing("session-1")
        # Simulate expired token
        rs = rc._sessions["session-1"]
        rs.created_at = time.time() - 400  # 400 seconds ago
        assert rs.is_expired is True


# ================================================================ Telemetry

class TestTelemetryCollector:
    def test_start_finish_trace(self, tmp_path):
        tc = TelemetryCollector(tmp_path)
        trace_id = tc.start_trace("agent.run")
        assert trace_id in tc._traces
        tc.finish_trace(trace_id)
        assert tc._traces[trace_id]["status"] == "ok"

    def test_start_finish_span(self, tmp_path):
        tc = TelemetryCollector(tmp_path)
        span = tc.start_span("model.chat")
        assert span.is_active is True
        tc.finish_span(span.span_id)
        assert span.is_active is False
        assert span.status == "ok"

    def test_record_tool_call(self, tmp_path):
        tc = TelemetryCollector(tmp_path)
        tc.record_tool_call("read_file", 50.0, True)
        stats = tc.get_stats()
        assert stats["total_tool_calls"] == 1

    def test_record_model_call(self, tmp_path):
        tc = TelemetryCollector(tmp_path)
        tc.record_model_call(100.0, 1000, True)
        stats = tc.get_stats()
        assert stats["total_tokens"] == 1000

    def test_error_counting(self, tmp_path):
        tc = TelemetryCollector(tmp_path)
        span = tc.start_span("test")
        tc.finish_span(span.span_id, "error")
        stats = tc.get_stats()
        assert stats["total_errors"] == 1

    def test_get_stats(self, tmp_path):
        tc = TelemetryCollector(tmp_path)
        tc.start_trace("test")
        tc.record_tool_call("read_file", 50.0, True)
        tc.record_tool_call("write_file", 100.0, True)
        tc.record_model_call(200.0, 500, True)
        stats = tc.get_stats()
        assert stats["total_traces"] == 1
        assert stats["total_tool_calls"] == 2
        assert stats["total_tokens"] == 500
        assert stats["avg_tool_latency_ms"] == 75.0

    def test_export_jsonl(self, tmp_path):
        tc = TelemetryCollector(tmp_path)
        span = tc.start_span("test")
        tc.finish_span(span.span_id)
        count = tc.export_jsonl()
        assert count == 1

    def test_clear(self, tmp_path):
        tc = TelemetryCollector(tmp_path)
        tc.start_span("test")
        tc.record_tool_call("test", 10, True)
        tc.clear()
        stats = tc.get_stats()
        assert stats["total_spans"] == 0
        assert stats["total_tool_calls"] == 0

    def test_add_span_event(self, tmp_path):
        tc = TelemetryCollector(tmp_path)
        span = tc.start_span("test")
        tc.add_span_event(span.span_id, "cache_hit", {"key": "test"})
        assert len(span.events) == 1
        assert span.events[0]["name"] == "cache_hit"
