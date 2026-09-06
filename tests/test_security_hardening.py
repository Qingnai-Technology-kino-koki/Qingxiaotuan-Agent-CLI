"""最强安全级别加固回归测试 (fail-closed 单一闸门 + 各模块门禁)。

覆盖本次加固的模块:
- ext.security_gate: 统一 fail-closed 闸门 (shell / mcp_tool / remote_prompt / file_write)
- core.remote_control: 一次性 token / 设备绑定 / 红线 prompt 拒绝 / 断开作废
- tools.mcp.sandbox: 执行前红线闸门 + 敏感环境变量抹除
- core.workspace_trust: fail-closed 动作裁决 + shell 咨询
- ext.rules_engine: 规则强制 (enforce) + ReDoS 防护
- core.sandbox: 子进程环境密钥抹除
"""
import tempfile

import pytest

from qingxiaotuan.ext.security_gate import GateVerdict, SecurityGate
from qingxiaotuan.core.remote_control import RemoteControl
from qingxiaotuan.tools.mcp.sandbox import _refuse_if_redline, _sanitize_env
from qingxiaotuan.core.workspace_trust import (
    TrustLevel,
    consult_for_shell,
    is_action_allowed,
)
from qingxiaotuan.ext.rules_engine import RuleEngine, _safe_compile
from qingxiaotuan.core.sandbox import _sanitize_sandbox_env


# ---------------------------------------------------------------- SecurityGate


def test_gate_shell_hard_redline_denied():
    g = SecurityGate()
    assert g.decide_shell("rm -rf /").blocks()
    assert g.decide_shell("git push --force").blocks()
    assert g.decide_shell("dd if=/dev/zero of=/dev/sda").blocks()


def test_gate_shell_untrusted_denied():
    g = SecurityGate(trust_level=TrustLevel.UNTRUSTED)
    assert g.decide_shell("ls -la").action == "deny"


def test_gate_shell_unknown_requires_confirm():
    g = SecurityGate(trust_level=TrustLevel.UNKNOWN)
    assert g.decide_shell("ls -la").needs_confirm()


def test_gate_shell_trusted_benign_allowed():
    g = SecurityGate(trust_level=TrustLevel.TRUSTED)
    assert g.decide_shell("git status").allows()


def test_gate_remote_non_benign_requires_confirm():
    g = SecurityGate(remote=True)
    # git reset --hard 属高风险但非硬红线 -> 远程来源需确认 (非良性命令)
    assert g.decide_shell("git reset --hard").needs_confirm()
    assert g.decide_shell("git status").allows()


def test_gate_classify_remote_redline_denied():
    g = SecurityGate()
    assert g.classify_remote_prompt("rm -rf /").blocks()
    # 良性远程 prompt 放行
    assert g.classify_remote_prompt("git status").allows()


def test_gate_mcp_tool_dangerous_denied():
    g = SecurityGate()
    assert g.decide_mcp_tool("mcp__db__exec", '{"command":"rm -rf /"}').blocks()
    # 非 mcp 工具直接放行
    assert g.decide_mcp_tool("read_file", None).allows()


def test_gate_file_write_untrusted_and_redline_denied():
    assert SecurityGate(trust_level=TrustLevel.UNTRUSTED).decide_file_write("a.txt", "hi").blocks()
    assert SecurityGate().decide_file_write("a.sh", "rm -rf /").blocks()
    assert SecurityGate().decide_file_write("a.txt", "hello").allows()


def test_gate_fail_closed_on_unknown_kind():
    assert SecurityGate().decide("bogus_kind", "x").blocks()


def test_gate_fail_closed_on_exception():
    # 异常一律保守拒绝
    g = SecurityGate()
    class _Bad:
        def blocks(self):
            raise RuntimeError("boom")
    # 直接验证 GateVerdict 在异常路径上返回 deny (通过注入异常触发)
    v = g.decide_shell(None)  # None 会触发内部异常 -> fail-closed
    assert v.blocks()


