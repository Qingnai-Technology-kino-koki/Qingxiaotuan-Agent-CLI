"""Tests for arch/plugin.py — ArchPlugin activation and service registration."""

import pytest

from qingxiaotuan.arch.plugin import ArchPlugin
from qingxiaotuan.core.kernel import Kernel


class TestArchPlugin:
    def test_plugin_metadata(self):
        """ArchPlugin 应有正确的元数据。"""
        p = ArchPlugin()
        assert p.name == "arch"
        assert "arch" in p.provides
        assert "arch.security" in p.provides
        assert "arch.execution" in p.provides
        assert "arch.orchestration" in p.provides
        assert "arch.context" in p.provides
        assert "arch.observability" in p.provides

    def test_activate_registers_services(self):
        """激活后应向 kernel 注册五层服务。"""
        kernel = Kernel()
        # ArchPlugin 需要 config 服务
        from qingxiaotuan.config import Config
        kernel.provide("config", Config())

        plugin = ArchPlugin()
        kernel.register(plugin)
        kernel.activate_all()

        # 验证五层服务已注册
        assert kernel.get("arch") is not None
        assert kernel.get("arch.security") is not None
        assert kernel.get("arch.execution") is not None
        assert kernel.get("arch.orchestration") is not None
        assert kernel.get("arch.context") is not None
        assert kernel.get("arch.observability") is not None

    def test_arch_service_has_all_layers(self):
        """arch 聚合服务应包含五层。"""
        kernel = Kernel()
        from qingxiaotuan.config import Config
        kernel.provide("config", Config())

        plugin = ArchPlugin()
        kernel.register(plugin)
        kernel.activate_all()

        arch = kernel.require("arch")
        assert "security" in arch
        assert "execution" in arch
        assert "orchestration" in arch
        assert "context" in arch
        assert "observability" in arch

    def test_security_layer_has_classes(self):
        """安全层应包含 SyscallSandbox / ImmutableSecurityPolicy / CryptoVault。"""
        kernel = Kernel()
        from qingxiaotuan.config import Config
        kernel.provide("config", Config())

        plugin = ArchPlugin()
        kernel.register(plugin)
        kernel.activate_all()

        sec = kernel.require("arch.security")
        assert "SyscallSandbox" in sec
        assert "ImmutableSecurityPolicy" in sec
        assert "CryptoVault" in sec

    def test_execution_layer_has_classes(self):
        """执行层应包含 SemanticBus / ToolPipeline / EventSemantics。"""
        kernel = Kernel()
        from qingxiaotuan.config import Config
        kernel.provide("config", Config())

        plugin = ArchPlugin()
        kernel.register(plugin)
        kernel.activate_all()

        exec_ = kernel.require("arch.execution")
        assert "SemanticBus" in exec_
        assert "ToolPipeline" in exec_
        assert "EventSemantics" in exec_
        assert "LoopRegistry" in exec_
