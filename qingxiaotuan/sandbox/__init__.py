"""系统级 4 层沙箱滤网子系统。

超越 TraeWork / CodeX 的"单容器单层"方案: 以确定性滤网栈替代"运行时碰运气"。

    L0 Intent   意图滤网   静态识别致命红线 / 网络外泄 / 命令语言类别               (模型无关)
    L1 Trust    信任滤网   工作区信任分级 + 计划模式 + 域名白名单                   (模型无关)
    L2 Resource 资源滤网   网络开关 / 内存超时 / 工作区隔离模式(direct / 副本→diff→apply)
    L3 Hard     强隔离滤网  隔离后端自动选择(docker→bwrap→seatbelt→jobobject→local), fail-closed

三层特性使它区别于主流 Agent 沙箱:
  1. 确定性兜底 —— L0/L1 不依赖任何内核/容器, 缺隔离运行时也成立;
  2. 自动后端 —— 不强绑单一运行时, 拿到当前机器最强可用隔离;
  3. 每层都是滤网 —— 任何一层都能拒绝, 只许收窄不许放宽。
"""
from __future__ import annotations

from .verdict import Action, Payload, Verdict, SEV
from .filters import IntentFilter, TrustFilter, ResourceFilter, HardIsolationFilter, SandboxFilterChain, build_chain
from .backends import pick_backend, detect_backends
from .isolation import Snapshot, IsolatedWorkdir, isolate_workdir, resolve_in_workspace, plan_isolation
from .manager import SandboxManager, SandboxExecResult, DEFAULT_POLICY

__all__ = [
    "Action", "Payload", "Verdict", "SEV",
    "IntentFilter", "TrustFilter", "ResourceFilter", "HardIsolationFilter",
    "SandboxFilterChain", "build_chain",
    "pick_backend", "detect_backends",
    "Snapshot", "IsolatedWorkdir", "isolate_workdir", "resolve_in_workspace", "plan_isolation",
    "SandboxManager", "SandboxExecResult", "DEFAULT_POLICY",
]