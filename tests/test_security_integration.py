"""安全加固集成测试: core egress/one-way 接入 shell 路径 + 总线默认落盘。"""
from pathlib import Path

from qingxiaotuan.core.network_guard import (
    NetworkGuard,
    get_network_guard,
    set_network_guard,
)
from qingxiaotuan.core.security_bus import get_security_bus
from qingxiaotuan.harden.network_policy import EgressRestrictedNetworkGuard


def test_core_egress_denies_outside_cidr():
    g = NetworkGuard(egress_cidr_allow=["10.0.0.0/8"])
    d = g.check("curl http://192.168.1.1/secret")
    assert d.action == "deny"
    assert "白名单" in d.reasons[0]


def test_core_egress_allows_inside_cidr():
    g = NetworkGuard(egress_cidr_allow=["10.0.0.0/8"])
    d = g.check("curl http://10.1.2.3/file")
    assert d.action == "allow"


def test_core_egress_domain_requires_confirm():
    g = NetworkGuard(egress_cidr_allow=["10.0.0.0/8"])
    d = g.check("curl https://example.com/api")
    assert d.action == "confirm"
    assert d.detected_domains == ["example.com"]


def test_core_one_way_blocks_listen():
    g = NetworkGuard(one_way_mode=True)
    assert g.check("python -m http.server 8000").action == "deny"
    assert g.check("nc -l 1234").action == "deny"
    assert g.check("socat TCP-LISTEN:4444,fork -").action == "deny"
    assert g.check("ssh -D 1080 user@host").action == "deny"


def test_core_one_way_allows_egress():
    g = NetworkGuard(one_way_mode=True)
    assert g.check("curl https://example.com").action != "deny"


def test_no_egress_keeps_base_behavior():
    g = NetworkGuard()
    d = g.check("curl https://example.com")
    assert d.action in ("allow", "confirm")


def test_set_network_guard_takes_effect_in_shell_path():
    # tools.shell 通过 get_network_guard() 获取守卫; 注入后应当生效
    custom = NetworkGuard(one_way_mode=True)
    try:
        set_network_guard(custom)
        assert get_network_guard() is custom
        assert get_network_guard().check("ssh -D 1080 user@host").action == "deny"
    finally:
        set_network_guard(NetworkGuard())


def test_security_bus_default_persists():
    # 未显式传路径时, 全局总线默认落盘 (非空路径)
    bus = get_security_bus()
    assert bus._persist_path is not None
    assert isinstance(bus._persist_path, Path)


def test_security_bus_global_is_singleton():
    a = get_security_bus()
    b = get_security_bus()
    assert a is b


def test_harden_subclass_delegates_to_core():
    g = EgressRestrictedNetworkGuard(
        egress_cidr_allow=["10.0.0.0/8"], one_way_mode=True
    )
    pol = g.describe_policy()
    assert pol["one_way_mode"] is True
    assert "10.0.0.0/8" in pol["egress_cidr_allow"]
    assert pol["egress_restricted"] is True
    # 行为仍由 core 提供
    assert g.check("nc -l 1").action == "deny"
    assert g.check("curl http://8.8.8.8").action == "deny"
