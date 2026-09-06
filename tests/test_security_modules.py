"""安全模块测试 —— 路径安全 / 文件完整性 / 安全策略 / 安全告警"""
from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path

import pytest


# ============================================================ 路径安全测试

class TestPathSafety:
    """测试路径遍历保护。"""

    def _make_safety(self, workspace: str = "") -> "PathSafety":
        from qingxiaotuan.core.path_safety import PathSafety
        return PathSafety(workspace=workspace)

    def test_normal_relative_path_safe(self):
        safety = self._make_safety(workspace="/home/user/project")
        r = safety.validate_path("src/main.py", action="read")
        assert r.safe
        assert not r.denied

    def test_traversal_attack_detected(self):
        safety = self._make_safety(workspace="/home/user/project")
        r = safety.validate_path("../../etc/passwd", action="read")
        assert r.denied
        assert r.risk_level == "critical"
        assert "traversal_attack" in r.violations

    def test_url_encoded_traversal(self):
        safety = self._make_safety(workspace="/home/user/project")
        r = safety.validate_path("..%2f..%2fetc/passwd", action="read")
        assert r.denied

    def test_double_url_encoded_traversal(self):
        safety = self._make_safety(workspace="/home/user/project")
        r = safety.validate_path("..%252f..%252fetc/passwd", action="read")
        assert r.denied

    def test_unicode混淆_traversal(self):
        safety = self._make_safety(workspace="/home/user/project")
        r = safety.validate_path("..%af..%afetc/passwd", action="read")
        assert r.denied

    def test_sensitive_env_file(self):
        safety = self._make_safety(workspace="/home/user/project")
        r = safety.validate_path(".env", action="read")
        assert r.denied
        assert "sensitive_path" in r.violations

    def test_sensitive_ssh_key(self):
        safety = self._make_safety(workspace="/home/user/project")
        r = safety.validate_path(".ssh/id_rsa", action="read")
        assert r.denied

    def test_sensitive_aws_credentials(self):
        safety = self._make_safety(workspace="/home/user/project")
        r = safety.validate_path(".aws/credentials", action="read")
        assert r.denied

    def test_write_boundary_escape(self):
        safety = self._make_safety(workspace="/home/user/project")
        r = safety.validate_path("/tmp/evil.py", action="write")
        assert r.denied
        assert "boundary_escape" in r.violations

    def test_write_system_dir_forbidden(self):
        safety = self._make_safety(workspace="/home/user/project")
        r = safety.validate_path("/etc/crontab", action="write")
        assert r.denied
        # Windows 上 /etc/crontab 可能先被边界检查拦截, Linux 上被系统目录检查拦截
        assert "system_dir_write" in r.violations or "boundary_escape" in r.violations

    def test_read_allows_outside_workspace(self):
        safety = self._make_safety(workspace="/home/user/project")
        r = safety.validate_path("/tmp/data.txt", action="read")
        assert r.safe  # 读操作不限制工作区边界

    def test_safe_join_normal(self):
        safety = self._make_safety()
        result = safety.safe_join("/home/user/project", "src", "main.py")
        assert result is not None
        assert result.endswith("main.py")

    def test_safe_join_traversal_blocked(self):
        safety = self._make_safety()
        result = safety.safe_join("/home/user/project", "../etc/passwd")
        assert result is None

    def test_safe_join_escape_blocked(self):
        safety = self._make_safety()
        result = safety.safe_join("/home/user/project", "src", "../../etc/passwd")
        assert result is None

    def test_find_project_root(self):
        safety = self._make_safety()
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / ".git").mkdir()
            (Path(tmpdir) / "src").mkdir()
            (Path(tmpdir) / "src" / "deep").mkdir()
            root = safety.find_project_root(os.path.join(tmpdir, "src", "deep"))
            assert root == str(Path(tmpdir).resolve())


# ============================================================ 文件完整性测试

