"""安全策略引擎 —— YAML 声明式安全规则，支持组织/用户/项目级策略。

核心理念:
- **策略即代码**: 用 YAML 定义安全策略，无需修改 Python 代码
- **三级策略**: 组织级 (强制) → 用户级 → 项目级 (可被覆盖)
- **规则类型**: 工具白名单/黑名单、命令模式匹配、网络域名控制、文件路径限制
- **优先级**: deny > ask > allow (deny 始终最高，allow 最低)
- **fail-closed**: 未知规则类型一律拒绝

与现有安全层的关系:
- safety_engine: 静态命令分析 (内置模式库)
- security_gate: 统一裁决入口
- security_policy: 用户自定义策略 (叠加在内置规则之上)

用法::

    from qingxiaotuan.core.security_policy import SecurityPolicyEngine

    engine = SecurityPolicyEngine(home=Path("~/.qingxiaotuan"))

    # 评估工具调用
    verdict = engine.evaluate_tool("run_shell", {"command": "rm -rf /tmp"})
    if verdict.action == "deny":
        print(f"策略拒绝: {verdict.reason}")

    # 加载项目级策略
    engine.load_project_policy(Path("/path/to/project"))
"""
from __future__ import annotations

import copy
import fnmatch
import json
import logging
import os
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

log = logging.getLogger(__name__)


# ============================================================ 数据类

@dataclass
class PolicyRule:
    """单条安全策略规则。"""
    id: str
    name: str
    action: str                    # deny / ask / allow
    priority: int = 100            # 越小优先级越高
    enabled: bool = True
    description: str = ""

    # 匹配条件 (任一匹配即触发)
    tools: Optional[List[str]] = None          # 工具名匹配 (支持 * 通配)
    patterns: Optional[List[str]] = None       # 正则模式匹配 (匹配参数文本)
    file_paths: Optional[List[str]] = None     # 文件路径匹配 (glob 模式)
    domains: Optional[List[str]] = None        # 网络域名匹配

    # 元数据
    source: str = "user"            # org / user / project / builtin
    created_at: float = 0.0
    tags: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "id": self.id, "name": self.name, "action": self.action,
            "priority": self.priority, "enabled": self.enabled,
            "description": self.description, "source": self.source,
            "created_at": self.created_at, "tags": self.tags,
        }
        if self.tools:
            d["tools"] = self.tools
        if self.patterns:
            d["patterns"] = self.patterns
        if self.file_paths:
            d["file_paths"] = self.file_paths
        if self.domains:
            d["domains"] = self.domains
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "PolicyRule":
        return cls(
            id=d.get("id", ""), name=d.get("name", ""),
            action=d.get("action", "ask"),
            priority=d.get("priority", 100),
            enabled=d.get("enabled", True),
            description=d.get("description", ""),
            tools=d.get("tools"),
            patterns=d.get("patterns"),
            file_paths=d.get("file_paths"),
            domains=d.get("domains"),
            source=d.get("source", "user"),
            created_at=d.get("created_at", 0.0),
            tags=d.get("tags", []),
        )


@dataclass
class PolicyVerdict:
    """策略评估结果。"""
    action: str = "allow"           # allow / ask / deny
    rule_id: str = ""               # 触发的规则 ID
    rule_name: str = ""             # 触发的规则名称
    reason: str = ""                # 拒绝/询问原因
    priority: int = 100
    source: str = ""

    def __bool__(self) -> bool:
        return self.action != "deny"


# ============================================================ 内置规则

