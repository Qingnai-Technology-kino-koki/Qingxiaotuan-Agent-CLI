"""SecurityAuditor 接入可插拔加密后端的测试: 持久化 / 防篡改链 / 后端选型降级。"""
import logging

from qingxiaotuan.core.security_auditor import SecurityAuditor
from qingxiaotuan.harden.crypto_provider import SoftwareProvider


def _record_some(auditor):
    auditor.record(
        module="security_gate", action="deny", severity="critical",
        input_summary="rm -rf /", reasons=["命中致命红线"],
        context={"trust_level": "trusted"},
    )
    auditor.record(
        module="safety_engine", action="allow", severity="low",
        input_summary="ls -la", reasons=[],
    )


def test_auditor_software_provider_persist_and_reload(tmp_path):
    log_path = tmp_path / "audit_home"
    a1 = SecurityAuditor(home=log_path, crypto_provider="software")
    assert isinstance(a1._provider, SoftwareProvider)
    _record_some(a1)
    assert a1.count() == 2
    assert a1.verify_integrity()["valid"] is True
    assert "software" in a1.algorithm

    # 用同一 home 重新打开, 应当读回既有日志 (证明落盘 + 可解密)
    a2 = SecurityAuditor(home=log_path, crypto_provider="software")
    assert a2.count() == 2
    integ = a2.verify_integrity()
    assert integ["valid"] is True
    # 防篡改链: 改一条内存记录的 hash 应使校验失败
    a2._buffer[0].record_hash = "deadbeef"
    assert a2.verify_integrity()["valid"] is False


def test_auditor_provider_fallback_gmssl(tmp_path, caplog):
    # 本环境通常未装 gmssl -> 应降级到 software 并告警, 不抛错
    with caplog.at_level(logging.WARNING, logger="qingxiaotuan.core.security_auditor"):
        a = SecurityAuditor(home=tmp_path / "h1", crypto_provider="gmssl")
    assert a._provider.name == "software"
    assert a.count() == 0  # 仍可正常工作


def test_auditor_provider_fallback_hsm(tmp_path, caplog):
    # hsm 不适用于流式审计日志 -> 降级 software + 明确告警
    with caplog.at_level(logging.WARNING, logger="qingxiaotuan.core.security_auditor"):
        a = SecurityAuditor(home=tmp_path / "h2", crypto_provider="hsm")
    assert a._provider.name == "software"


def test_auditor_export_markdown_contains_algorithm(tmp_path):
    a = SecurityAuditor(home=tmp_path / "h3", crypto_provider="software")
    _record_some(a)
    md = a.export_markdown(last_n=10)
    assert "算法" in md
    assert "software" in md
