"""融合层权限测试 (fusion.tools_permission_gate)。

覆盖: 开关关闭时完全等价原生、开关开启时把写/改类工具对敏感文件的 allow 升级为 confirm、
原生 deny/confirm 不被降级、读类工具保持原生放行、敏感路径可嵌套在参数结构中。

无需任何外部 IDE/网络/LLM。
"""

from __future__ import annotations

from qingxiaotuan.tools.permissions import PermissionDecision, PermissionPolicy
from qingxiaotuan.tools.permission_fusion import (
    FusionPermissionPolicy,
    build_permission_policy,
)


class _Cfg(dict):
    """最小 config 替身: 支持 .get, 并允许 fusion.* 开关。"""


class _Tool:
    def __init__(self, name="write_file", read_only=False, dangerous=False):
        self.name = name
        self.read_only = read_only
        self.dangerous = dangerous


class _FixedPolicy:
    """测试用固定决策代理, 便于验证叠加层对 deny/confirm 的透传。"""

    def __init__(self, decision: PermissionDecision):
        self._decision = decision

    def decide(self, tool, args, *, yolo=False):
        return self._decision


def _cfg(enabled: bool) -> _Cfg:
    return _Cfg({"fusion.tools_permission_gate": enabled})


# --------------------------------------------------------------------- 叠加检测: 始终生效


def test_sensitive_write_upgraded_to_confirm():
    tool = _Tool(name="write_file", read_only=False, dangerous=False)
    pol = FusionPermissionPolicy(PermissionPolicy(_cfg(False)), _cfg(False))
    d = pol.decide(tool, {"path": "/home/u/.ssh/id_rsa", "content": "k"})
    assert d.action == "confirm"
    assert "敏感文件" in d.reason


def test_non_sensitive_write_still_allows():
    # 非敏感路径的写工具 -> 原生 allow 透传。
    tool = _Tool(read_only=False, dangerous=False)
    pol = FusionPermissionPolicy(PermissionPolicy(_cfg(False)), _cfg(False))
    d = pol.decide(tool, {"path": "/tmp/hello.txt"})
    assert d.action == "allow"


def test_sensitive_path_nested_in_structure():
    tool = _Tool(name="write_file", read_only=False, dangerous=False)
    pol = FusionPermissionPolicy(PermissionPolicy(_cfg(False)), _cfg(False))
    args = {"files": [{"target": "/etc/nginx/id_ed25519"}, {"target": "/tmp/ok.txt"}]}
    d = pol.decide(tool, args)
    assert d.action == "confirm"
    assert "/etc/nginx/id_ed25519" in d.reason


def test_read_tool_sensitive_not_upgraded():
    # 读类工具 (read_only=True) 触及敏感文件仍保持原生 allow, 避免过问。
    tool = _Tool(name="read_file", read_only=True, dangerous=False)
    pol = FusionPermissionPolicy(PermissionPolicy(_cfg(False)), _cfg(False))
    d = pol.decide(tool, {"path": "/home/u/.ssh/id_rsa"})
    assert d.action == "allow"


def test_deny_not_downgraded():
    base = PermissionDecision("deny", "命中拒绝规则")
    pol = FusionPermissionPolicy(_FixedPolicy(base), _cfg(False))
    d = pol.decide(_Tool(), {"path": "/home/u/.ssh/id_rsa"})
    assert d.action == "deny"


def test_confirm_passthrough():
    base = PermissionDecision("confirm", "危险工具需要确认")
    pol = FusionPermissionPolicy(_FixedPolicy(base), _cfg(False))
    d = pol.decide(_Tool(), {"path": "/home/u/.ssh/id_rsa"})
    assert d.action == "confirm"


# --------------------------------------------------------------------- 工厂


def test_build_factory_always_wraps():
    pol = build_permission_policy(_cfg(False))
    assert isinstance(pol, FusionPermissionPolicy)  # 始终包裹, 敏感文件检测始终生效

def test_build_factory_sensitive_detected():
    pol = build_permission_policy(_cfg(False))
    assert isinstance(pol, FusionPermissionPolicy)
    tool = _Tool(name="write_file", read_only=False, dangerous=False)
    d = pol.decide(tool, {"path": "/home/u/.ssh/id_rsa"})
    assert d.action == "confirm"
