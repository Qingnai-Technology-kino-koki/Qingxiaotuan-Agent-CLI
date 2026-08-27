"""测试: 用户自定义权限规则表 (permissions.rules) —— 对标 Claude Code 的 allow/deny/ask 规则。

覆盖:
- allow 规则: 非 YOLO 下放行危险工具 (免确认自动批准)
- deny 规则: 连只读工具一起拦截
- ask 规则: 即便 YOLO 也强制走确认环节
- 优先级: 多条命中取最严者 deny > ask > allow
- pattern 参数通配: 未命中 pattern 时回落默认决策
- 内置 shell 加固确认不受 allow 规则豁免
- 无关工具不受规则影响
"""

from __future__ import annotations

import json

from qingxiaotuan.tools.base import Tool, ToolContext, ToolRegistry
from qingxiaotuan.tools.permissions import PermissionPolicy


class _Cfg:
    """最小配置桩: 只回答 permissions.rules。"""

    def __init__(self, rules):
        self.rules = rules

    def get(self, key, default=None):
        if key == "permissions.rules":
            return self.rules
        return default


class _FakeKernel:
    def get(self, _):
        return None


def _ctx(rules, *, yolo=False, confirm_calls=None) -> ToolContext:
    policy = PermissionPolicy(_Cfg(rules))

    def _confirm(prompt):
        if confirm_calls is not None:
            confirm_calls.append(prompt)
        return False  # 一律拒绝, 便于断言"确实走了确认"

    return ToolContext(kernel=_FakeKernel(), workspace=".", yolo=yolo,
                       confirm=_confirm, permissions=policy)


def _reg() -> ToolRegistry:
    reg = ToolRegistry()
    reg.register(Tool(name="deploy_tool", description="d",
                      parameters={"type": "object", "properties": {}},
                      handler=lambda ctx, **kw: "ok", dangerous=True))
    reg.register(Tool(name="run_shell", description="d",
                      parameters={"type": "object", "properties": {"command": {"type": "string"}}},
                      handler=lambda ctx, **kw: "ok", dangerous=True))
    reg.register(Tool(name="read_tool", description="d",
                      parameters={"type": "object", "properties": {}},
                      handler=lambda ctx, **kw: "ok", read_only=True))
    return reg


# ----------------------------------------------------------------- allow 规则

def test_allow_rule_auto_approves_dangerous_tool_without_yolo():
    reg = _reg()
    calls: list[str] = []
    ctx = _ctx([{"tool": "deploy_tool", "action": "allow"}], confirm_calls=calls)
    result = reg.dispatch_result("deploy_tool", "{}", ctx)
    assert result.status == "ok"
    assert result.content == "ok"
    assert calls == []  # 未触发任何确认弹窗


# ----------------------------------------------------------------- deny 规则

def test_deny_rule_blocks_even_readonly_tool():
    reg = _reg()
    result = reg.dispatch_result("read_tool", "{}", _ctx([{"tool": "*", "action": "deny"}]))
    assert result.status == "denied"
    assert "permissions.rules" in result.content


# ----------------------------------------------------------------- ask 规则

def test_ask_rule_forces_confirm_even_under_yolo():
    reg = _reg()
    calls: list[str] = []
    ctx = _ctx([{"tool": "deploy_tool", "action": "ask"}], yolo=True, confirm_calls=calls)
    result = reg.dispatch_result("deploy_tool", "{}", ctx)
    assert result.status == "denied"  # 确认回调恒拒绝
    assert len(calls) == 1            # 确实走进了确认环节


# ----------------------------------------------------------------- 优先级与目标文本

def test_match_rule_priority_deny_over_ask_over_allow():
    policy = PermissionPolicy(_Cfg([
        {"tool": "a", "action": "allow"},
        {"tool": "a", "action": "ask"},
        {"tool": "a", "action": "deny"},
    ]))
    assert policy.match_rule("a", {}) == "deny"


def test_match_rule_pattern_targets_command_path_url_and_fallback():
    policy = PermissionPolicy(_Cfg([]))
    # run_shell -> command 文本
    policy.rules = ({"tool": "run_shell", "pattern": "git *", "action": "allow"},)
    assert policy.match_rule("run_shell", {"command": 'git push origin'}) == "allow"
    assert policy.match_rule("run_shell", {"command": "rm -rf /"}) is None
    # 其余工具 -> path 优先
    policy.rules = ({"tool": "*_file", "pattern": "*.txt", "action": "allow"},)
    assert policy.match_rule("write_file", {"path": "a.TXT"}) == "allow"
    # url 次之, 兜底拼接参数值
    policy.rules = ({"tool": "web_fetch", "pattern": "https://example.com/*", "action": "allow"},)
    assert policy.match_rule("web_fetch", {"url": "https://example.com/x"}) == "allow"
    policy.rules = ({"tool": "misc", "pattern": "*hello world*", "action": "deny"},)
    assert policy.match_rule("misc", {"x": "hello", "y": "world"}) == "deny"


def test_pattern_miss_falls_back_to_default_decision():
    reg = _reg()
    calls: list[str] = []
    ctx = _ctx([{"tool": "run_shell", "pattern": "git *", "action": "allow"}],
               confirm_calls=calls)
    result = reg.dispatch_result("run_shell", json.dumps({"command": "rm -rf build"}), ctx)
    assert result.status == "denied"   # 规则未命中 -> 危险工具默认确认 -> 被拒
    assert len(calls) == 1


def test_builtin_hardening_not_exempted_by_allow_rule():
    reg = _reg()
    calls: list[str] = []
    ctx = _ctx([{"tool": "run_shell", "action": "allow"}], confirm_calls=calls)
    # fd 重定向命中内置 fail-closed 加固, 应先于用户 allow 规则生效
    result = reg.dispatch_result("run_shell", json.dumps({"command": "prog 2> err.log"}), ctx)
    assert result.status == "denied"
    assert len(calls) == 1  # 未被 allow 规则豁免, 确实走了人工确认


# ----------------------------------------------------------------- 隔离性

def test_unrelated_rules_do_not_affect_other_tools():
    reg = _reg()
    calls: list[str] = []
    ctx = _ctx([{"tool": "read_tool", "action": "deny"},
                {"tool": "deploy_tool", "action": "allow"}], confirm_calls=calls)
    assert reg.dispatch_result("read_tool", "{}", ctx).status == "denied"
    assert reg.dispatch_result("deploy_tool", "{}", ctx).status == "ok"
    assert calls == []


def test_empty_rules_keep_default_behavior():
    policy = PermissionPolicy(None)
    assert policy.rules == ()
    assert policy.match_rule("anything", {}) is None
