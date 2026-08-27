"""Auto Mode Permission Classifier (对标 Claude Code 2.1.239 Auto Mode).

核心思想:
- 不依赖 LLM 判断, 用纯规则 + 风险评分决定工具调用是否自动批准
- 三级权限: auto (自动批准安全操作, 危险操作确认) / normal (确认所有危险) / yolo (全部自动)
- 用户可自定义 auto mode 规则表 (auto_mode_rules), 按 deny > ask > allow 优先级
- 每次工具调用前, classifier 返回 RiskLevel + 决策理由, 供 ToolExecutor 决定是否需要确认

风险评分维度:
1. 工具类型 (read-only vs write vs destructive vs network)
2. 命令模式匹配 (shell 命令中的危险模式)
3. 文件路径范围 (是否在工作区内)
4. 用户自定义规则
5. 会话历史 (同一操作的重复频率)
"""

from __future__ import annotations

import fnmatch
import os
import re
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, Callable, Dict, List, Optional, Set, Tuple
from urllib.parse import urlparse


class RiskLevel(IntEnum):
    """风险等级: 数值越大越危险。"""
    SAFE = 0          # 只读操作, 无副作用
    LOW = 1           # 可逆写操作 (edit_file, write_file 在工作区内)
    MODERATE = 2      # 需要确认的写操作 (run_shell 非危险命令, 外部网络请求)
    HIGH = 3          # 危险操作 (run_shell 危险模式, delete_file)
    CRITICAL = 4      # 极危险操作 (rm -rf, DROP TABLE, git push --force)


# ================================================================ 工具风险分类

# 工具名 → 默认风险等级 (无用户规则覆盖时的基线)
TOOL_BASELINE_RISK: Dict[str, RiskLevel] = {
    # 只读工具
    "read_file": RiskLevel.SAFE,
    "read_files": RiskLevel.SAFE,
    "code_search": RiskLevel.SAFE,
    "glob": RiskLevel.SAFE,
    "list_directory": RiskLevel.SAFE,
    "web_search": RiskLevel.SAFE,
    "web_fetch": RiskLevel.LOW,        # 网络请求, 有 SSRF 风险
    "memory_search": RiskLevel.SAFE,
    "skill_list": RiskLevel.SAFE,
    "pipeline_list_tools": RiskLevel.SAFE,
    # 可逆写工具
    "write_file": RiskLevel.LOW,
    "edit_file": RiskLevel.LOW,
    "str_replace": RiskLevel.LOW,
    # Shell 执行 (风险由命令内容决定)
    "run_terminal_command": RiskLevel.MODERATE,
    "run_shell": RiskLevel.MODERATE,
    # 危险工具
    "delete_file": RiskLevel.HIGH,
    "delete_dir": RiskLevel.HIGH,
    "move_file": RiskLevel.MODERATE,
    # MCP
    "mcp_call": RiskLevel.LOW,
    # 内部工具
    "todo_write": RiskLevel.SAFE,
    "todo_read": RiskLevel.SAFE,
    "checkpoint_save": RiskLevel.SAFE,
    "checkpoint_restore": RiskLevel.LOW,
    "skill_save": RiskLevel.LOW,
    "enter_plan_mode": RiskLevel.SAFE,
    "exit_plan_mode": RiskLevel.SAFE,
    "task": RiskLevel.MODERATE,         # 子 Agent 委派
    "background_submit": RiskLevel.MODERATE,
    "send_message": RiskLevel.LOW,      # 跨 session 消息
    "list_agents": RiskLevel.SAFE,
}

