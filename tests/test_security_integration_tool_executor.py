"""集成测试: 统一安全闸门接入 ToolExecutor 主执行链。

验证「该加的必须集成」—— 此前 SecurityGate / security_auditor 虽已实现并注册,
但 ToolExecutor 主工具执行链并未走它们: 非 MCP 内置工具参数从不扫红线、
write_file/edit_file 没过 decide_file_write 闸门、工具调用没有统一审计记录。

本测试确保: 每一个工具调用都默认过统一闸门, 危险操作 fail-closed 拦截,
且审计溯源默认开启。
"""

import types

from qingxiaotuan.core.tool_executor import ToolExecutor


class _FakeCtx:
    kernel = None
    yolo = False
    trust_level = None
    remote = False


class _FakeRegistry:
    def get(self, name):
        return None

    def dispatch(self, name, args, ctx):
        return "dispatched:" + name


class _FakeAuditor:
    def __init__(self):
        self.records = []

    def record(self, **kw):
        self.records.append(kw)


def _make(auditor=None):
    ctx = _FakeCtx()
    if auditor is not None:
        ctx.kernel = types.SimpleNamespace(
            get=lambda svc: auditor if svc == "security_auditor" else None
        )
    ex = ToolExecutor(registry=_FakeRegistry())
    return ex, ctx


def test_write_file_with_redline_content_blocked():
    """write_file 写入含致命操作的内容应被闸门拒绝。"""
    ex, ctx = _make()
    denied, msg = ex._security_gate_check(
        "write_file",
        '{"path": "/tmp/x.sh", "content": "rm -rf /"}',
        ctx,
    )
    assert denied is True
    assert "安全拦截" in msg


def test_edit_file_with_redline_blocked():
    """edit_file 的 new_string 含致命操作应被拦截。"""
    ex, ctx = _make()
    denied, msg = ex._security_gate_check(
        "edit_file",
        '{"path": "/tmp/x", "old_string": "a", "new_string": "rm -rf / --no-preserve-root"}',
        ctx,
    )
    assert denied is True


def test_mcp_tool_dangerous_arg_blocked():
    """MCP 工具的文本参数含危险操作应被拦截 (走统一闸门)。"""
    ex, ctx = _make()
    denied, msg = ex._security_gate_check(
        "mcp__server__exec", '{"command": "rm -rf /"}', ctx
    )
    assert denied is True
    assert "安全拦截" in msg


def test_benign_read_file_allowed():
    """良性内置工具 (如 read_file) 不应被拦截。"""
    ex, ctx = _make()
    denied, msg = ex._security_gate_check(
        "read_file", '{"path": "/tmp/notes.txt"}', ctx
    )
    assert denied is False
    assert msg == ""


def test_benign_mcp_query_allowed():
    """MCP 良性查询 (SELECT) 不应被拦截。"""
    ex, ctx = _make()
    denied, msg = ex._security_gate_check(
        "mcp__postgres__query", '{"query": "SELECT * FROM users"}', ctx
    )
    assert denied is False


def test_other_tool_redline_param_blocked():
    """其它内置工具的文本参数含危险操作应被纵深防御拦截。"""
    ex, ctx = _make()
    denied, msg = ex._security_gate_check(
        "run_code", '{"code": "rm -rf /"}', ctx
    )
    assert denied is True


def test_audit_records_deny_when_auditor_present():
    """审计器存在时, 危险调用应写入审计记录 (默认开)。"""
    auditor = _FakeAuditor()
    ex, ctx = _make(auditor=auditor)
    denied, _ = ex._security_gate_check(
        "write_file", '{"path": "/tmp/x", "content": "rm -rf /"}', ctx
    )
    assert denied is True
    # gate.decide("file_write") 会自动 record 该裁决
    assert len(auditor.records) >= 1
    rec = auditor.records[0]
    assert rec.get("action") == "deny"
    assert rec.get("severity") in ("critical", "high")


def test_audit_records_allow_for_mcp_when_present():
    """审计器存在时, 放行的 MCP 调用也应留痕 (允许记录由 gate.decide 完成)。"""
    auditor = _FakeAuditor()
    ex, ctx = _make(auditor=auditor)
    denied, _ = ex._security_gate_check(
        "mcp__postgres__query", '{"query": "SELECT 1"}', ctx
    )
    assert denied is False
    assert any(r.get("action") == "allow" for r in auditor.records)


def test_execute_single_appends_denied_message():
    """_execute_single 对危险调用应追加 status=denied 的 tool 消息。"""
    ex, ctx = _make()
    messages = []
    tc = {
        "id": "call_1",
        "function": {
            "name": "write_file",
            "arguments": '{"path": "/tmp/x", "content": "rm -rf /"}',
        },
    }
    ex._execute_single(tc, ctx, exclude=set(), on_tool_result=None)
    assert messages == []  # 拦截时不依赖外部 messages 注入
    # 通过 on_tool_result 回调捕获结果
    captured = []
    ex._execute_single(
        tc, ctx, exclude=set(), on_tool_result=lambda n, r: captured.append((n, r))
    )
    assert captured, "应触发 on_tool_result"
    name, result = captured[0]
    assert name == "write_file"
    assert "安全拦截" in result
