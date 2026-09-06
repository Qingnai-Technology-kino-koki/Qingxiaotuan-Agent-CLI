"""Shell 工具插件: 在工作区执行终端命令 (危险操作, 默认需用户确认)。

世界级差异化: 命令执行前先经「最小影响半径」安全护栏 (safety 引擎) 做静态评分。
    - critical 级: 默认硬拦截, 除非 YOLO 且不在红线名单 (绝不自动执行 rm -rf / force push 等)
    - high / medium 级: 在确认提示中展示安全替代建议, 由用户决策
    - none / low: 正常放行
护栏引擎不可用时普通命令安全降级放行, 但 YOLO 红线判定本地兜底, 依然拦截致命命令。

安全架构 (v2.0):
    - 黑名单 (always block): is_hard_redline 命中的文件系统/OS 级毁灭操作无论如何都不执行
      (含 YOLO 模式); SQL 破坏性操作归为「可确认关键级」, 走极端 5 次确认
    - 白名单 (Trae 模式): CLI 手动维护, 命中自动执行, 但白名单永远低于黑名单
    - 多阶段确认: 极高风险命令弹5次警告 (不同位置按键), 高风险弹3次
    - 白名单优先级永远低于黑名单: 即使在白名单中, 命中红线仍拦截
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import sys
import threading
import time

from ..core.kernel import Kernel, Plugin
from ..core.whitelist import WhitelistManager, get_warning_level, MultiStageConfirm
from ..core.background_shell import (
    is_bg_command, run_in_background, strip_bg_prefix, get_manager,
)
from ..ext.safety_engine import is_redline as _engine_is_redline
from ..ext.safety_engine import is_hard_redline, is_benign_dev_command
from .base import Tool, ToolContext, string_prop

log = logging.getLogger(__name__)

MAX_OUTPUT = 20000

# 字面量兜底 (展示用/兼容旧引用); 判定逻辑以 ext/safety_engine.is_redline 为单一来源。
# 即使 YOLO 模式也绝不自动执行的致命操作红线 (与 safety 引擎的 critical 规则对齐)。
# 注: SQL 破坏性操作 (drop table / drop database) 已移出硬红线, 改走「可确认关键级」,
# 因此此处不再列出 (避免与 step3/step4 的极端确认流程冲突)。
YOLO_REDLINE = (
    "rm -rf", "rm -fr", "del /s /q", "rmdir /s", "format ", "sudo rm",
    "push --force", "push -f", "fork bomb",
    ":(){",
)


def is_redline(command: str) -> bool:
    """致命操作红线: 引擎 token 化判定 (单一来源) + 字面量兜底。

    覆盖 rm -rf / rm -r -f / --recursive --force / git push -f / del /s 等变体。
    """
    c = command.lower()
    if any(tok in c for tok in YOLO_REDLINE):
        return True
    try:
        return _engine_is_redline(c)
    except Exception as exc:  # noqa: BLE001
        log.debug("redline 引擎判定异常, 回退字面量: %s", exc)
        return False


def _strict_shell_enabled() -> bool:
    """模型无关的严格模式开关, 默认开启。

    弱模型(3B+)判断不可靠, 不能由它决定一条"没见过但危险"的命令是否执行;
    严格模式把非良性命令强制交给人工确认。关闭方式: QXT_STRICT_SHELL=0/false/off/no。
    """
    v = os.environ.get("QXT_STRICT_SHELL", "1").strip().lower()
    return v not in ("0", "false", "off", "no")


# Plan 模式下的写操作特征: 命中任一即视为修改类命令, 只读模式拦截
_WRITE_MARKERS = (
    ">", ">>", "| tee", "sed -i", "rm ", "mv ", "cp ", "mkdir", "touch ", "chmod", "chown",
    "git add", "git commit", "git push", "git reset", "git checkout", "git clean",
    "git stash", "git merge", "git rebase", "git tag", "git branch -d", "git branch -D",
    "npm install", "npm i ", "npm run build", "pip install", "pip uninstall",
    "pipenv install", "poetry add", "poetry install", "cargo build", "cargo install",
    "go build", "go install", "go mod", "make ", "cmake", "docker build", "docker run",
    "docker compose", "kubectl apply", "terraform apply", "python -m pytest --cov",
    "pytest --cov", "coverage run", "black ", "isort ", "ruff --fix", "yarn add",
    "pnpm add", "bun add", "conda install", "apt install", "apt-get install",
    "brew install", "brew uninstall", "dd ", "mkfs", "fdisk", "kill ", "pkill",
    "taskkill", "del ", "erase ", "-delete", "ren ", "copy ", "xcopy", "robocopy", "move ",
    "curl -o", "wget -O", "wget -o", "tar -x", "unzip", "git init", "git clone",
    "git config", "git remote", "git fetch", "git pull",
)


def _is_readonly_command(command: str) -> bool:
    """判断命令是否只读 (Plan 模式放行)。

    fail-closed 语义: 写特征命中 → 拒绝; 只读前缀命中 → 放行;
    无法识别的命令一律视为修改类 (保守拒绝), 宁可多拦不误放。
    """
    c = command.strip().lower()
    if not c:
        return True
    if any(marker in c for marker in _WRITE_MARKERS):
        return False
    first = c.split("|")[0].strip()
    readonly_prefixes = ("ls", "cat", "head", "tail", "grep", "find", "echo",
                         "pwd", "whoami", "date", "which", "where", "type",
                         "git status", "git diff", "git log", "git show",
                         "git branch", "git remote -v", "python -c", "python -m py_compile")
    return any(first.startswith(p) for p in readonly_prefixes)


def _risk_to_advice(reasons: list, previews: list) -> str:
    parts = []
    if reasons:
        parts.append("命中风险: " + "; ".join(reasons[:3]))
    if previews:
        parts.append("安全替代: " + " | ".join(previews[:2]))
    return "  ".join(parts)


# TOCTOU 修复: 脚本文件内容安全检测
def _strip_quotes(p: str) -> str:
    """去掉路径两端成对引号 (单/双), 引号内可能含空格。"""
    if len(p) >= 2 and p[0] == p[-1] and p[0] in "'\"":
        return p[1:-1]
    return p


# 支持带空格路径: 匹配单引号路径 / 双引号路径 / 非空白序列
_SCRIPT_PATH_PATTERN = r'("(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)*\'|\S+)'
_SCRIPT_FLAGS_RE = re.compile(
    r'\b(?:powershell|pwsh)\b.*?-(?:File|f|Command|c)\s+' + _SCRIPT_PATH_PATTERN, re.IGNORECASE
)
_CMD_C_SCRIPT_RE = re.compile(
    r'\bcmd\b.*/\s*c\s+' + _SCRIPT_PATH_PATTERN, re.IGNORECASE
)
_SH_SCRIPT_RE = re.compile(
    r'\b(?:bash|sh|zsh|ksh)\b\s+' + _SCRIPT_PATH_PATTERN, re.IGNORECASE
)


def _check_script_content(command: str, workspace: str) -> str | None:
    """TOCTOU 修复: 检查通过 -File/-f 等方式执行的脚本文件内容。"""
    for regex in (_SCRIPT_FLAGS_RE, _CMD_C_SCRIPT_RE, _SH_SCRIPT_RE):
        m = regex.search(command)
        if not m:
            continue
        script_path = _strip_quotes(m.group(1))
        candidates = [
            script_path,
            os.path.join(workspace, script_path),
        ]
        for path in candidates:
            try:
                real_path = os.path.realpath(path)
                if os.path.isfile(real_path):
                    with open(real_path, 'r', errors='replace') as f:
                        content = f.read(8192)
                    if content and _engine_is_redline(content):
                        return (
                            f"[TOCTOU 安全拦截] 脚本文件 {script_path} 的内容包含致命操作:\n"
                            f"文件内容 (前 200 字符): {content[:200]}\n"
                            f"该脚本通过命令执行: {command}"
                        )
            except (OSError, PermissionError):
                continue
    return None


# ============================================================
# 安全架构 v2.0: 黑名单 + 白名单 + 多阶段确认
# ============================================================

# 线程安全的单例白名单管理器
_whitelist_manager: WhitelistManager | None = None
_whitelist_lock = threading.Lock()


def _get_whitelist_manager(home=None) -> WhitelistManager:
    """获取全局白名单管理器单例。"""
    global _whitelist_manager
    with _whitelist_lock:
        if _whitelist_manager is None:
            _whitelist_manager = WhitelistManager(home)
        return _whitelist_manager


# 全局多阶段确认管理器
_confirmer: MultiStageConfirm | None = None
_confirmer_lock = threading.Lock()


def _get_confirmer() -> MultiStageConfirm:
    """获取全局多阶段确认管理器单例。"""
    global _confirmer
    with _confirmer_lock:
        if _confirmer is None:
            _confirmer = MultiStageConfirm()
        return _confirmer


def _pre_exec_guard(ctx: ToolContext, command: str) -> str | None:
    """执行前安全拦截。返回 None 表示放行; 返回字符串表示拦截原因 (已拒绝)。

    v2.0 安全架构: 工作区信任 → 黑名单 → 白名单 → 多阶段确认 → 网络门控 → 分类器兜底。
    v2.1 增强: 所有安全决策通过 SecurityEventBus 记录审计。
    """
    # 安全事件总线: 记录本次安全决策
    def _emitSecurityEvent(event_type: str, payload: dict) -> None:
        try:
            from ..core.security_bus import SecurityEvent, get_security_bus
            bus = get_security_bus()
            bus.emit(SecurityEvent(
                event_type=event_type,
                timestamp=time.time(),
                payload={"command": command[:300], **payload},
                source="shell_guard",
                severity=payload.get("severity", "info"),
            ))
        except Exception:  # noqa: BLE001
            pass

    # ======== 0) 工作区信任级别 (fail-closed, 仅当 ctx 提供 trust_level 时生效) ========
    # 已有工作区信任系统长期未被执行路径 consult, 此处补齐门禁:
    #   untrusted -> 直接拒绝 shell; unknown -> 必须确认; limited -> 非良性命令确认。
    trust_level = getattr(ctx, "workspace_trust_level", None)
    if trust_level is not None:
        try:
            from ..core.workspace_trust import consult_for_shell
            taction, treason = consult_for_shell(trust_level, command)
        except Exception as exc:  # noqa: BLE001
            taction, treason = "deny", f"信任级别裁决异常, 保守拒绝: {exc}"
        if taction == "deny":
            ctx.safety_severity = "high"
            return f"[已拦截] {treason}: {command}"
        if taction == "confirm":
            confirm_fn = getattr(ctx, "confirm", None)
            if confirm_fn is not None:
                try:
                    if not confirm_fn(f"⚠️ {treason}: {command}\n确认执行?"):
                        return f"[已拦截] 用户拒绝在 {trust_level} 工作区执行: {command}"
                except Exception:  # noqa: BLE001
                    return f"[已拦截] 确认通道异常, {trust_level} 工作区禁止执行: {command}"
            else:
                return f"[已拦截] 无确认通道, {trust_level} 工作区禁止执行: {command}"

    # TOCTOU 修复: 检查脚本文件内容 (在命令级红线检测之前)
    workspace = getattr(ctx, 'workspace', '') or ''
    script_check = _check_script_content(command, workspace)
    if script_check is not None:
        return script_check

    # 网络出口门控: 网络命令需经 NetworkGuard 评估 (数据外泄/远程执行检测)
    try:
        from ..core.network_guard import get_network_guard
        net_guard = get_network_guard()
        net_decision = net_guard.check(command)
        if net_decision.action == "deny":
            reasons_str = "; ".join(net_decision.reasons[:5])
            ctx.safety_severity = net_decision.risk_level
            return (
                f"[已拦截] 网络出口门控: {command}\n"
                f"原因: {reasons_str}\n"
                f"检测到的域名: {', '.join(net_decision.detected_domains) or '(无)'}"
            )
        if net_decision.action == "confirm" and ctx.confirm is not None:
            reasons_str = "; ".join(net_decision.reasons[:3])
            warning = (
                f"⚠️ 网络出口门控检测到中风险操作:\n{command}\n"
                f"原因: {reasons_str}\n"
                f"检测到的域名: {', '.join(net_decision.detected_domains) or '(无)'}\n"
                "确认要执行网络操作吗? (仅本次生效)"
            )
            try:
                if not ctx.confirm(warning):
                    return f"[已拦截] 用户拒绝网络操作: {command}"
            except Exception as exc:  # noqa: BLE001
                # fail-closed: 确认通道故障 ≠ 安全, 拒绝网络操作
                log.error("网络确认通道异常, fail-closed 拒绝: %s", exc)
                return f"[已拦截] 网络确认通道异常, 安全降级拒绝: {command}"
    except Exception as exc:  # noqa: BLE001
        # fail-closed: 网络门控不可用时, 涉及网络的命令必须被拒绝,
        # 而非静默放行 (否则被禁用的门控形同虚设)。
        log.error("网络门控异常, fail-closed 拒绝: %s", exc)
        return f"[已拦截] 网络出口门控不可用, 安全降级拒绝: {command}"

    # ======== 1) 硬红线 (最高优先级, 永远拦截, 含 YOLO 模式) ========
    # 使用 is_hard_redline: 仅文件系统/OS 级毁灭操作永不自动执行;
    # SQL 破坏性操作 (DROP/DELETE/TRUNCATE) 不在此列, 走下方 step3/step4 的极端确认流程。
    if is_hard_redline(command):
        reasons: list[str] = []
        previews: list[str] = []
        svc = ctx.kernel.get("safety_check") if ctx.kernel else None
        if svc is not None:
            try:
                verdict = svc.check(ctx, command)
                reasons = verdict.get("reasons") or []
                previews = verdict.get("safe_preview") or []
            except Exception as exc:  # noqa: BLE001
                log.debug("safety_check 调用失败 (红线拦截不受影响): %s", exc)
        advice = _risk_to_advice(reasons, previews)
        suffix = "" if ctx.confirm is not None else "\n(当前无确认通道, 无法请求人工放行)"
        ctx.safety_severity = "critical"
        _emitSecurityEvent("security.command.blocked", {"reason": "hard_redline", "severity": "critical"})
        return (f"[已拦截] 命中致命操作红线, 即使在 YOLO 模式也禁止自动执行: {command}\n"
                + advice + suffix)

    # ======== 2) 白名单检查 (Trae 模式: 用户同意后自动执行) ========
    # 白名单优先级永远低于黑名单: 上面已经检查过红线, 能到这里说明不在红线中
    wl_manager = _get_whitelist_manager()
    # 会话级 allowedTools (--allowedTools): 作为白名单的"额外加分", 优先级仍低于黑名单/红线。
    session_allowed = getattr(ctx, "allowed_tools", None)
    _session_match = bool(session_allowed is not None
                          and getattr(session_allowed, "allows_bash", lambda c: False)(command))
    if wl_manager.is_whitelisted(command) or _session_match:
        # 白名单命中, 但仍需检查是否包含危险子模式 (防御纵深)
        warning_level = get_warning_level(command)
        if warning_level >= 5 and not getattr(ctx, "yolo", False):
            # 即使白名单, 极高风险命令仍需5次确认
            if ctx.confirm is not None:
                confirmer = _get_confirmer()
                confirmer.set_confirm_fn(ctx.confirm)
                if not confirmer.confirm(command, 5, "极高风险: 白名单中的命令仍包含致命操作模式"):
                    return f"[已拦截] 用户拒绝执行极高风险命令: {command}"
            else:
                return f"[已拦截] 无确认通道, 极高风险命令无法自动执行: {command}"
        return None  # 白名单命中且无高危模式 → 放行

    # ======== 2.5) 严格模式兜底 (模型无关): 非良性 + 低/中危直接交给人工确认 ========
    # 旧逻辑的洞: 一条「非红线、非白名单、warning_level 又偏低」的命令会一路走到
    # return None 自动放行, 执行与否全凭模型判断 —— 弱模型(3B+)被注入/诱导时,
    # 完全可能发出"从未见过但危险"的低危命令而被放行。这里把执行决定权从模型
    # 挪到人手里: 非良性命令强制人工确认(仅本次), 无确认通道则 fail-closed 拒绝。
    # 白名单命令已在 step2 放行跳过此处; 高危(>=3)仍走 step3 多级确认, 避免重复弹窗。
    warning_level_now = get_warning_level(command)
    if _strict_shell_enabled():
        try:
            benign = is_benign_dev_command(command)
        except Exception:  # noqa: BLE001 - 无法评估按非良性处理 (fail-closed)
            benign = False
        if not benign:
            # 非良性命令一律交给人工: 低/中危 (warning<3) 且当前那一步是最后的放行点,
            # 弹 1 次确认; warning>=3 交由下方 step3 的多级确认兜底 (不在此重复弹窗)。
            # 关键: 无确认通道 (无人值守/headless) 时对所有非良性命令直接 fail-closed 拒绝,
            # 堵住旧洞 —— 高危非良性命令 (kill -9 / docker rm -f 等) 无通道时过去只是
            # 「附建议即自动执行」, 弱模型(3B+)被诱导即可绕过。现在一律要求人类在场。
            if ctx.confirm is None:
                return (f"[已拦截] 严格模式(QXT_STRICT_SHELL): 无确认通道, "
                        f"非良性命令禁止自动执行:\n{command}")
            if warning_level_now < 3:
                try:
                    if not ctx.confirm(
                        f"⚠️ 严格模式(QXT_STRICT_SHELL): 非良性命令需人工确认(仅本次):\n"
                        f"{command}\n确认执行?"
                    ):
                        return f"[已拦截] 用户在严格模式拒绝非良性命令: {command}"
                except Exception:  # noqa: BLE001
                    return f"[已拦截] 严格模式确认通道异常, 保守拒绝执行: {command}"

    # ======== 3) 多阶段确认 (非白名单命令) ========
    warning_level = get_warning_level(command)
    if warning_level >= 5:
        # 极高风险: YOLO 模式或 无确认通道 → 硬拦 (fail-closed);
        # 非 YOLO 且有确认通道 → 弹 5 次确认, 用户通过才放行 (仅本次)。
        # 这样形成「硬红线 (永不确认)」与「可确认关键级 (极端 5 次确认)」两个清晰层级。
        ctx.safety_severity = "critical"
        if getattr(ctx, "yolo", False) or ctx.confirm is None:
            return (f"[已拦截] 极高风险操作 (需要5次确认), "
                    f"{'YOLO 模式禁止自动执行' if getattr(ctx, 'yolo', False) else '当前无确认通道'}: {command}")
        confirmer = _get_confirmer()
        confirmer.set_confirm_fn(ctx.confirm)
        if not confirmer.confirm(command, 5, "极高风险操作"):
            return f"[已拦截] 用户在第5次警告中拒绝执行: {command}"
    elif warning_level >= 3:
        # 高风险: 有确认通道时弹3次确认; 无确认通道时不做硬拦截,
        # 落到 step4 附加安全建议 (与「high 级仅提示、不硬拦」的设计一致, 见各 test_high_*)
        if ctx.confirm is not None:
            confirmer = _get_confirmer()
            confirmer.set_confirm_fn(ctx.confirm)
            if not confirmer.confirm(command, 3, "高风险操作"):
                return f"[已拦截] 用户在第3次警告中拒绝执行: {command}"

    # ======== 4) 继续原来的 safety_check 流程 ========
    if ctx.kernel is None:
        return None
    svc = ctx.kernel.get("safety_check")
    if svc is None:
        return None
    try:
        verdict = svc.check(ctx, command)
    except Exception as exc:  # noqa: BLE001
        # fail-closed: safety_check 异常时不做静默放行。
        # strict_mode 已在上游挡住非良性命令, 但 safety_check 故障时
        # 仍应记录异常, 避免高危命令因安全组件故障而溜过。
        log.error("safety_check 异常 (不再静默放行): %s", exc)
        return None
    risk = (verdict.get("risk") or "none").lower()
    block = bool(verdict.get("block"))
    reasons = verdict.get("reasons") or []
    previews = verdict.get("safe_preview") or []
    if block or risk == "critical":
        advice = _risk_to_advice(reasons, previews)
        ctx.safety_severity = "critical"
        if not getattr(ctx, "yolo", False) and ctx.confirm is not None:
            warning = (
                f"⚠️ 检测到致命风险操作 (critical):\n{command}\n{advice}\n"
                "安全引擎建议不要执行。确认要人工放行吗? (仅本次生效)"
            )
            try:
                if ctx.confirm(warning):
                    return None
            except Exception as exc:  # noqa: BLE001
                log.debug("critical 人工放行确认异常, 维持拦截: %s", exc)
        suffix = "" if ctx.confirm is not None else "\n(当前无确认通道, 无法请求人工放行)"
        return (f"[已拦截] 检测到致命风险操作 (critical), 必须人工确认, 不自动执行:\n{command}\n"
                + advice + suffix)
    if risk in ("high", "medium") and (reasons or previews):
        ctx.safety_severity = risk
        ctx.safety_advice = _risk_to_advice(reasons, previews)

    # ======== 5) 独立安全分类器兜底 (Claude Code 分类器架构) ========
    # safety_engine 未拦截时, 由分类器做语义级评估, 捕获模式匹配遗漏的新型攻击
    try:
        from ..core.security_classifier import get_classifier
        classifier = get_classifier()
        cls_result = classifier.classify_with_safety_engine(command, verdict)
        if cls_result.action == "deny":
            advice = "; ".join(cls_result.reasons[:5])
            ctx.safety_severity = cls_result.risk_level
            suffix = "" if ctx.confirm is not None else "\n(当前无确认通道, 无法请求人工放行)"
            return (f"[已拦截] 安全分类器检测到高风险操作:\n{command}\n"
                    f"风险评分: {cls_result.risk_level} (置信度: {cls_result.confidence:.0%})\n"
                    f"原因: {advice}{suffix}")
        if cls_result.action == "confirm" and ctx.confirm is not None:
            # 分类器建议确认: 弹一次确认 (非多阶段, 仅分类器兜底)
            warning = (
                f"⚠️ 安全分类器检测到中风险操作:\n{command}\n"
                f"风险评分: {cls_result.risk_level} (置信度: {cls_result.confidence:.0%})\n"
                f"原因: {'; '.join(cls_result.reasons[:3])}\n"
                "确认要执行吗? (仅本次生效)"
            )
            try:
                if not ctx.confirm(warning):
                    return f"[已拦截] 用户拒绝分类器建议确认的操作: {command}"
            except Exception as exc:  # noqa: BLE001
                # fail-closed: 确认通道故障 ≠ 安全
                log.error("分类器确认通道异常, fail-closed 拒绝: %s", exc)
                return f"[已拦截] 分类器确认通道异常, 安全降级拒绝: {command}"
    except Exception as exc:  # noqa: BLE001
        # fail-closed: 安全分类器是最后一道兜底防线, 其异常不应
        # 让命令静默通过 —— 记录异常并拒绝。
        log.error("安全分类器异常, fail-closed 拒绝: %s", exc)
        return f"[已拦截] 安全分类器不可用, 安全降级拒绝: {command}"

    return None


def _kill_proc_tree(proc: subprocess.Popen) -> None:
    """终止进程树 (Windows 用 taskkill /T, POSIX 用 killpg)。"""
    try:
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                capture_output=True, timeout=5,
            )
        else:
            import os, signal
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                pass
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
    except Exception:  # noqa: BLE001
        try:
            proc.kill()
        except Exception:
            pass


def _route_sandbox_isolation(ctx: ToolContext, command: str, timeout: int):
    """若统一滤网裁决本命令需隔离, 用隔离后端执行并返回结果文本 (None=无需隔离)。

    消费 ToolExecutor 在 ctx._sandbox_verdict 写下的 L3 裁决; 未裁决或后端不可用
    (非强制) 时返回 None, 交原有本地执行路径。
    """
    verdict = getattr(ctx, "_sandbox_verdict", None)
    if verdict is None or not getattr(verdict, "isolate", False):
        return None
    try:
        from ..sandbox.manager import SandboxManager
        manager = SandboxManager.from_kernel(getattr(ctx, "kernel", None)) \
            if getattr(ctx, "kernel", None) is not None else SandboxManager()
        res = manager.execute_isolated(command, verdict, ctx, timeout=timeout or 60.0)
    except Exception as exc:  # noqa: BLE001 - 隔离执行异常不外抛, 留给原有路径兜底
        return f"[错误] 沙箱隔离执行初始化异常: {type(exc).__name__}: {exc}"
    # 强制隔离且后端可用 → 返回隔离结果。否则 (enforced=False, 回落 local):
    # 若配置 enforce_required 则以裁决的 DENY 语义拒绝, 由调用方阅读; 这里统一落地。
    if verdict.enforced or getattr(verdict, "backend_available", False):
        return res.as_shell_text() if res.returncode >= 0 else f"[错误] {res.error or '隔离执行失败'}"
    return None


def run_shell(ctx: ToolContext, command: str, timeout: int = 0) -> str:
    # 0) 后台 Shell (`! cmd`): 异步执行, 立即返回 job id, 不阻塞该回合
    if is_bg_command(command):
        return _start_background(ctx, command, timeout)
    # 0b) Plan 模式: 只读命令放行, 写命令拦截
    if getattr(ctx, "plan_mode", False) and not _is_readonly_command(command):
        return ("[Plan 模式] 只读模式已启用, 已阻止修改类命令:\n"
                f"  {command}\n"
                "请先输出分析与实施计划, 退出 Plan 模式后再执行修改。")
    # 1) 执行前安全护栏 (v2.0: 黑名单+白名单+多阶段确认)
    guard = _pre_exec_guard(ctx, command)
    if guard:
        return guard
    # 1.5) 系统级沙箱强隔离选路: 若统一滤网裁决本命令需隔离 (L3 后端可就绪),
    #       改走隔离执行 (jobobject/bwrap/docker/local), 并返回其裁决化的结果文本。
    isolated = _route_sandbox_isolation(ctx, command, timeout)
    if isolated is not None:
        return isolated
    limit = timeout or ctx.config("tools.shell.timeout", 60)
    try:
        proc = subprocess.Popen(
            command,
            shell=True,
            cwd=ctx.workspace,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            errors="replace",
            start_new_session=os.name != "nt",
        )
        try:
            if limit and limit > 0:
                out, err = proc.communicate(timeout=limit)
            else:
                out, err = proc.communicate()
        except subprocess.TimeoutExpired:
            _kill_proc_tree(proc)
            try:
                out, err = proc.communicate(timeout=5)
            except Exception:  # noqa: BLE001
                out, err = "", ""
            partial = ((out or "") + (err or "")).strip()
            if len(partial) > MAX_OUTPUT:
                partial = partial[:MAX_OUTPUT] + f"\n...[截断, 原始输出 {len(partial)} 字符]"
            notice = "(命令超时, 进程树已被终止)"
            return f"exit=-1\n{notice}\n{partial}" if partial else f"exit=-1\n{notice}"
        out = (out or "") + (err or "")
        if len(out) > MAX_OUTPUT:
            out = out[:MAX_OUTPUT] + f"\n...[截断, 原始输出 {len(out)} 字符]"
        return f"exit={proc.returncode}\n{out.strip()}"
    except Exception as exc:  # noqa: BLE001
        return f"[错误] shell 执行异常: {type(exc).__name__}: {exc}"


def _start_background(ctx: ToolContext, command: str, timeout: int = 0) -> str:
    """后台 `! cmd`: 异步启动并立即返回 job 摘要文本。

    后台命令同样经过执行前安全护栏: 命中红线/非良性命令仍被拦截 (后台不等于豁免)。
    """
    body = strip_bg_prefix(command).strip()
    if not body:
        return "[错误] 后台命令为空"
    # Plan 模式只读约束同样适用于后台命令 (不能借 `!` 绕过只读拦截)
    if getattr(ctx, "plan_mode", False) and not _is_readonly_command(body):
        return ("[Plan 模式] 只读模式已启用, 已阻止后台写命令 (`!` 不豁免只读约束):\n"
                f"  {body}\n"
                "请先输出分析与实施计划并退出 Plan 模式后再执行。")
    guard = _pre_exec_guard(ctx, body)
    if guard:
        return guard
    try:
        job = run_in_background(body, cwd=getattr(ctx, "workspace", None) or None,
                                timeout=timeout or None)
    except ValueError as exc:
        return f"[错误] {exc}"
    except Exception as exc:  # noqa: BLE001
        return f"[错误] 后台命令启动失败: {type(exc).__name__}: {exc}"
    return (f"[后台已启动] job={job.job_id} (pid={job.pid}) status=running\n"
            f"命令: {body}\n"
            f"该命令在后台执行, 不阻塞当前回合。用 bg_shell 工具 (action=status) "
            f"查看进度、action=logs 取输出、action=wait 阻塞等它结束、action=cancel 终止。")

bg_shell_action_prop = {
    "type": "string",
    "enum": ["status", "logs", "wait", "cancel", "list", "prune"],
    "description": "操作: status=查状态; logs=取输出尾部; wait=阻塞等待结束; "
                   "cancel=终止; list=列出全部后台任务; prune=清理已结束任务的占位",
}


def run_bg_shell(ctx: ToolContext, action: str = "status", job_id: str = "",
                 lines: int = 20, timeout: float = 0, offset: int = 0) -> str:
    """后台 shell 控制工具: 查询/取回/等待/取消用 `!` 启动的后台命令。"""
    action = (action or "status").lower()
    manager = get_manager()
    try:
        if action == "list":
            view = manager.status_dict()
            if not view.get("ok"):
                return f"[错误] {view.get('error')}"
            parts = [f"后台任务共 {view['total']} 个 (按状态: {view['by_status']}; "
                     f"运行中 {view['running']}/{view['max_running'] or '不限'}):"]
            for j in view["jobs"]:
                parts.append(
                    f"  {j['job_id']:<14} {j['status']:<9} returncode={j.get('returncode')} "
                    f"elapsed={j['elapsed']}s  {j['command'][:70]}")
            return "\n".join(parts) if len(parts) > 1 else "[后台] 暂无后台任务"

        if action == "prune":
            removed = manager.prune_disk()
            manager.prune_finished()
            return f"[已清理] 移除了 {removed} 个已结束后台任务的磁盘占位"

        if not job_id:
            return "[错误] bg_shell status/logs/wait/cancel 需提供 job_id (可用 action=list 查看)"

        if action == "status":
            view = manager.status_dict(job_id, tail=0)
            if not view.get("ok"):
                return f"[错误] {view.get('error')}"
            return (f"job={view['job_id']} status={view['status']} "
                    f"returncode={view.get('returncode')} elapsed={view['elapsed']}s "
                    f"pid={view.get('pid')}\n命令: {view['command']}"
                    + (f"\n结果:{view.get('returncode')}" if view["status"] in ("done",) else ""))

        if action == "logs":
            if offset > 0:
                view = manager.tail_since(job_id, offset=offset, limit=max(1, int(lines)))
                if not view.get("ok"):
                    return f"[错误] {view.get('error')}"
                body = view["text"]
                head = (f"job={job_id} status={view['status']} (offset={offset} "
                        f"之后新增 {len(body)} 行):")
                marker = f"\n(next_offset={view['next_offset']})"
                return (f"{head}\n{body or '(暂无新输出)'}{marker}"
                        if body else f"{head}\n(暂无新输出)\n(next_offset={view['next_offset']})")
            body = manager.tail(job_id, max(1, int(lines)))
            st = manager.status_dict(job_id)
            head = f"job={job_id} status={st.get('status')} (最近 {max(1, int(lines))} 行):"
            return f"{head}\n{body}" if body else f"{head}\n(暂无输出)"

        if action == "wait":
            job = manager.get(job_id)
            if job is None:
                return f"[错误] 后台任务 {job_id} 未找到"
            job = manager.wait(job_id, timeout=timeout or None)
            tail = job.tail(max(1, int(lines)))
            head = (f"job={job_id} 已结束, status={job.status} "
                    f"returncode={job.returncode} elapsed={job.to_dict()['elapsed']}s")
            return f"{head}\n最近输出:\n{tail}" if tail else head

        if action == "cancel":
            ok = manager.cancel(job_id)
            return f"[已终止] {job_id}" if ok else f"[错误] job={job_id} 不存在或已结束"
        return f"[错误] 未知 action: {action}"
    except Exception as exc:  # noqa: BLE001
        return f"[错误] bg_shell 执行异常: {type(exc).__name__}: {exc}"


class ShellPlugin(Plugin):
    name = "tools.shell"
    provides = []
    requires = ["tool_registry"]

    def activate(self, kernel: Kernel) -> None:
        config = kernel.get("config")
        if config and not config.get("tools.shell.enabled", True):
            return
        registry = kernel.require("tool_registry")
        need_confirm = not config or config.get("tools.shell.require_confirm", True)
        registry.register(Tool(
            name="run_shell",
            description="在工作区执行 shell 命令, 返回退出码与输出。执行前经最小影响半径安全护栏静态评分, critical 级自动拦截。命令以 ! 开头时在后台异步执行, 并配合 bg_shell 工具查询。",
            parameters={
                "type": "object",
                "properties": {
                    "command": string_prop("命令 (以 ! 开头则后台异步执行)"),
                    "timeout": {"type": "integer", "description": "超时秒数 (可选)"},
                },
                "required": ["command"],
            },
            handler=run_shell,
            dangerous=need_confirm,
            group="shell",
        ))
        # 后台 shell 控制工具: 只读/控制, 不要求确认 (启动命令本身已在 run_shell 按危险度确认)
        registry.register(Tool(
            name="bg_shell",
            description="控制用 `! 命令` 后台启动的 shell 任务: 查列表/查状态/取输出尾部/阻塞等待/终止。",
            parameters={
                "type": "object",
                "properties": {
                    "action": bg_shell_action_prop,
                    "job_id": string_prop("后台任务 id (status/logs/wait/cancel 需要)"),
                    "lines": {"type": "integer", "description": "logs/wait 取输出的行数 (默认 20)"},
                    "timeout": {"type": "number", "description": "wait 阻塞的超时秒数 (0=无限)"},
                    "offset": {"type": "integer", "description": "logs 增量跟随: 只读 offset 行之后的输出 (默认 0=取末尾等价最近)"},
                },
                "required": ["action"],
            },
            handler=run_bg_shell,
            dangerous=False,
            group="shell",
        ))