_BUILTIN_RULES = [
    PolicyRule(
        id="builtin_no_rm_rf_root",
        name="禁止递归强删根目录",
        action="deny",
        priority=10,
        description="rm -rf / 永不自动执行",
        tools=["run_shell"],
        patterns=[r"\brm\s+-(?:r|f)*(?:r|f).*\s+/\s*$", r"\brm\s+-[a-zA-Z]*r[a-zA-Z]*f"],
        source="builtin",
    ),
    PolicyRule(
        id="builtin_no_force_push",
        name="禁止 git 强推",
        action="ask",
        priority=20,
        description="git push --force 需确认",
        tools=["run_shell"],
        patterns=[r"\bgit\s+push\b.*(?:--force|-f\b|\+\S+)"],
        source="builtin",
    ),
    PolicyRule(
        id="builtin_no_dd_device",
        name="禁止直接写磁盘设备",
        action="deny",
        priority=10,
        description="dd if=... of=/dev/sdX 永不自动执行",
        tools=["run_shell"],
        patterns=[r"\bdd\b.*\bof=/dev/"],
        source="builtin",
    ),
    PolicyRule(
        id="builtin_no_format",
        name="禁止格式化磁盘",
        action="deny",
        priority=10,
        description="format / mkfs 永不自动执行",
        tools=["run_shell"],
        patterns=[r"\b(?:format|mkfs)\b"],
        source="builtin",
    ),
    PolicyRule(
        id="builtin_no_shutdown",
        name="禁止系统关机",
        action="deny",
        priority=10,
        description="shutdown / reboot / halt 永不自动执行",
        tools=["run_shell"],
        patterns=[r"\b(?:shutdown|reboot|halt|poweroff)\b"],
        source="builtin",
    ),
    PolicyRule(
        id="builtin_env_file_protect",
        name="保护 .env 文件",
        action="deny",
        priority=5,
        description=".env 文件禁止读取/写入 (含 API 密钥)",
        file_paths=["*.env", ".env.*"],
        source="builtin",
    ),
    PolicyRule(
        id="builtin_ssh_protect",
        name="保护 SSH 密钥",
        action="deny",
        priority=5,
        description="SSH 密钥文件禁止读取/写入",
        file_paths=["*.pem", "id_rsa*", "id_ed25519*", ".ssh/*"],
        source="builtin",
    ),
]


# ============================================================ 策略引擎