# ---------------------------------------------------------------- remote_control


def _rc() -> RemoteControl:
    return RemoteControl(home=tempfile.mkdtemp())


def test_remote_single_use_token():
    rc = _rc()
    tok = rc.start_pairing("s1")["token"]
    assert rc.confirm_pairing(tok, "iPhone") is True
    # 一次性 token 已作废, 不能再次确认 (防重放)
    assert rc.confirm_pairing(tok, "iPhone") is False


def test_remote_redline_prompt_refused():
    rc = _rc()
    tok = rc.start_pairing("s1")["token"]
    rc.confirm_pairing(tok, "iPhone")
    assert rc.send_prompt("s1", "rm -rf /") is False
    assert rc.send_prompt("s1", "git status") is True


def test_remote_disconnect_invalidates():
    rc = _rc()
    tok = rc.start_pairing("s1")["token"]
    rc.confirm_pairing(tok, "iPhone")
    assert rc.disconnect("s1") is True
    # 断开后 token 作废, 无法再发送
    assert rc.send_prompt("s1", "ls") is False


# ---------------------------------------------------------------- mcp sandbox


def test_mcp_sandbox_redline_blocked():
    assert _refuse_if_redline("rm -rf /") is not None
    assert _refuse_if_redline("ls -la") is None


def test_mcp_sandbox_env_sanitize():
    e = _sanitize_env({"PATH": "/x", "MY_API_KEY": "s", "QXT_TOKEN": "t"}, {"OK": "1"})
    assert e["MY_API_KEY"] == "" and e["QXT_TOKEN"] == ""
    assert e["PATH"] == "/x" and e["OK"] == "1"


# ---------------------------------------------------------------- workspace_trust


def test_workspace_trust_fail_closed():
    assert is_action_allowed(TrustLevel.UNTRUSTED, "shell") is False
    assert is_action_allowed("garbage", "shell") is False  # 未知级别保守拒绝
    assert is_action_allowed(TrustLevel.TRUSTED, "shell") is True
    assert is_action_allowed(TrustLevel.LIMITED, "read") is True
    assert is_action_allowed(TrustLevel.LIMITED, "shell") is False


def test_consult_for_shell():
    assert consult_for_shell(TrustLevel.UNTRUSTED, "ls")[0] == "deny"
    assert consult_for_shell(TrustLevel.UNKNOWN, "ls")[0] == "confirm"
    assert consult_for_shell(TrustLevel.TRUSTED, "ls")[0] == "allow"
    assert consult_for_shell(TrustLevel.LIMITED, "rm -rf x")[0] == "confirm"
    assert consult_for_shell(TrustLevel.LIMITED, "git status")[0] == "allow"


# ---------------------------------------------------------------- rules_engine


def test_rules_enforce_blocks_error():
    eng = RuleEngine()
    eng.load_yaml_text(
        "- id: need_key\n"
        "  severity: error\n"
        '  match:\n    path: "*.txt"\n'
        "  assert: 'matches(content, /api[_-]?key/ )'\n"
        "  message: need api key\n"
    )
    # 不含 api key -> 命中 error 级违规 -> 拒绝
    assert eng.enforce("hello", "a.txt") is not None
    # 含 api key -> 通过
    assert eng.enforce("api_key = 1", "a.txt") is None


def test_rules_redos_rejected():
    with pytest.raises(ValueError):
        _safe_compile("(a+)+")
    with pytest.raises(ValueError):
        _safe_compile("x" * 2000)


# ---------------------------------------------------------------- core/sandbox


def test_sandbox_env_sanitize():
    e = _sanitize_sandbox_env({"PATH": "/x", "OPENAI_API_KEY": "sk", "HOME": "/h"})
    assert e["OPENAI_API_KEY"] == "" and e["PATH"] == "/x" and e["HOME"] == "/h"
