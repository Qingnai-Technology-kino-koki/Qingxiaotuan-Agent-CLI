"""把五层架构注册为内核一等公民服务。

激活后, 其它插件/命令可用::

    arch   = kernel.require("arch")          # 五个层的工厂集合
    sec    = kernel.require("arch.security") # SyscallSandbox / ImmutableSecurityPolicy / CryptoVault
    exec_  = kernel.require("arch.execution")# LoopRegistry / SemanticBus / ToolPipeline / EventSemantics
    orch   = kernel.require("arch.orchestration")
    ctx    = kernel.require("arch.context")
    obs    = kernel.require("arch.observability")
"""

from __future__ import annotations

from ..core.kernel import Kernel, Plugin


class ArchPlugin(Plugin):
    name = "arch"
    version = "0.1.0"
    provides = [
        "arch",
        "arch.security",
        "arch.execution",
        "arch.orchestration",
        "arch.context",
        "arch.observability",
    ]
    requires: list = []

    def activate(self, kernel: Kernel) -> None:
        from .security import CryptoVault, ImmutableSecurityPolicy, SyscallSandbox
        from .execution import (
            EventSemantics,
            LoopRegistry,
            SemanticBus,
            ToolPipeline,
        )
        from .orchestration import (
            ConflictResolver,
            HumanApprovalGate,
            Orchestrator,
        )
        from .context import (
            ContextEventLog,
            SkillDistiller,
            TieredContext,
            fork_and_replay,
        )
        from .observability import (
            CrossSessionAttributor,
            StepViewer,
            TrajectoryStore,
        )

        kernel.provide("arch.security", {
            "SyscallSandbox": SyscallSandbox,
            "ImmutableSecurityPolicy": ImmutableSecurityPolicy,
            "CryptoVault": CryptoVault,
        }, owner=self.name)
        kernel.provide("arch.execution", {
            "EventSemantics": EventSemantics,
            "LoopRegistry": LoopRegistry,
            "SemanticBus": SemanticBus,
            "ToolPipeline": ToolPipeline,
        }, owner=self.name)
        kernel.provide("arch.orchestration", {
            "Orchestrator": Orchestrator,
            "HumanApprovalGate": HumanApprovalGate,
            "ConflictResolver": ConflictResolver,
        }, owner=self.name)
        kernel.provide("arch.context", {
            "TieredContext": TieredContext,
            "ContextEventLog": ContextEventLog,
            "SkillDistiller": SkillDistiller,
            "fork_and_replay": fork_and_replay,
        }, owner=self.name)
        kernel.provide("arch.observability", {
            "TrajectoryStore": TrajectoryStore,
            "StepViewer": StepViewer,
            "CrossSessionAttributor": CrossSessionAttributor,
        }, owner=self.name)
        kernel.provide("arch", {
            "security": kernel.require("arch.security"),
            "execution": kernel.require("arch.execution"),
            "orchestration": kernel.require("arch.orchestration"),
            "context": kernel.require("arch.context"),
            "observability": kernel.require("arch.observability"),
        }, owner=self.name)