# Shell 命令危险模式 → 风险升级
SHELL_RISK_PATTERNS: List[Tuple[re.Pattern, RiskLevel, str]] = [
    # CRITICAL: 不可逆破坏性操作
    (re.compile(r"\brm\s+(-\w*\s+)*(--?rf|--?fr|-rf|-fr)\s+/"), RiskLevel.CRITICAL, "rm -rf / 级别操作"),
    (re.compile(r"\bdrop\s+table\b", re.IGNORECASE), RiskLevel.CRITICAL, "DROP TABLE"),
    (re.compile(r"\bdrop\s+database\b", re.IGNORECASE), RiskLevel.CRITICAL, "DROP DATABASE"),
    (re.compile(r"\btruncate\s+table\b", re.IGNORECASE), RiskLevel.CRITICAL, "TRUNCATE TABLE"),
    (re.compile(r"\bgit\s+push\s+.*--force"), RiskLevel.CRITICAL, "git push --force"),
    (re.compile(r"\bgit\s+push\s+.*-f\b"), RiskLevel.CRITICAL, "git push -f"),
    (re.compile(r"\bgit\s+reset\s+--hard"), RiskLevel.CRITICAL, "git reset --hard"),
    (re.compile(r"\bgit\s+clean\s+(-\w*\s+)*-f"), RiskLevel.CRITICAL, "git clean -f"),
    # HIGH: 危险但常见
    (re.compile(r"\brm\s+(-\w*\s+)*-rf?\b"), RiskLevel.HIGH, "rm -rf"),
    (re.compile(r"\brm\s+(-\w*\s+)*-r\b"), RiskLevel.HIGH, "rm -r"),
    (re.compile(r"\bsudo\b"), RiskLevel.HIGH, "sudo 命令"),
    (re.compile(r"\bchmod\s+777\b"), RiskLevel.HIGH, "chmod 777"),
    (re.compile(r"\bchown\s+.*\s+/"), RiskLevel.HIGH, "chown 系统目录"),
    (re.compile(r"\bcurl\s+.*\|\s*(ba)?sh"), RiskLevel.HIGH, "curl | sh 管道执行"),
    (re.compile(r"\bwget\s+.*\|\s*(ba)?sh"), RiskLevel.HIGH, "wget | sh 管道执行"),
    (re.compile(r"\bsystemctl\s+(stop|disable|mask)\b"), RiskLevel.HIGH, "systemctl 停用服务"),
    (re.compile(r"\bdocker\s+rm\b"), RiskLevel.HIGH, "docker rm"),
    (re.compile(r"\bdocker\s+kill\b"), RiskLevel.HIGH, "docker kill"),
    (re.compile(r"\bkubectl\s+delete\b"), RiskLevel.HIGH, "kubectl delete"),
    # MODERATE: 需要注意但不危险
    (re.compile(r"\bgit\s+push\b"), RiskLevel.MODERATE, "git push"),
    (re.compile(r"\bgit\s+commit\b"), RiskLevel.MODERATE, "git commit"),
    (re.compile(r"\bpip\s+install\b"), RiskLevel.MODERATE, "pip install"),
    (re.compile(r"\bnpm\s+install\b"), RiskLevel.MODERATE, "npm install"),
    (re.compile(r"\byarn\s+add\b"), RiskLevel.MODERATE, "yarn add"),
    (re.compile(r"\bcargo\s+install\b"), RiskLevel.MODERATE, "cargo install"),
]


# ================================================================ 数据结构

@dataclass
class RiskAssessment:
    """风险评估结果。"""
    level: RiskLevel
    reason: str
    tool_name: str
    matched_pattern: Optional[str] = None  # 命中的危险模式描述
    auto_approve: bool = False             # 是否自动批准
    needs_confirm: bool = False            # 是否需要用户确认
    rule_source: str = ""                  # 决策来源 (规则表 / 内置分类 / 用户自定义)


@dataclass
class AutoModeRule:
    """Auto Mode 自定义规则。"""
    tool_pattern: str       # fnmatch 模式 (如 "run_shell", "write_file")
    action: str             # allow | deny | ask
    pattern: Optional[str] = None  # 参数模式 (如命令行正则)
    description: str = ""   # 规则描述 (供用户查看)


# ================================================================ 分类器

