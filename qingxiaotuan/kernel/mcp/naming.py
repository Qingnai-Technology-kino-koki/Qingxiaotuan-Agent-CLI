"""MCP 工具名限定 —— 把远端工具名规格化为稳定、可反查的限定名。

- 非字母数字字符 -> ``_``，连续 ``_`` 折叠为单个。
- 限定名 ``mcp__<sanitizedServer>__<sanitizedTool>``。
- 超过 64 字符时，对完整限定名做 FNV-1a（32 位）取低 8 位十六进制，
  截断为 ``<head>_<hash>``（与 TS ``stableHash8`` 行为一致，输出恒为 8 位小写 hex）。
"""

from __future__ import annotations

import re

MCP_NAME_PREFIX = "mcp__"
MCP_NAME_SEPARATOR = "__"
MAX_QUALIFIED_LENGTH = 64

_NON_ALNUM_RE = re.compile(r"[^A-Za-z0-9_-]")
_UNDERSCORE_RUN_RE = re.compile(r"_+")


def sanitize_mcp_name_part(part: str) -> str:
    """把 server / tool 名里的非 ``[A-Za-z0-9_-]`` 字符替换为 ``_`` 并折叠连续下划线。"""
    sanitized = _NON_ALNUM_RE.sub("_", part)
    return _UNDERSCORE_RUN_RE.sub("_", sanitized)


def _fnv1a_8(input_str: str) -> str:
    """FNV-1a 32 位哈希，返回 8 位小写十六进制（取低 8 位 hex）。

    忠实对应 TS ``stableHash8``：``hash ^= codePointAt(i); hash = imul(hash, 0x01000193)``。
    乘法按 32 位有符号整数折算，最终对 ``0xFFFFFFFF`` 取低 32 位后格式化为 8 位 hex。
    """
    hash_val = 0x811C9DC5
    for ch in input_str:
        hash_val ^= ord(ch)
        # 等价 Math.imul(hash, 0x01000193)，落在 32 位范围内
        hash_val = (hash_val * 0x01000193) & 0xFFFFFFFF
    return format(hash_val, "08x")


def qualify_mcp_tool_name(server_name: str, tool_name: str) -> str:
    """生成 MCP 工具在本地注册表里的限定名。"""
    full = (
        f"{MCP_NAME_PREFIX}{sanitize_mcp_name_part(server_name)}"
        f"{MCP_NAME_SEPARATOR}{sanitize_mcp_name_part(tool_name)}"
    )
    if len(full) <= MAX_QUALIFIED_LENGTH:
        return full
    hash_str = _fnv1a_8(full)
    head = full[: MAX_QUALIFIED_LENGTH - len(hash_str) - 1]
    return f"{head}_{hash_str}"


def is_mcp_tool_name(name: str) -> bool:
    """判断一个工具名是否由 ``qualify_mcp_tool_name`` 生成（以 ``mcp__`` 开头）。"""
    return name.startswith(MCP_NAME_PREFIX)