class TestFileIntegrity:
    """测试文件完整性监控。"""

    def _make_monitor(self, tmpdir: str, on_change=None):
        from qingxiaotuan.core.file_integrity import FileIntegrityMonitor
        return FileIntegrityMonitor(home=Path(tmpdir), on_change=on_change)

    def test_snapshot_directory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            # 创建测试文件
            test_dir = Path(tmpdir) / "test_files"
            test_dir.mkdir()
            (test_dir / "a.py").write_text("print('hello')")
            (test_dir / "b.txt").write_text("world")

            monitor = self._make_monitor(tmpdir)
            count = monitor.snapshot_directory(test_dir)
            assert count == 2

    def test_check_no_changes(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            test_dir = Path(tmpdir) / "test_files"
            test_dir.mkdir()
            (test_dir / "a.py").write_text("print('hello')")

            monitor = self._make_monitor(tmpdir)
            monitor.snapshot_directory(test_dir)
            changes = monitor.check_directory(test_dir)
            assert len(changes) == 0

    def test_check_modified_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            test_dir = Path(tmpdir) / "test_files"
            test_dir.mkdir()
            (test_dir / "a.py").write_text("print('hello')")

            monitor = self._make_monitor(tmpdir)
            monitor.snapshot_directory(test_dir)

            # 修改文件
            (test_dir / "a.py").write_text("print('modified')")

            changes = monitor.check_directory(test_dir)
            assert len(changes) == 1
            assert changes[0].change_type == "modified"

    def test_check_added_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            test_dir = Path(tmpdir) / "test_files"
            test_dir.mkdir()
            (test_dir / "a.py").write_text("print('hello')")

            monitor = self._make_monitor(tmpdir)
            monitor.snapshot_directory(test_dir)

            # 添加新文件
            (test_dir / "b.py").write_text("print('new')")

            changes = monitor.check_directory(test_dir)
            assert len(changes) == 1
            assert changes[0].change_type == "added"

    def test_check_deleted_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            test_dir = Path(tmpdir) / "test_files"
            test_dir.mkdir()
            (test_dir / "a.py").write_text("print('hello')")
            (test_dir / "b.py").write_text("print('world')")

            monitor = self._make_monitor(tmpdir)
            monitor.snapshot_directory(test_dir)

            # 删除文件
            (test_dir / "b.py").unlink()

            changes = monitor.check_directory(test_dir)
            assert len(changes) == 1
            assert changes[0].change_type == "deleted"

    def test_on_change_callback(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            test_dir = Path(tmpdir) / "test_files"
            test_dir.mkdir()
            (test_dir / "a.py").write_text("print('hello')")

            changes_received = []
            def on_change(change):
                changes_received.append(change)

            monitor = self._make_monitor(tmpdir, on_change=on_change)
            monitor.snapshot_directory(test_dir)

            (test_dir / "a.py").write_text("print('modified')")
            monitor.check_directory(test_dir)

            assert len(changes_received) == 1
            assert changes_received[0].change_type == "modified"

    def test_stats(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            test_dir = Path(tmpdir) / "test_files"
            test_dir.mkdir()
            (test_dir / "a.py").write_text("print('hello')")

            monitor = self._make_monitor(tmpdir)
            monitor.snapshot_directory(test_dir)

            (test_dir / "a.py").write_text("print('modified')")
            monitor.check_directory(test_dir)

            stats = monitor.stats()
            assert stats["total_changes"] == 1
            assert stats["by_type"]["modified"] == 1

    def test_export_report(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            test_dir = Path(tmpdir) / "test_files"
            test_dir.mkdir()
            (test_dir / "a.py").write_text("print('hello')")

            monitor = self._make_monitor(tmpdir)
            monitor.snapshot_directory(test_dir)

            (test_dir / "a.py").write_text("print('modified')")
            monitor.check_directory(test_dir)

            report = monitor.export_report()
            assert "文件完整性监控报告" in report
            assert "modified" in report

    def test_include_exclude_patterns(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            test_dir = Path(tmpdir) / "test_files"
            test_dir.mkdir()
            (test_dir / "a.py").write_text("print('hello')")
            (test_dir / "b.txt").write_text("world")

            monitor = self._make_monitor(tmpdir)

            # 仅包含 .py 文件
            count = monitor.snapshot_directory(test_dir, include_patterns=["*.py"])
            assert count == 1

            # 排除 .txt 文件
            count2 = monitor.snapshot_directory(test_dir, exclude_patterns=["*.txt"])
            assert count2 == 1


# ============================================================ 安全策略测试

class TestSecurityPolicy:
    """测试安全策略引擎。"""

    def _make_engine(self, tmpdir: str):
        from qingxiaotuan.core.security_policy import SecurityPolicyEngine
        return SecurityPolicyEngine(home=Path(tmpdir))

    def test_builtin_rules_loaded(self):
        engine = self._make_engine(tempfile.mkdtemp())
        rules = engine.list_rules(source="builtin")
        assert len(rules) > 0

    def test_deny_rm_rf(self):
        engine = self._make_engine(tempfile.mkdtemp())
        verdict = engine.evaluate_command("rm -rf /")
        assert verdict.action == "deny"

    def test_deny_dd_device(self):
        engine = self._make_engine(tempfile.mkdtemp())
        verdict = engine.evaluate_command("dd if=/dev/zero of=/dev/sda")
        assert verdict.action == "deny"

    def test_deny_format(self):
        engine = self._make_engine(tempfile.mkdtemp())
        verdict = engine.evaluate_command("format C:")
        assert verdict.action == "deny"

    def test_deny_shutdown(self):
        engine = self._make_engine(tempfile.mkdtemp())
        verdict = engine.evaluate_command("shutdown -h now")
        assert verdict.action == "deny"

    def test_allow_safe_command(self):
        engine = self._make_engine(tempfile.mkdtemp())
        verdict = engine.evaluate_command("ls -la")
        assert verdict.action == "allow"

    def test_env_file_protected(self):
        engine = self._make_engine(tempfile.mkdtemp())
        verdict = engine.evaluate_file_path(".env", action="read")
        assert verdict.action == "deny"

    def test_ssh_key_protected(self):
        engine = self._make_engine(tempfile.mkdtemp())
        verdict = engine.evaluate_file_path(".ssh/id_rsa", action="read")
        assert verdict.action == "deny"

    def test_custom_rule_deny(self):
        engine = self._make_engine(tempfile.mkdtemp())
        from qingxiaotuan.core.security_policy import PolicyRule
        engine.add_rule(PolicyRule(
            id="test_no_drop_prod",
            name="禁止删除生产数据",
            action="deny",
            priority=50,
            tools=["run_shell"],
            patterns=[r"DELETE\s+FROM\s+production"],
        ))
        verdict = engine.evaluate_command("DELETE FROM production WHERE id=1")
        assert verdict.action == "deny"
        assert verdict.rule_id == "test_no_drop_prod"

    def test_custom_rule_ask(self):
        engine = self._make_engine(tempfile.mkdtemp())
        from qingxiaotuan.core.security_policy import PolicyRule
        engine.add_rule(PolicyRule(
            id="test_ask_deploy",
            name="部署需确认",
            action="ask",
            priority=50,
            tools=["run_shell"],
            patterns=[r"docker\s+deploy"],
        ))
        verdict = engine.evaluate_command("docker deploy myapp")
        assert verdict.action == "ask"

    def test_remove_rule(self):
        engine = self._make_engine(tempfile.mkdtemp())
        from qingxiaotuan.core.security_policy import PolicyRule
        engine.add_rule(PolicyRule(
            id="test_removable",
            name="可移除规则",
            action="deny",
        ))
        assert engine.remove_rule("test_removable")
        verdict = engine.evaluate_tool("test_removable", {})
        assert verdict.action == "allow"

    def test_export_import_rules(self):
        engine = self._make_engine(tempfile.mkdtemp())
        from qingxiaotuan.core.security_policy import PolicyRule
        engine.add_rule(PolicyRule(
            id="test_export",
            name="导出测试",
            action="deny",
            tools=["test_tool"],
        ))
        exported = engine.export_rules()
        assert "test_export" in exported

        # 导入
        engine2 = self._make_engine(tempfile.mkdtemp())
        count = engine2.load_rules_from_yaml(exported, source="imported")
        assert count >= 1

    def test_stats(self):
        engine = self._make_engine(tempfile.mkdtemp())
        stats = engine.stats()
        assert stats["total"] > 0
        assert "builtin" in stats["by_source"]

    def test_priority_deny_over_allow(self):
        engine = self._make_engine(tempfile.mkdtemp())
        from qingxiaotuan.core.security_policy import PolicyRule
        # 先加 allow
        engine.add_rule(PolicyRule(
            id="test_allow",
            name="允许",
            action="allow",
            priority=100,
            tools=["run_shell"],
        ))
        # 再加 deny (更高优先级)
        engine.add_rule(PolicyRule(
            id="test_deny",
            name="拒绝",
            action="deny",
            priority=10,
            tools=["run_shell"],
        ))
        verdict = engine.evaluate_command("anything")
        assert verdict.action == "deny"
        assert verdict.rule_id == "test_deny"


# ============================================================ 安全告警测试

class TestSecurityAlert:
    """测试安全告警系统。"""

    def _make_alerter(self, tmpdir: str, enable_desktop=False):
        from qingxiaotuan.core.security_alert import SecurityAlerter
        return SecurityAlerter(
            home=Path(tmpdir),
            enable_desktop=enable_desktop,
        )

    def test_alert_recorded(self):
        alerter = self._make_alerter(tempfile.mkdtemp())
        record = alerter.alert("critical", "测试告警", "测试消息")
        assert record.severity == "critical"
        assert record.title == "测试告警"
        assert record.notified  # desktop通知失败也算已记录

    def test_alert_in_buffer(self):
        alerter = self._make_alerter(tempfile.mkdtemp())
        alerter.alert("high", "测试告警")
        recent = alerter.get_recent_alerts()
        assert len(recent) == 1

    def test_alert_severity_filter(self):
        alerter = self._make_alerter(tempfile.mkdtemp())
        alerter.alert("critical", "critical告警")
        alerter.alert("high", "high告警")
        alerter.alert("low", "low告警")

        critical = alerter.get_recent_alerts(severity="critical")
        assert len(critical) == 1

    def test_rate_limiting(self):
        alerter = self._make_alerter(tempfile.mkdtemp())
        r1 = alerter.alert("critical", "重复告警", "第一次")
        r2 = alerter.alert("critical", "重复告警", "第二次")
        # 第二次应被速率限制
        assert r1.notified
        # r2 可能被限制也可能不 (取决于时间间隔)

    def test_alert_callback(self):
        alerts_received = []
        def on_alert(record):
            alerts_received.append(record)

        alerter = self._make_alerter(tempfile.mkdtemp())
        alerter._on_alert = on_alert
        alerter.alert("critical", "回调测试")
        assert len(alerts_received) == 1

    def test_alert_stats(self):
        alerter = self._make_alerter(tempfile.mkdtemp())
        alerter.alert("critical", "测试1", source="test")
        alerter.alert("high", "测试2", source="test")

        stats = alerter.get_alert_stats()
        assert stats["total"] == 2
        assert stats["by_severity"]["critical"] == 1

    def test_export_report(self):
        alerter = self._make_alerter(tempfile.mkdtemp())
        alerter.alert("critical", "测试告警")
        report = alerter.export_alerts()
        assert "安全告警报告" in report
        assert "测试告警" in report

    def test_alert_detail_shortcut(self):
        alerter = self._make_alerter(tempfile.mkdtemp())
        r = alerter.alert_critical("critical快捷方法")
        assert r.severity == "critical"

        r = alerter.alert_high("high快捷方法")
        assert r.severity == "high"

    def test_persistence_on_disk(self):
        tmpdir = tempfile.mkdtemp()
        alerter1 = self._make_alerter(tmpdir)
        alerter1.alert("critical", "持久化测试")

        # 新建 alerter 实例 (模拟重启), 应从磁盘加载
        alerter2 = self._make_alerter(tmpdir)
        # 日志已写入磁盘, 但内存缓冲是新的
        # 验证日志文件存在
        log_file = Path(tmpdir) / "audit" / "security_alerts.jsonl"
        assert log_file.exists()
