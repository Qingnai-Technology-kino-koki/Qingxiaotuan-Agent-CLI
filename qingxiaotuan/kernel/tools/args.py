"""args —— 工具参数的解析与校验。

只保留测试与权限策略真正用到的最小能力：
- ``parse_tool_call_arguments``：对坏 JSON 容错，绝不抛出。
- ``validate_tool_args``：基于 JSON Schema 的轻量校验（不引入 ajv）。
- ``PathSecurityError``：路径不安全时抛出（兼容上游错误名）。
- ``is_sensitive_file``：敏感文件判定（供 sensitive-file-access-ask 策略使用）。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Optional


# ------------------------------------------------------------ 参数解析（容错）

@dataclass
class ParseToolArgsResult:
    data: Any
    parse_failed: bool
    error: Optional[str] = None


def parse_tool_call_arguments(raw: Any) -> ParseToolArgsResult:
    """解析模型给出的工具参数 JSON 字符串。

    等价于 TS ``parseToolCallArguments``：
    - None / 空串 / 空白 -> { data: {}, parseFailed: false }
    - 非字符串 -> 原样返回（已经是对象）
    - 字符串 -> 尝试 JSON.parse，失败则返回 parseFailed=True、data={}
    """
    if raw is None or (isinstance(raw, str) and raw.strip() == ""):
        return ParseToolArgsResult(data={}, parse_failed=False)
    if not isinstance(raw, str):
        return ParseToolArgsResult(data=raw, parse_failed=False)
    try:
        return ParseToolArgsResult(data=json.loads(raw), parse_failed=False)
    except Exception as exc:  # noqa: BLE001
        return ParseToolArgsResult(data={}, parse_failed=True, error=str(exc))


# ------------------------------------------------------------ 轻量 schema 校验

# 仅实现与工具参数最相关的几个类型校验，覆盖绝大多数字段即可。
_TYPE_CHECKS = {
    "string": lambda v: isinstance(v, str),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "array": lambda v: isinstance(v, list),
    "object": lambda v: isinstance(v, dict),
    "null": lambda v: v is None,
}


def validate_tool_args(schema: Any, args: Any) -> Optional[str]:
    """校验 args 是否满足 schema。返回错误字符串，或 None 表示通过。"""
    if not isinstance(schema, dict) or not isinstance(args, dict):
        return None
    required = schema.get("required") or []
    for req in required:
        if req not in args:
            return f"missing required property '{req}'"
    props = schema.get("properties") or {}
    for key, value in args.items():
        prop = props.get(key)
        if not isinstance(prop, dict):
            continue
        expected = prop.get("type")
        if expected is None:
            continue
        checker = _TYPE_CHECKS.get(expected)
        if checker is None:
            continue
        if not checker(value):
            return f"'{key}' must be {expected}"
    return None


# ------------------------------------------------------------ 路径安全

class PathSecurityError(Exception):
    """路径越界 / 命中敏感文件 / 非法路径时抛出。"""

    def __init__(self, code: str, raw_path: str, canonical_path: str, message: str) -> None:
        super().__init__(message)
        self.name = "PathSecurityError"
        self.code = code
        self.raw_path = raw_path
        self.canonical_path = canonical_path


_SENSITIVE_BASENAMES = {
    ".env",
    "id_rsa",
    "id_ed25519",
    "id_ecdsa",
    "credentials",
}
_SENSITIVE_PREFIXES = ("id_rsa", "id_ed25519", "id_ecdsa", "credentials")
_SENSITIVE_DOT_SUFFIXES = {
    ".bak",
    ".backup",
    ".copy",
    ".disabled",
    ".key",
    ".old",
    ".orig",
    ".pem",
    ".save",
    ".tmp",
}
_SENSITIVE_PATH_SUFFIXES = [".aws/credentials", ".gcp/credentials"]
_ENV_PREFIX = ".env."
_ENV_EXEMPTIONS = {".env.example", ".env.sample", ".env.template"}
_PUBLIC_KEY_BASENAMES = {"id_rsa.pub", "id_ed25519.pub", "id_ecdsa.pub"}


def is_sensitive_file(path: str) -> bool:
    """判断路径是否命中敏感文件模式（env / 凭据 / SSH key 等）。"""
    import os

    name = os.path.basename(path)
    comp_name = name.lower()
    comp_path = path.lower().replace("\\", "/")

    if comp_name in _ENV_EXEMPTIONS:
        return False
    if comp_name in _PUBLIC_KEY_BASENAMES:
        return False
    if comp_name in _SENSITIVE_BASENAMES:
        return True
    if comp_name.startswith(_ENV_PREFIX):
        return True

    for prefix in _SENSITIVE_PREFIXES:
        if comp_name == prefix:
            return True
        if comp_name.startswith(prefix):
            rest = comp_name[len(prefix):]
            if rest and (rest[0] == "-" or rest[0] == "_"):
                return True
            if rest.startswith(".") and rest in _SENSITIVE_DOT_SUFFIXES:
                return True

    for suffix in _SENSITIVE_PATH_SUFFIXES:
        if comp_path.endswith("/" + suffix) or (f"/{suffix}/" in comp_path):
            return True

    return False


# 供 rule-match 使用的小工具：把规则 subject 与值做 glob 匹配（带 ! 取反）。
_GLOB_SPECIAL = re.compile(r"[\\*?[\]{}()!+@|]")


def literal_rule_pattern(tool_name: str, subject: str) -> str:
    escaped = _GLOB_SPECIAL.sub(r"\\\g<0>", subject)
    return f"{tool_name}({escaped})"


def matches_glob_rule_subject(rule_args: str, subject: str) -> bool:
    """对齐 TS ``matchesGlobRuleSubject``：空串匹配所有；! 开头取反。"""
    negated = rule_args.startswith("!")
    positive = rule_args[1:] if negated else rule_args
    hit = _glob_match(subject, positive)
    return (not hit) if negated else hit


def _glob_match(value: str, pattern: str) -> bool:
    """极简 glob：支持 * 与 ?（不引入第三方依赖）。"""
    if pattern == value:
        return True
    if "*" not in pattern and "?" not in pattern:
        return False
    regex = "^" + re.escape(pattern).replace(r"\*", ".*").replace(r"\?", ".") + "$"
    return re.match(regex, value, re.IGNORECASE) is not None


# 用于构造会话级审批规则（简化）：从 execution.approval_rule 直接当字符串键。
def build_session_approval_rule(tool_name: str, rule: str) -> str:
    return f"{tool_name}:{rule}"


__all__ = [
    "ParseToolArgsResult",
    "parse_tool_call_arguments",
    "validate_tool_args",
    "PathSecurityError",
    "is_sensitive_file",
    "matches_glob_rule_subject",
    "literal_rule_pattern",
    "build_session_approval_rule",
]