class SecurityPolicyEngine:
    """安全策略引擎 —— YAML 声明式安全规则。

    用法::

        engine = SecurityPolicyEngine(home=Path("~/.qingxiaotuan"))

        # 评估工具调用
        verdict = engine.evaluate_tool("run_shell", {"command": "rm -rf /tmp"})

        # 加载自定义规则
        engine.load_rules_from_yaml(yaml_text, source="user")

        # 导出策略
        yaml_text = engine.export_rules()
    """

    def __init__(
        self,
        home: Optional[Path] = None,
        extra_builtin: Optional[List[PolicyRule]] = None,
    ) -> None:
        self._home = Path(home) if home else Path.home() / ".qingxiaotuan"
        self._rules: List[PolicyRule] = []
        self._lock = threading.Lock()
        self._compiled_patterns: Dict[str, re.Pattern] = {}

        # 加载内置规则
        self._rules.extend(_BUILTIN_RULES)
        if extra_builtin:
            self._rules.extend(extra_builtin)

        # 加载用户规则
        self._load_user_rules()

        # 按优先级排序
        self._sort_rules()

    # ------------------------------------------------------------ 评估

    def evaluate_tool(
        self,
        tool_name: str,
        args: Dict[str, Any],
        context: Optional[Dict[str, Any]] = None,
    ) -> PolicyVerdict:
        """评估工具调用是否符合策略。

        Args:
            tool_name: 工具名 (如 run_shell, write_file)
            args: 工具参数
            context: 可选上下文 (workspace, trust_level 等)

        Returns:
            PolicyVerdict: action=allow 放行, action=ask 需确认, action=deny 拒绝
        """
        context = context or {}

        with self._lock:
            rules = list(self._rules)

        # 收集所有匹配的规则
        matches: List[PolicyRule] = []
        for rule in rules:
            if not rule.enabled:
                continue
            if self._rule_matches(rule, tool_name, args, context):
                matches.append(rule)

        if not matches:
            return PolicyVerdict(action="allow")

        # 按优先级排序 (小数字 = 高优先级), deny 优先于 ask 优先于 allow
        matches.sort(key=lambda r: (
            {"deny": 0, "ask": 1, "allow": 2}.get(r.action, 3),
            r.priority,
        ))

        # 返回最高优先级的匹配结果
        winner = matches[0]
        return PolicyVerdict(
            action=winner.action,
            rule_id=winner.id,
            rule_name=winner.name,
            reason=winner.description or f"命中策略规则: {winner.name}",
            priority=winner.priority,
            source=winner.source,
        )

    def evaluate_command(self, command: str) -> PolicyVerdict:
        """评估 shell 命令。"""
        return self.evaluate_tool("run_shell", {"command": command})

    def evaluate_file_path(self, path: str, action: str = "read") -> PolicyVerdict:
        """评估文件路径。"""
        return self.evaluate_tool(f"{'write' if action == 'write' else 'read'}_file", {"path": path})

    def evaluate_content(self, content: str, path: str = "") -> PolicyVerdict:
        """评估文件内容 (写入前的内容安全扫描)。"""
        return self.evaluate_tool("write_file", {"path": path, "content": content})

    # ------------------------------------------------------------ 热加载

    def reload(self) -> int:
        """重新加载用户规则 (热加载)。"""
        with self._lock:
            # 保留内置规则
            builtin = [r for r in self._rules if r.source == "builtin"]
            self._rules = builtin
        self._compiled_patterns.clear()
        self._load_user_rules()
        self._sort_rules()
        return len(self._rules)

    def watch_rules_file(self, interval: float = 5.0) -> None:
        """启动规则文件监控线程 (可选)。"""
        import threading
        def _watcher():
            last_mtime = 0.0
            while True:
                try:
                    path = self._user_rules_path()
                    if path.exists():
                        mtime = path.stat().st_mtime
                        if mtime > last_mtime:
                            last_mtime = mtime
                            self.reload()
                            log.debug("安全策略规则已热加载")
                except Exception:
                    pass
                time.sleep(interval)
        t = threading.Thread(target=_watcher, daemon=True)
        t.start()

    # ------------------------------------------------------------ 规则管理

    def load_rules_from_yaml(self, yaml_text: str, source: str = "user") -> int:
        """从 YAML 加载规则。

        YAML 格式::

            rules:
              - id: no_delete_prod
                name: 禁止删除生产数据
                action: deny
                priority: 50
                tools: [run_shell]
                patterns:
                  - "DELETE\\s+FROM\\s+production"
                description: 生产环境数据禁止删除

        Returns:
            加载的规则数
        """
        try:
            import yaml
        except ImportError:
            log.warning("PyYAML 未安装, 无法加载 YAML 策略")
            return 0

        try:
            data = yaml.safe_load(yaml_text)
        except Exception as exc:
            log.error("YAML 解析失败: %s", exc)
            return 0

        if not isinstance(data, dict) or "rules" not in data:
            log.warning("YAML 格式错误: 缺少 'rules' 键")
            return 0

        rules_data = data["rules"]
        if not isinstance(rules_data, list):
            rules_data = [rules_data]

        count = 0
        new_rules: List[PolicyRule] = []
        for item in rules_data:
            if not isinstance(item, dict):
                continue
            try:
                rule = PolicyRule.from_dict(item)
                rule.source = source
                rule.created_at = rule.created_at or time.time()
                if rule.id and rule.name and rule.action in ("deny", "ask", "allow"):
                    new_rules.append(rule)
                    count += 1
            except Exception as exc:
                log.debug("规则解析失败: %s", exc)

        if new_rules:
            with self._lock:
                # 移除同 ID 的旧规则
                existing_ids = {r.id for r in new_rules}
                self._rules = [r for r in self._rules if r.id not in existing_ids]
                self._rules.extend(new_rules)
                self._sort_rules()
            self._save_user_rules()
            log.info("已加载 %d 条 %s 策略规则", count, source)

        return count

    def add_rule(self, rule: PolicyRule) -> None:
        """添加单条规则。"""
        with self._lock:
            self._rules = [r for r in self._rules if r.id != rule.id]
            self._rules.append(rule)
            self._sort_rules()
        if rule.source != "builtin":
            self._save_user_rules()

    def remove_rule(self, rule_id: str) -> bool:
        """移除规则。"""
        with self._lock:
            before = len(self._rules)
            self._rules = [r for r in self._rules if r.id != rule_id]
            removed = len(self._rules) < before
        if removed:
            self._save_user_rules()
        return removed

    def list_rules(self, source: Optional[str] = None) -> List[PolicyRule]:
        """列出规则。"""
        with self._lock:
            rules = list(self._rules)
        if source:
            rules = [r for r in rules if r.source == source]
        return rules

    def export_rules(self, source: Optional[str] = None) -> str:
        """导出规则为 YAML。"""
        rules = self.list_rules(source)
        try:
            import yaml
            data = {"rules": [r.to_dict() for r in rules]}
            return yaml.dump(data, allow_unicode=True, default_flow_style=False, sort_keys=False)
        except ImportError:
            return json.dumps({"rules": [r.to_dict() for r in rules]}, ensure_ascii=False, indent=2)

    def stats(self) -> Dict[str, Any]:
        """策略统计。"""
        with self._lock:
            rules = list(self._rules)
        by_action: Dict[str, int] = {}
        by_source: Dict[str, int] = {}
        for r in rules:
            by_action[r.action] = by_action.get(r.action, 0) + 1
            by_source[r.source] = by_source.get(r.source, 0) + 1
        return {
            "total": len(rules),
            "enabled": sum(1 for r in rules if r.enabled),
            "by_action": by_action,
            "by_source": by_source,
        }

    # ------------------------------------------------------------ 内部方法

    def _rule_matches(
        self,
        rule: PolicyRule,
        tool_name: str,
        args: Dict[str, Any],
        context: Dict[str, Any],
    ) -> bool:
        """检查规则是否匹配给定的工具调用。"""
        # 工具名匹配
        if rule.tools:
            matched = any(fnmatch.fnmatch(tool_name, p) for p in rule.tools)
            if not matched:
                return False

        # 提取文本参数用于模式匹配
        text = self._extract_text(args)

        # 正则模式匹配
        if rule.patterns and text:
            for pattern in rule.patterns:
                if self._pattern_matches(pattern, text):
                    return True
            if not rule.tools and not rule.file_paths and not rule.domains:
                return False

        # 文件路径匹配
        if rule.file_paths:
            path = args.get("path", args.get("file_path", ""))
            if path:
                for fp in rule.file_paths:
                    if fnmatch.fnmatch(path, fp) or fnmatch.fnmatch(os.path.basename(path), fp):
                        return True

        # 域名匹配
        if rule.domains:
            url = args.get("url", "")
            if url:
                from urllib.parse import urlparse
                hostname = urlparse(url).hostname or ""
                for domain in rule.domains:
                    if fnmatch.fnmatch(hostname, domain):
                        return True

        # 如果有工具名匹配但没有其他条件匹配, 且工具名确实匹配了, 规则生效
        if rule.tools and not rule.patterns and not rule.file_paths and not rule.domains:
            return any(fnmatch.fnmatch(tool_name, p) for p in rule.tools)

        return False

    def _pattern_matches(self, pattern: str, text: str) -> bool:
        """正则匹配, 带缓存。"""
        if pattern not in self._compiled_patterns:
            try:
                self._compiled_patterns[pattern] = re.compile(pattern, re.IGNORECASE)
            except re.error:
                return False
        return bool(self._compiled_patterns[pattern].search(text))

    @staticmethod
    def _extract_text(args: Dict[str, Any]) -> str:
        """从参数中提取文本。"""
        parts = []
        for key in ("command", "content", "text", "code", "sql", "script", "url"):
            val = args.get(key)
            if isinstance(val, str) and val.strip():
                parts.append(val)
        return "\n".join(parts)

    def _sort_rules(self) -> None:
        """按优先级排序。"""
        self._rules.sort(key=lambda r: r.priority)

    def _user_rules_path(self) -> Path:
        return self._home / "security_policy.json"

    def _save_user_rules(self) -> None:
        """保存用户/项目级规则。"""
        with self._lock:
            user_rules = [r for r in self._rules if r.source not in ("builtin",)]
        try:
            data = [r.to_dict() for r in user_rules]
            self._user_rules_path().write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except OSError as exc:
            log.debug("保存策略规则失败: %s", exc)

    def _load_user_rules(self) -> None:
        """加载用户级规则。"""
        path = self._user_rules_path()
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            for item in data:
                rule = PolicyRule.from_dict(item)
                if rule.id and rule.action in ("deny", "ask", "allow"):
                    self._rules.append(rule)
        except Exception:
            pass


# ============================================================ 模块级便捷函数

_global_policy_engine: Optional[SecurityPolicyEngine] = None


def get_policy_engine(home: Optional[Path] = None) -> SecurityPolicyEngine:
    """获取全局安全策略引擎单例。"""
    global _global_policy_engine
    if _global_policy_engine is None:
        _global_policy_engine = SecurityPolicyEngine(home=home)
    return _global_policy_engine