class AutoModeClassifier:
    """Auto Mode 权限分类器 (对标 Claude Code 2.1.239 Auto Mode)。

    用法:
        classifier = AutoModeClassifier(config)
        assessment = classifier.assess("run_shell", {"command": "git push"})
        if assessment.needs_confirm:
            # 需要用户确认
            ...
    """

    def __init__(self, config: Any = None) -> None:
        self.config = config
        self._rules: List[AutoModeRule] = self._load_rules()
        self._session_history: Dict[str, int] = {}  # 工具名 → 本会话调用次数
        self._confirmed_tools: Set[str] = set()      # 用户本次会话已确认的工具 (同类不再重复确认)

    def _get(self, key: str, default: Any = Any) -> Any:
        if self.config is None:
            return default
        value = self.config.get(key, default)
        return default if value is None else value

    def _load_rules(self) -> List[AutoModeRule]:
        """从配置加载自定义 auto mode 规则。"""
        raw_rules = self._get("permissions.auto_mode_rules", [])
        rules: List[AutoModeRule] = []
        for r in raw_rules:
            if not isinstance(r, dict):
                continue
            tool = str(r.get("tool", "")).strip()
            action = str(r.get("action", "")).strip().lower()
            if not tool or action not in ("allow", "deny", "ask"):
                continue
            rules.append(AutoModeRule(
                tool_pattern=tool,
                action=action,
                pattern=r.get("pattern"),
                description=str(r.get("description", "")),
            ))
        return rules

    def assess(self, tool_name: str, args: Dict[str, Any]) -> RiskAssessment:
        """评估单次工具调用的风险等级并给出决策。

        决策流程:
        1. 用户自定义规则表 (最高优先级)
        2. Shell 命令危险模式匹配 (仅 run_shell/run_terminal_command)
        3. 工具基线风险分类
        4. 会话历史 (重复操作降低确认需求)
        """
        # 1. 用户自定义规则
        rule_decision = self._match_rules(tool_name, args)
        if rule_decision is not None:
            return rule_decision

        # 2. Shell 命令分析
        if tool_name in ("run_shell", "run_terminal_command"):
            shell_assessment = self._assess_shell_command(args)
            if shell_assessment is not None:
                return shell_assessment

        # 3. 工具基线风险
        baseline = TOOL_BASELINE_RISK.get(tool_name, RiskLevel.MODERATE)

        # 4. 会话历史: 同一工具已确认过的, 降级为自动批准
        self._session_history[tool_name] = self._session_history.get(tool_name, 0) + 1
        if tool_name in self._confirmed_tools and self._session_history[tool_name] > 1:
            return RiskAssessment(
                level=baseline,
                reason=f"同类工具 {tool_name} 本会话已确认过 (第 {self._session_history[tool_name]} 次)",
                tool_name=tool_name,
                auto_approve=True,
                needs_confirm=False,
                rule_source="session_history",
            )

        # 5. 按风险等级决策
        return self._decide_by_level(baseline, tool_name)

    def _match_rules(self, tool_name: str, args: Dict[str, Any]) -> Optional[RiskAssessment]:
        """匹配用户自定义规则表, 返回命中结果或 None。"""
        target = self._rule_target(tool_name, args)
        hits: List[Tuple[str, str]] = []  # (action, description)

        for rule in self._rules:
            if not fnmatch.fnmatch(tool_name.lower(), rule.tool_pattern.lower()):
                continue
            if rule.pattern and not fnmatch.fnmatch(target, rule.pattern.lower()):
                continue
            hits.append((rule.action, rule.description))

        if not hits:
            return None

        # deny > ask > allow
        for priority_action in ("deny", "ask", "allow"):
            for action, desc in hits:
                if action == priority_action:
                    return RiskAssessment(
                        level=RiskLevel.CRITICAL if action == "deny" else RiskLevel.MODERATE,
                        reason=f"auto mode 规则: {desc or action} {tool_name}",
                        tool_name=tool_name,
                        auto_approve=(action == "allow"),
                        needs_confirm=(action == "ask"),
                        rule_source="auto_mode_rules",
                    )
        return None

    def _assess_shell_command(self, args: Dict[str, Any]) -> Optional[RiskAssessment]:
        """分析 shell 命令的危险模式。"""
        command = str(args.get("command", ""))
        if not command.strip():
            return None

        # 检查危险模式
        for pattern, level, desc in SHELL_RISK_PATTERNS:
            if pattern.search(command):
                return RiskAssessment(
                    level=level,
                    reason=f"Shell 命令风险: {desc} — `{command[:100]}`",
                    tool_name="run_shell",
                    matched_pattern=desc,
                    auto_approve=(level <= RiskLevel.LOW),
                    needs_confirm=(level >= RiskLevel.MODERATE),
                    rule_source="shell_pattern",
                )

        # 无危险模式匹配 → LOW 风险 (普通命令)
        return RiskAssessment(
            level=RiskLevel.LOW,
            reason=f"普通 shell 命令: `{command[:80]}`",
            tool_name="run_shell",
            auto_approve=True,
            needs_confirm=False,
            rule_source="shell_default",
        )

    def _decide_by_level(self, level: RiskLevel, tool_name: str) -> RiskAssessment:
        """按风险等级给出默认决策。"""
        if level <= RiskLevel.LOW:
            return RiskAssessment(
                level=level,
                reason=f"低风险工具: {tool_name}",
                tool_name=tool_name,
                auto_approve=True,
                needs_confirm=False,
                rule_source="baseline",
            )
        elif level == RiskLevel.MODERATE:
            return RiskAssessment(
                level=level,
                reason=f"中等风险工具: {tool_name}",
                tool_name=tool_name,
                auto_approve=False,
                needs_confirm=True,
                rule_source="baseline",
            )
        else:  # HIGH / CRITICAL
            return RiskAssessment(
                level=level,
                reason=f"高风险工具: {tool_name}",
                tool_name=tool_name,
                auto_approve=False,
                needs_confirm=True,
                rule_source="baseline",
            )

    def confirm_tool(self, tool_name: str) -> None:
        """标记工具已确认 (本会话后续同类操作自动批准)。"""
        self._confirmed_tools.add(tool_name)

    def reset_session(self) -> None:
        """重置会话状态 (新会话开始时调用)。"""
        self._session_history.clear()
        self._confirmed_tools.clear()
        self._rules = self._load_rules()

    @staticmethod
    def _rule_target(tool_name: str, args: Dict[str, Any]) -> str:
        """提取规则匹配文本 (小写)。"""
        if tool_name in ("run_shell", "run_terminal_command"):
            return str(args.get("command", "")).lower()
        for key in ("path", "url"):
            if key in args:
                return str(args[key]).lower()
        return " ".join(str(v) for v in args.values()).lower()


# ================================================================ 便捷函数

def assess_tool_risk(
    tool_name: str,
    args: Dict[str, Any],
    config: Any = None,
) -> RiskAssessment:
    """一次性评估工具风险 (无状态, 适合快速查询)。"""
    classifier = AutoModeClassifier(config)
    return classifier.assess(tool_name, args)


def is_auto_approvable(
    tool_name: str,
    args: Dict[str, Any],
    config: Any = None,
) -> bool:
    """快速判断工具是否可自动批准。"""
    assessment = assess_tool_risk(tool_name, args, config)
    return assessment.auto_approve
