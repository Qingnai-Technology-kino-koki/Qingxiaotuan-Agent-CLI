"""SandboxManager: 4 层滤网流水线的统一入口。

职责:
  - 按配置构建或复用一条 SandboxFilterChain (L0 意图 → L1 信任 → L2 资源 → L3 强隔离)
  - assess_tool / assess_command: 把工具或命令归一化成 Payload, 跑完整滤网, 返回裁决
  - execute_isolated: 当裁决要求隔离(isolate)时, 用选中的后端真正隔离执行
  - 全工具统一入口(在 ToolExecutor 层), 杜绝"绕道文件工具/run_tests 绕过 shell 护栏"的侧门

对 run_shell: Shell 内部已有完整的确认/红线链; 本层只做"是否需强隔离 + 隔离执行"的选路,
不重复它的确认 —— 因此 confirm 语义由调用方(Shell 或 ToolExecutor)按现有通道消费。
"""
from __future__ import annotations

import logging
import os
import subprocess
import time as _time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .verdict import Action, Payload, Verdict, SEV
from .filters import SandboxFilterChain, build_chain
from .backends import pick_backend

log = logging.getLogger(__name__)

DEFAULT_POLICY: Dict[str, Any] = {
    "enabled": True,
    "backend": "auto",                 # auto | local | docker | landlock | seatbelt | jobobject
    "enforce_required": False,         # 强隔离场景无强后端时是否 fail-closed 拒绝
    "deny_unknown_shell": False,
    "resource": {
        "deny_network_by_default": False,
        "max_memory_mb": 0,
        "max_timeout": 0,
        "isolate_copy_threshold": "high",   # 高危以上走 copy-diff-apply
    },
}

# 工具名 → 方法类别 (供信任/计划模式滤网判断"只读")
_TOOL_METHOD = {
    "run_shell": "shell",
    "run_tests": "code", "git_status": "code", "git_diff": "code",
    "git_log": "code", "git_stash": "code", "codebase_map": "code",
    "write_file": "write", "edit_file": "write", "str_replace": "write",
    "delete_file": "write", "delete_dir": "write", "move_file": "write",
    "read_file": "read", "search_files": "read", "list_dir": "read", "glob": "read",
}

# 可执行/敏感文本字段 (从工具参数里抽出来喂意图滤网)
_EXEC_FIELDS = (
    "command", "code", "sql", "script", "query", "content", "text", "input", "url", "shell",
)


@dataclass
class SandboxExecResult:
    """隔离执行返回。enforced=False 表示实际回落到非隔离执行。"""

    stdout: str = ""
    stderr: str = ""
    returncode: int = 0
    ignored: bool = False        # 隔离执行不可用/被跳过 → 调用方维持原执行
    backend: str = ""
    enforced: bool = False
    error: str = ""

    def as_shell_text(self) -> str:
        parts = []
        if self.returncode == -1:
            parts.append("exit=-1 (被终止)")
        if self.stdout:
            parts.append(self.stdout.rstrip())
        if self.stderr:
            parts.append(self.stderr.rstrip())
        return "\n".join(parts)


