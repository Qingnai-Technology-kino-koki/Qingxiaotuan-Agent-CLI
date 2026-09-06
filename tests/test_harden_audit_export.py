"""审计日志标准化导出测试 (CEF / JSONL / RFC5424 Syslog)。"""
import json

from qingxiaotuan.core.security_bus import SecurityEvent
from qingxiaotuan.harden.audit_export import (
    AuditExporter,
    format_event,
    to_cef,
    to_jsonl,
    to_syslog,
)


def _ev(**kw):
    base = dict(
        event_type="security.command.blocked",
        timestamp=1700000000.0,
        payload={"command": "rm -rf /", "reason": "hard_redline"},
        source="safety_engine",
        severity="critical",
    )
    base.update(kw)
    return SecurityEvent(**base)


def test_format_jsonl():
    line = to_jsonl(_ev())
    obj = json.loads(line)
    assert obj["event_type"] == "security.command.blocked"
    assert obj["severity"] == "critical"


def test_format_cef():
    line = to_cef(_ev())
    assert line.startswith("CEF:0|")
    assert "|10|" in line  # critical -> 10
    assert "msg=" in line


def test_format_syslog():
    line = to_syslog(_ev())
    assert line.startswith("<") and "1 " in line  # RFC5424: <PRI>1
    # MSG 部分应为 JSON (从首个 '{' 提取, 不受 RFC5424 头部字段干扰)
    msg = line[line.index("{"):]
    assert json.loads(msg)["event_type"] == "security.command.blocked"


def test_exporter_writes_files(tmp_path):
    out = tmp_path / "export"
    exp = AuditExporter(formats=("jsonl", "cef"), out_dir=str(out))
    exp.emit(_ev())
    exp.emit(_ev(severity="high"))
    exp.close()
    jl = (out / "audit_export.jsonl").read_text(encoding="utf-8").splitlines()
    cef = (out / "audit_export.cef").read_text(encoding="utf-8").splitlines()
    assert len(jl) == 2 and len(cef) == 2
    assert jl[0].startswith("{")
    assert cef[0].startswith("CEF:0|")


def test_exporter_push_failure_is_silent(tmp_path):
    # push 到无效 URL 不应抛异常, 只返回 False
    exp = AuditExporter(formats=("jsonl",), out_dir=str(tmp_path), push_url="http://127.0.0.1:9/nope")
    # 不应抛异常
    exp.emit(_ev())
    exp.close()


def test_export_file_replay(tmp_path):
    src = tmp_path / "bus.jsonl"
    src.write_text(to_jsonl(_ev()) + "\n" + to_jsonl(_ev(severity="high")) + "\n", encoding="utf-8")
    out = tmp_path / "out"
    exp = AuditExporter(formats=("jsonl",), out_dir=str(out))
    n = exp.export_file(str(src))
    exp.close()
    assert n == 2
    lines = (out / "audit_export.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2


def test_unsupported_format_raises():
    import pytest
    with pytest.raises(ValueError):
        format_event(_ev(), "xml")
