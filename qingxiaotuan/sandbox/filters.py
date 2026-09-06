"""4 层沙箱滤网 + 编排链。

每一层都是独立滤网, 对一次执行请求(Payload)输出 Verdict, 只许"收紧"不许"放宽":
    L0 Intent  意图滤网   确定性识别致命红线 / 网络外泄 / 命令语言类别      (静态, 无副作用)
    L1 Trust   信任滤网   工作区信任分级 + 计划模式 + 域名白名单            (静态)
    L2 Resource 资源滤网   网络开关 / 内存/超时 / 工作区隔离模式选择         (元数据)
    L3 Hard    强隔离滤网  隔离后端自动选择(docker→bwrap→jobobject→local)  (执行策略, fail-closed)

对比单层"丢容器"方案的价值: L0/L1 不依赖任何内核/容器, 在命令进入任何运行时之前,
已把致命命令与越权写入判定为 deny —— 即便机器上没有 docker/bwrap/seccomp,
前两层滤网依然成立。L3 只是"锦上添花的真正隔离", 而 L2 决定该不该上这道锦上添花。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .verdict import Action, Payload, SEV, Verdict, deny, confirm, isolate

log = logging.getLogger(__name__)


# ----------------------------------------------------------------- 工具: 文本归并
def _all_benign(texts: List[str]) -> bool:
    """texts 里每个有意义的命令串都判为良性。"""
    try:
        from ..ext.safety_engine import is_benign_dev_command
    except Exception:  # pragma: no cover
        return False
    for t in texts:
        t = (t or "").strip()
        if not t:
            continue
        try:
            if not is_benign_dev_command(t):
                return False
        except Exception:  # noqa: BLE001 - 无法评估按非良性处理
            return False
    return True


def _has_network_request(texts: List[str]) -> bool:
    """任一命令串带网络获取/上传/传输/监听特征。"""
    try:
        from ..core.network_guard import get_network_guard
        guard = get_network_guard()
        for t in texts:
            t = (t or "").strip()
            if not t:
                continue
            dec = guard.check(t)
            if getattr(dec, "detected_domains", None) or getattr(dec, "risk_level", "") in ("high", "medium"):
                return True
    except Exception:  # pragma: no cover
        pass
    return False


# ================================================================ L0 意图滤网
class IntentFilter:
    """L0 意图滤网: 全静态、确定性最强、模型无关。

    职责:
      1) 命中致命红线 (文件系统/OS 级毁灭, 永不自动执行) → deny critical
      2) 命中数据外泄 / 远程执行 (curl|sh 等) → deny high
      3) 全部良性 (ls/git status/pytest/echo...) → allow (交后续信任滤网继续)
      4) 其它非良性 → 交 L1/L2 决定 (不在此越权 deny, 避免与 shell 既有确认链重复)
    """

    name = "intent"

    def check(self, payload: Payload) -> Optional[Verdict]:
        texts = payload.text or ([payload.command] if payload.command else [])
        if not texts:
            return None
        try:
            from ..ext.safety_engine import is_hard_redline
        except Exception:  # pragma: no cover
            is_hard_redline = None

        critical = False
        if is_hard_redline is not None:
            for t in texts:
                if t.strip() and is_hard_redline(t):
                    critical = True
                    break
        if critical:
            return deny(self.name, "命中致命红线(文件系统/OS级毁灭操作), 永不自动执行",
                        SEV["critical"])

        # 数据外泄 / 远程执行: 网络门控判定为 deny 的红线
        try:
            from ..core.network_guard import get_network_guard
            guard = get_network_guard()
            for t in texts:
                if not t.strip():
                    continue
                dec = guard.check(t)
                if getattr(dec, "action", "") == "deny":
                    return deny(self.name,
                                "; ".join(getattr(dec, "reasons", []) or ["命中网络外泄/远程执行红线"]),
                                SEV["high"])
                    # 这里取整条暂不默认隔离;风险判断交给配置门
        except Exception:  # pragma: no cover
            pass

        if _all_benign(texts):
            return Verdict(Action.ALLOW, layer=self.name, severity=SEV["low"],
                           reason="命令为良性开发操作")

        # 非良性但未致命: 不在此 deny, 交给 trust 滤网按工作区信任裁决
        return None


# ================================================================ L1 信任滤网
class TrustFilter:
    """L1 信任滤网: 工作区信任分级(fail-closed) + 计划模式 + 域名白名单。

    trusted 工作区直接放行(用户显式信任); untrusted 一律 deny;
    unknown / limited 的非良性操作需确认 —— 这是"默认开启、trusted 放行"的落点。
    """

    name = "trust"

    def __init__(self, *, deny_unknown_shell: bool = False) -> None:
        self._deny_unknown_shell = deny_unknown_shell

    @staticmethod
    def _is_readonly_tool(payload: Payload) -> bool:
        read_only = ("read", "search", "list", "glob", "diff", "log", "status", "plan")
        return any(payload.method in (r,) or r in payload.tool_name for r in read_only)

    def check(self, payload: Payload) -> Optional[Verdict]:
        if not payload.any_text and self._is_readonly_tool(payload):
            # 只读工具无可执行内容: 直接放行, 不挑衅
            return None

        lvl = payload.trust_level
        if lvl in ("untrusted",):
            if payload.any_text:
                return deny(self.name, "工作区为 untrusted, 禁止执行命令/写入操作", SEV["high"])
        elif lvl in ("unknown",):
            if payload.any_text and not payload.yolo:
                return confirm(self.name, "工作区信任级别未知, 需用户确认后方可执行")
        elif lvl in ("limited",):
            if payload.any_text and not _all_benign_safe(payload) and not payload.yolo:
                return confirm(self.name, "工作区为 limited 信任, 非良性操作需确认", SEV["medium"])

        # 计划模式: 写类型工具必须拒绝
        if payload.plan_mode and not self._is_readonly_tool(payload):
            if payload.any_text:
                return deny(self.name, "计划模式已启用, 禁止执行写/修改操作", SEV["medium"])

        # 域名白名单 (配置提供): 含域名但不在白名单 → deny
        allowed = payload.meta.get("allowed_domains")
        if allowed:
            try:
                from ..core.network_guard import get_network_guard
                guard = get_network_guard()
                for domain in self._detected_domains(payload, guard):
                    if domain not in allowed:
                        return deny(self.name, f"域名不在白名单: {domain}", SEV["high"])
            except Exception:  # pragma: no cover
                pass
        return None

    @staticmethod
    def _detected_domains(payload: Payload, guard) -> List[str]:
        out: List[str] = []
        for t in payload.text:
            if not t.strip():
                continue
            try:
                dec = guard.check(t)
                out.extend(getattr(dec, "detected_domains", None) or [])
            except Exception:  # pragma: no cover
                pass
        return list(dict.fromkeys(out))


def _all_benign_safe(payload: Payload) -> bool:
    texts = payload.text or ([payload.command] if payload.command else [])
    return _all_benign(texts)


# ================================================================ L2 资源滤网
_SEV_RANK = {SEV["none"]: 0, SEV["low"]: 1, SEV["medium"]: 2, SEV["high"]: 3, SEV["critical"]: 4}


class ResourceFilter:
    """L2 资源滤网: 网络开关 + 内存/超时 + 工作区隔离模式选择。

    不主动拒绝; 它把"该不该上 L3 强隔离"以及"隔离时用哪种工作区模式"决定下来,
    并把网络/资源边界写进 Verdict 供执行端消费。
    """

    name = "resource"

    def __init__(
        self,
        *,
        deny_network_by_default: bool = False,
        max_memory_mb: int = 0,
        max_timeout: float = 0.0,
        isolate_copy_threshold: str = "high",
    ) -> None:
        self._deny_net = deny_network_by_default
        self._mem = max_memory_mb
        self._timeout = max_timeout
        self._copy_threshold = isolate_copy_threshold

    def check(self, payload: Payload) -> Optional[Verdict]:
        if not payload.any_text:
            return None
        v = Verdict(layer=self.name)
        v.memory_mb = self._mem
        v.timeout = self._timeout

        sev = payload.meta.get("severity") or SEV["none"]
        # 高危可逆操作 → 副本→diff→apply; 低危 → 直接执行(限路径)
        if _SEV_RANK.get(sev, 0) >= _SEV_RANK.get(self._copy_threshold, 3):
            v.workspace_mode = "copy_diff_apply"
            v.isolate = True

        wants_net = _has_network_request(payload.text)
        if wants_net and self._deny_net:
            v.network = False
            v.isolate = True
            v.reason = "策略默认禁网, 含网络请求的命令改走无网隔离执行"
        return v


# ================================================================ L3 强隔离滤网
class HardIsolationFilter:
    """L3 强隔离滤网: 隔离后端自动选择 (docker→bwrap→jobobject→local)。

    - 只有当 L2/意图已 decide 需要隔离(isolate) 时才介入。
    - 后端自动探测可用性; enforced=True 且无可用强后端 → deny (fail-closed)。
    这才是"超越 CodeX 单容器方案"的关键: 强后端缺失时不静默降级到裸执行, 而是拒绝。
    """

    name = "hard"

    def __init__(self, *, prefer: str = "auto", enforce_require: bool = True) -> None:
        self._prefer = prefer
        self._enforce = enforce_require

    def check(self, verdict: Verdict, payload: Payload) -> Optional[Verdict]:
        if not verdict.isolate:
            return None  # 不需要强隔离, 不触发后端
        from .backends import pick_backend
        backend, available = pick_backend(prefer=self._prefer)
        # "强后端" = 具备 OS 级隔离的真实贡献者 (docker/bwrap/jobobject/seatbelt), local 不算
        strong = available and backend != "local"
        if self._enforce and not strong:
            return verdict.merged(
                deny(self.name,
                     f"需要强隔离执行, 但无可用强后端(仅回落到 {backend}): fail-closed 拒绝",
                     SEV["critical"])
            )
        return verdict.merged(
            Verdict(layer=self.name, backend=backend,
                    backend_available=strong, enforced=strong or self._enforce)
        )


# ================================================================ 编排链
@dataclass
class SandboxFilterChain:
    """4 层滤网流水线: 依序求值, 逐层收紧, 拒绝即短路。"""

    intent: IntentFilter = field(default_factory=IntentFilter)
    trust: TrustFilter = field(default_factory=TrustFilter)
    resource: ResourceFilter = field(default_factory=ResourceFilter)
    hard: HardIsolationFilter = field(default_factory=HardIsolationFilter)

    # 其它先进可插拔滤网 (如 ai 语义滤网 / 政策规则滤网), 默认空
    extras: List[Any] = field(default_factory=list)

    def _order(self):
        yield self.intent
        yield self.trust
        yield from self.extras
        yield self.resource
        yield self.hard

    def evaluate(self, payload: Payload) -> Verdict:
        v = Verdict()
        for filt in self._order():
            if filt is self.hard:
                out = self.hard.check(v, payload)
            elif isinstance(filt, (ResourceFilter, TrustFilter, IntentFilter)):
                out = filt.check(payload)
            else:
                try:
                    out = filt.check(payload, v)
                except Exception:  # noqa: BLE001
                    out = deny("extras", "扩展滤网异常, fail-closed", SEV["high"])
            if out is not None:
                v = v.merged(out)
                if v.blocks:
                    break
        return v


def build_chain(policy: Optional[Dict[str, Any]] = None) -> SandboxFilterChain:
    """按配置构建 4 层滤网链。"""
    policy = policy or {}
    resource = policy.get("resource", {})
    return SandboxFilterChain(
        trust=TrustFilter(deny_unknown_shell=bool(policy.get("deny_unknown_shell", False))),
        resource=ResourceFilter(
            deny_network_by_default=bool(resource.get("deny_network_by_default", False)),
            max_memory_mb=int(resource.get("max_memory_mb", 0) or 0),
            max_timeout=float(resource.get("max_timeout", 0) or 0),
            isolate_copy_threshold=resource.get("isolate_copy_threshold", "high"),
        ),
        hard=HardIsolationFilter(
            prefer=policy.get("backend", "auto"),
            enforce_require=bool(policy.get("enforce_required", True)),
        ),
    )