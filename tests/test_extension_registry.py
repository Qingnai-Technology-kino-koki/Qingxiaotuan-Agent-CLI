"""分层扩展注册表 + 内核 provide_multi 增强叠加语义测试。"""

from __future__ import annotations

import pytest

from qingxiaotuan.core.kernel import AggregateProxy, PluginError, ServiceContainer
from qingxiaotuan.ext.extension_registry import (
    ExtensionRegistry,
    OnError,
    get_registry,
    register,
)


class FakeBase:
    def score(self, cmd):
        return {"base": cmd}


class FakePre:
    def score(self, cmd):
        return {"pre": cmd}


class FakePost:
    def score(self, cmd):
        return {"post": cmd}


class BrokenPre:
    def score(self, cmd):
        raise RuntimeError("boom")


# --------------------------------------------------------------- registry

def test_register_and_resolve_layers():
    reg = ExtensionRegistry()
    reg.register("safety.score", FakeBase(), layer="builtin")
    reg.register("safety.score", FakePre(), placement="pre")
    reg.register("safety.score", FakePost(), placement="post")
    res = reg.resolve("safety.score")
    assert [type(c.runtime()) for c in res.pre] == [FakePre]
    assert isinstance(res.base.runtime(), FakeBase)
    assert [type(c.runtime()) for c in res.post] == [FakePost]


def test_invoke_runs_pre_base_post_in_order():
    reg = ExtensionRegistry()
    reg.register("safety.score", FakeBase(), layer="builtin")
    reg.register("safety.score", FakePre(), placement="pre")
    reg.register("safety.score", FakePost(), placement="post")
    results = reg.invoke("safety.score", "score", "ls")
    assert sorted(results[0].keys())[0] == "pre"
    assert sorted(results[1].keys())[0] == "base"
    assert sorted(results[2].keys())[0] == "post"
    assert results[-1]["post"] == "ls"


def test_guarded_fail_closed_for_security():
    reg = ExtensionRegistry(on_error=OnError.DENY)
    reg.register("security.gate", FakeBase(), layer="builtin", kind="security")
    reg.register("security.gate", BrokenPre(), placement="pre", kind="security")
    with pytest.raises(RuntimeError):
        reg.guarded("security.gate", "score")("ls")


def test_guarded_degrades_for_capability():
    reg = ExtensionRegistry(on_error=OnError.DEGRADE)
    reg.register("diff.run", FakeBase(), layer="builtin")
    reg.register("diff.run", BrokenPre(), placement="pre")
    res = reg.guarded("diff.run", "score")("ls")
    assert res == {"base": "ls"}


def test_security_cannot_be_replaced():
    reg = ExtensionRegistry()
    with pytest.raises(ValueError):
        reg.register("security.gate", FakeBase(), kind="security", placement="replace")


def test_default_registry_register_then_resolve():
    # 使用独立注册表避免污染全局
    reg = get_registry()
    reg.register("test.dummy", FakePost(), placement="post")
    try:
        assert len(reg.resolve("test.dummy").post) == 1
    finally:
        with reg._lock:
            reg._contrib.pop("test.dummy", None)


# ------------------------------------------------------ kernel extend_service

def test_kernel_single_contributor_returns_instance():
    c = ServiceContainer()
    base = FakeBase()
    c.extend_service("svc", base)
    assert c.get("svc") is base


def test_kernel_multi_contributor_returns_aggregate():
    c = ServiceContainer()
    c.extend_service("svc", FakeBase(), owner="base")
    c.extend_service("svc", FakePre(), owner="pre", placement="pre")
    c.extend_service("svc", FakePost(), owner="post", placement="post")
    svc = c.get("svc")
    assert isinstance(svc, AggregateProxy)
    assert svc.contributions_count() == 3
    # 执行顺序 pre->base->post, 首个成功者胜出
    assert svc.score("hi") == {"pre": "hi"}
    # 方法失败回退到下一贡献者
    c2 = ServiceContainer()
    c2.extend_service("svc", FakeBase(), owner="base")
    c2.extend_service("svc", BrokenPre(), owner="pre", placement="pre")
    assert c2.get("svc").score("x") == {"base": "x"}


def test_kernel_security_aggregate_fail_closed():
    c = ServiceContainer()
    c.extend_service("security_gate", BrokenPre(), owner="pre", placement="pre")
    c.extend_service("security_gate", FakeBase(), owner="base")
    with pytest.raises(RuntimeError):
        c.get("security_gate").score("rm")


def test_kernel_contributions_and_unprovide():
    c = ServiceContainer()
    c.extend_service("svc", FakeBase(), owner="base")
    c.extend_service("svc", FakePost(), owner="post")
    assert len(c.contributions("svc")) == 2
    c.unprovide("svc")
    assert c.get("svc") is None
    assert c.contributions("svc") == []


def test_kernel_provide_override_still_raises():
    # 不影响既有 provide 语义
    c = ServiceContainer()
    c.provide("svc", FakeBase())
    with pytest.raises(PluginError):
        c.provide("svc", FakeBase())