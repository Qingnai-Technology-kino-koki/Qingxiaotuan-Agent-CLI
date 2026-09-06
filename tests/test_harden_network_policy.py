"""网络出口加固测试 (CIDR 出口白名单 + 单向模式)。"""
from qingxiaotuan.harden.network_policy import EgressRestrictedNetworkGuard


def test_egress_cidr_denies_outside():
    g = EgressRestrictedNetworkGuard(egress_cidr_allow=["10.0.0.0/8"])
    d = g.check("curl http://192.168.1.1/secret")
    assert d.action == "deny"
    assert "白名单" in d.reasons[0]


def test_egress_cidr_allows_inside():
    g = EgressRestrictedNetworkGuard(egress_cidr_allow=["10.0.0.0/8"])
    d = g.check("curl http://10.1.2.3/file")
    assert d.action == "allow"


def test_egress_cidr_domain_requires_confirm():
    g = EgressRestrictedNetworkGuard(egress_cidr_allow=["10.0.0.0/8"])
    d = g.check("curl https://example.com/api")
    # 仅域名、无显式 IP -> 升级确认 (不做 DNS 误判)
    assert d.action == "confirm"
    assert d.detected_domains == ["example.com"]


def test_no_egress_restriction_keeps_base_behavior():
    g = EgressRestrictedNetworkGuard()  # 不开启 CIDR 限制
    d = g.check("curl https://example.com")
    assert d.action in ("allow", "confirm")


def test_one_way_blocks_listen():
    g = EgressRestrictedNetworkGuard(one_way_mode=True)
    assert g.check("python -m http.server 8000").action == "deny"
    assert g.check("nc -l 1234").action == "deny"


def test_one_way_allows_egress():
    g = EgressRestrictedNetworkGuard(one_way_mode=True)
    # 纯出站 GET 不被单向模式误伤
    assert g.check("curl https://example.com").action != "deny"


def test_describe_policy():
    g = EgressRestrictedNetworkGuard(egress_cidr_allow=["10.0.0.0/8"], one_way_mode=True)
    pol = g.describe_policy()
    assert pol["one_way_mode"] is True
    assert "10.0.0.0/8" in pol["egress_cidr_allow"]
    assert pol["egress_restricted"] is True