class SandboxManager:
    """统一 4 层滤网入口。"""

    def __init__(self, policy: Optional[Dict[str, Any]] = None,
                 auditor: Any = None, bus: Any = None) -> None:
        self.policy = _merge_policy(policy or {})
        self.chain: SandboxFilterChain = build_chain(self.policy) if self.policy.get("enabled", True) else SandboxFilterChain()
        self._disabled = not self.policy.get("enabled", True)
        self._auditor = auditor       # SecurityAuditor (可选): 每条裁决直写审计
        self._bus = bus               # SecurityEventBus (可选): 沙箱事件汇入统一审计流

    # ------------------------------------------------------------ 工具参数归一化
    @staticmethod
    def _method_for(fn_name: str) -> str:
        if fn_name.startswith("mcp__"):
            return "mcp"
        if fn_name.startswith("web_"):
            return "web"
        return _TOOL_METHOD.get(fn_name, "other")

    @staticmethod
    def _extract_text(fn_args: Any) -> List[str]:
        if isinstance(fn_args, str):
            try:
                import json as _json
                fn_args = _json.loads(fn_args) if fn_args.strip().startswith(("{", "[")) else {"command": fn_args}
            except Exception:
                fn_args = {"command": fn_args}
        if not isinstance(fn_args, dict):
            return []
        out: List[str] = []
        for key, val in fn_args.items():
            if key in _EXEC_FIELDS and isinstance(val, str) and val.strip():
                out.append(val)
        # 兜底: 任意字段里递归出现的整段可执行文本
        for val in fn_args.values():
            if isinstance(val, str) and val.strip().count("\n") >= 2:
                out.append(val)
        return list(dict.fromkeys(out))

    @staticmethod
    def _command_of(fn_args: Any) -> str:
        if isinstance(fn_args, dict):
            c = fn_args.get("command")
            return c if isinstance(c, str) else ""
        return ""

    # ------------------------------------------------------------ 审计闭环
    def _emit_audit(self, verdict: Verdict, payload: Payload) -> None:
        """把沙箱裁决汇入审计流 (仅当挂了 auditor / bus 时才落, 保证可移植/无副作用)。

        DENY 全量记录; CONFIRM / ISOLATE 仅在高危以上记一条 (避免噪音)。
        任何异常都吞掉 —— 审计是补充保障, 绝不反噬沙箱主流程。
        """
        if not (self._auditor is not None or self._bus is not None):
            return
        notable = verdict.blocks or verdict.severity in ("high", "critical")
        if not notable:
            return
        command = payload.command or (payload.text[0] if payload.text else "")
        command = (command or "")[:500]
        reasons = ([verdict.reason] if verdict.reason else []) + list(verdict.suggestions)
        context = {
            "layer": verdict.layer,
            "tool": payload.tool_name,
            "method": payload.method,
            "trust_level": payload.trust_level,
            "workspace_mode": verdict.workspace_mode,
            "backend": verdict.backend,
            "network": verdict.network,
            "plan_mode": payload.plan_mode,
        }
        try:
            if self._auditor is not None:
                self._auditor.record(
                    module="sandbox", action=verdict.action.value,
                    severity=verdict.severity, input_summary=command,
                    reasons=reasons, context=context,
                )
            if self._bus is not None:
                if verdict.blocks:
                    self._bus.emit_sandbox_blocked(
                        command, verdict.layer, verdict.reason,
                        severity=verdict.severity, suggestions=list(verdict.suggestions),
                    )
                else:
                    from ..core.security_bus import SecurityEvent, SecurityEventType
                    etype = (SecurityEventType.SANDBOX_ISOLATED if verdict.isolate
                             else SecurityEventType.SANDBOX_CONFIRMED)
                    self._bus.emit(SecurityEvent(
                        event_type=etype, timestamp=_time.time(),
                        payload={"command": command, "layer": verdict.layer,
                                 "reason": verdict.reason},
                        source="sandbox", severity=verdict.severity,
                    ))
        except Exception:  # noqa: BLE001 - 审计失败不影响主流程
            pass

    # ------------------------------------------------------------ 主评估
    def assess(self, payload: Payload) -> Verdict:
        if self._disabled:
            return Verdict()
        try:
            verdict = self.chain.evaluate(payload)
        except Exception as exc:  # noqa: BLE001 - 滤网自身故障必须 fail-closed
            log.error("沙箱滤网异常: %s", exc)
            verdict = Verdict(Action.DENY, layer="base", severity=SEV["critical"],
                              reason=f"沙箱评估内部故障, fail-closed ({type(exc).__name__})")
        self._emit_audit(verdict, payload)
        return verdict

    def assess_tool(self, fn_name: str, fn_args: Any, ctx: Any) -> Tuple[bool, str]:
        """全工具统一入口: 返回 (denied, block_msg)。供 ToolExecutor 调用。"""
        if self._disabled:
            return False, ""
        texts = self._extract_text(fn_args)
        command = self._command_of(fn_args)
        if not texts and not command:
            return False, ""
        payload = Payload(
            tool_name=fn_name,
            text=texts,
            command=command,
            method=self._method_for(fn_name),
            workspace=getattr(ctx, "workspace", ""),
            trust_level=getattr(ctx, "workspace_trust_level", getattr(ctx, "trust_level", None)),
            plan_mode=bool(getattr(ctx, "plan_mode", False)),
            yolo=bool(getattr(ctx, "yolo", False)),
            require_confirm=not getattr(ctx, "yolo", False),
            meta={
                "severity": getattr(ctx, "safety_severity", None),
                "allowed_domains": self._allowed_domains(ctx),
            },
        )
        verdict = self.assess(payload)
        if verdict.blocks:
            return True, verdict.describe()
        if verdict.action == Action.CONFIRM and not payload.yolo:
            confirm = getattr(ctx, "confirm", None)
            if confirm is None:
                return True, f"[沙箱信任滤网] 需要人工确认但没有确认通道: {verdict.reason}"
            try:
                if not confirm(f"[沙箱确认] {verdict.reason}\n请确认是否继续?"):
                    return True, f"[沙箱信任滤网] 用户拒绝: {verdict.reason}"
            except Exception:
                return True, f"[沙箱信任滤网] 确认通道异常, fail-closed: {verdict.reason}"
        return False, ""

    def assess_command(self, command: str, ctx: Any) -> Verdict:
        """对单条命令做完整滤网(供 run_shell 的隔离选路)。"""
        if self._disabled:
            return Verdict()
        payload = Payload(
            tool_name="run_shell",
            text=[command],
            command=command,
            method="shell",
            workspace=getattr(ctx, "workspace", ""),
            trust_level=getattr(ctx, "workspace_trust_level", getattr(ctx, "trust_level", None)),
            plan_mode=bool(getattr(ctx, "plan_mode", False)),
            yolo=bool(getattr(ctx, "yolo", False)),
            require_confirm=True,
            meta={"severity": getattr(ctx, "safety_severity", None),
                  "allowed_domains": self._allowed_domains(ctx)},
        )
        return self.assess(payload)

    # ------------------------------------------------------------ 隔离执行
    def should_isolate(self, verdict: Verdict) -> bool:
        return verdict.isolate

    def execute_isolated(self, command: str, verdict: Verdict, ctx: Any,
                         timeout: float = 60.0) -> SandboxExecResult:
        """当裁决要求隔离时, 用选中后端真正执行命令。

        支持: jobobject(win)/landlock(bwrap) 走 OS 原语; docker/seatbelt 走 provider;
        local 兜底(非强制隔离, enforced=False, 由调用方策略决定是否接受)。
        """
        if not verdict.isolate:
            return SandboxExecResult()
        cwd = getattr(ctx, "workspace", None) or "."
        backend = verdict.backend or pick_backend("auto")[0]
        block_net = not verdict.network

        if backend in ("jobobject",) and os.name == "nt":
            return self._run_syscall(command, timeout, block_net, cwd, backend)
        if backend == "landlock":
            return self._run_provider("landlock", command, cwd, timeout, block_net, verdict)
        if backend == "docker":
            return self._run_provider("docker", command, cwd, timeout, block_net, verdict)
        # local / 其它: 裸跑(非 OS 级隔离), 交由上层 enforce 决定是否接受
        try:
            proc = subprocess.run(
                command, shell=True, cwd=cwd,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, timeout=timeout or None, errors="replace",
            )
            return SandboxExecResult(stdout=proc.stdout or "", stderr=proc.stderr or "",
                                     returncode=proc.returncode or 0,
                                     backend=backend, enforced=False)
        except subprocess.TimeoutExpired:
            return SandboxExecResult(returncode=-1, backend=backend,
                                     enforced=False, error="超时")
        except Exception as exc:
            return SandboxExecResult(returncode=-1, backend=backend,
                                     enforced=False, error=str(exc))

    @staticmethod
    def _run_syscall(command: str, timeout: float, block_net: bool, cwd: str, backend: str) -> SandboxExecResult:
        try:
            from ..arch.platform import run_syscall_sandboxed
            res = run_syscall_sandboxed(
                ["cmd", "/c", command] if os.name == "nt" else ["/bin/sh", "-c", command],
                timeout=timeout or 30.0, block_network=block_net, cwd=cwd or None,
            )
            return SandboxExecResult(stdout=res.stdout or "", stderr=res.stderr or "",
                                     returncode=res.returncode, backend=res.mechanism or backend,
                                     enforced=True, error="blocked" if res.blocked else "")
        except Exception as exc:
            return SandboxExecResult(returncode=-1, backend=backend,
                                     enforced=False, error=str(exc))

    @staticmethod
    def _run_provider(backend: str, command: str, cwd: str, timeout: float,
                      block_net: bool, verdict: Verdict) -> SandboxExecResult:
        try:
            from ..core.sandbox_provider import SandboxProvider, SandboxProfile
            provider = SandboxProvider.create(backend)
            profile = SandboxProfile(network=verdict.network,
                                     max_memory_mb=verdict.memory_mb)
            result = provider.run(["/bin/sh", "-c", command] if os.name != "nt" else ["cmd", "/c", command],
                                  workspace=cwd, profile=profile, timeout=timeout or 60.0)
            return SandboxExecResult(stdout=result.stdout, stderr=result.stderr,
                                     returncode=result.returncode,
                                     backend=backend, enforced=result.enforced,
                                     error=result.error)
        except Exception as exc:
            return SandboxExecResult(returncode=-1, backend=backend,
                                     enforced=False, error=str(exc))

    # ------------------------------------------------------------ 配置
    def _allowed_domains(self, ctx: Any) -> Optional[List[str]]:
        # 优先取沙箱策略自带白名单 (sandbox.allowed_domains), 保证无 kernel 也可用、可测;
        # 否则回落 kernel 配置 (permissions.network.allow_domains 优先, sandbox.allowed_domains 兜底)。
        p_ad = self.policy.get("allowed_domains")
        if p_ad:
            return list(p_ad)
        try:
            cfg = ctx.kernel.get("config")
            if cfg is not None:
                ad = cfg.get("permissions.network.allow_domains", [])
                if ad:
                    return list(ad)
                sd = cfg.get("sandbox.allowed_domains", [])
                if sd:
                    return list(sd)
        except Exception:  # pragma: no cover
            pass
        return None

    @classmethod
    def from_kernel(cls, kernel: Any) -> "SandboxManager":
        cfg = None
        auditor = None
        try:
            cfg = kernel.get("config")
        except Exception:  # pragma: no cover
            pass
        try:
            auditor = kernel.get("security_auditor")
        except Exception:  # pragma: no cover
            auditor = None
        policy = cfg.get("sandbox", {}) if cfg is not None else {}
        bus = None
        try:
            from ..core.security_bus import get_security_bus
            bus = get_security_bus()
        except Exception:  # pragma: no cover
            bus = None
        return cls(policy, auditor=auditor, bus=bus)


def _merge_policy(raw: Dict[str, Any]) -> Dict[str, Any]:
    merged = json_deep_merge(DEFAULT_POLICY, raw)
    return merged


def json_deep_merge(base: Dict[str, Any], over: Dict[str, Any]) -> Dict[str, Any]:
    import copy
    out = copy.deepcopy(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = json_deep_merge(out[k], v)
        else:
            out[k] = v
    return out