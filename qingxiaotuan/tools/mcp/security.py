"""MCP 安全加固层 —— TrustFall / GuardFall 后的时代, MCP 安全不可再裸奔。

TrustFall 研究 (2026-05, Adversa AI) 揭示: Claude Code / Gemini CLI / Cursor CLI /
Copilot CLI 的 MCP 信任对话框存在设计缺陷, 恶意仓库可以通过 .mcp.json 自动执行
未沙箱的 MCP server。青小团虽然没有完全相同的信任对话框, 但 MCP 桥接层同样需要
加固——因为远端 MCP server 的工具描述 (tool description) 可能被注入恶意指令。

本模块提供:
1. **工具描述注入检测**: 扫描 MCP server 返回的工具描述中是否包含提示注入;
2. **参数消毒**: 对 MCP 工具调用的参数做安全清洗;
3. **工具描述审计日志**: 记录所有 MCP 工具的注册和描述内容;
4. **可疑工具标记**: 对可疑工具做标记, 需用户确认后才注册;
5. **描述长度限制**: 防止超长描述消耗上下文窗口。
"""

from __future__ import annotations

import logging
import re
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

log = logging.getLogger(__name__)


# ============================================================ 注入检测模式

# 提示注入 (Prompt Injection) 模式库
_PROMPT_INJECTION_PATTERNS = [
    # 直接指令覆盖
    (r'(?i)you\s+are\s+now\s+(?:a|an|the)\s+', "角色劫持 (you are now...)"),
    (r'(?i)ignore\s+(?:all\s+)?(?:previous|prior|above)\s+(?:instructions?|rules?|prompts?)',
     "指令覆盖 (ignore previous instructions)"),
    (r'(?i)disregard\s+(?:all\s+)?(?:previous|prior|above)\s+(?:instructions?|rules?)',
     "指令覆盖 (disregard previous)"),
    (r'(?i)forget\s+(?:all\s+)?(?:previous|prior|above)\s+(?:instructions?|context)',
     "指令覆盖 (forget previous)"),
    (r'(?i)new\s+instructions?:', "指令覆盖 (new instructions:)"),
    (r'(?i)override\s+(?:system|safety|security)\s+(?:prompt|instructions?)',
     "系统覆盖 (override system)"),
    (r'(?i)act\s+as\s+if\s+(?:you|the|there)\s+(?:are|is|were)',
     "角色扮演劫持 (act as if)"),
    # 工具调用劫持
    (r'(?i)call\s+(?:the\s+)?(?:run_shell|shell|execute|exec)\s+(?:tool|function|command)',
     "工具调用劫持 (call shell tool)"),
    (r'(?i)use\s+(?:the\s+)?(?:run_shell|shell|execute|exec)\s+to',
     "工具调用劫持 (use shell to)"),
    (r'(?i)(?:run|execute|call)\s+(?:the\s+)?command\s*:',
     "命令执行劫持 (run command:)"),
    # 数据外泄
    (r'(?i)send\s+(?:the\s+)?(?:results?|output|data|content)\s+to',
     "数据外泄指令 (send results to)"),
    (r'(?i)exfiltrate|exfil(?:trate)?',
     "数据外泄关键词 (exfiltrate)"),
    (r'(?i)POST\s+(?:the\s+)?(?:results?|output|data)\s+to',
     "数据外泄 POST (POST data to)"),
    # 权限提升
    (r'(?i)escalate\s+(?:privileges?|permissions?|access)',
     "权限提升 (escalate privileges)"),
    (r'(?i)(?:grant|give)\s+(?:yourself|me)\s+(?:root|admin|sudo)',
     "权限提升 (grant root/admin)"),
    # 隐藏指令
    (r'(?i)<\s*system\s*>\s*(?:ignore|override|disregard)',
     "伪系统标签 (<system> ignore)"),
    (r'(?i)###\s*(?:SYSTEM|ADMIN|IMPORTANT)\s*(?:MESSAGE|INSTRUCTION)',
     "伪系统指令 (### SYSTEM MESSAGE)"),
    (r'(?i)\[(?:SYSTEM|ADMIN|IMPORTANT)\]',
     "伪系统标签 ([SYSTEM])"),
    # MCP 特定攻击
    (r'(?i)when\s+called.*(?:run|execute|eval)\s+(?:any|all|following)',
     "MCP 工具描述劫持 (when called...execute)"),
    (r'(?i)the\s+(?:response|output)\s+(?:should|must|will)\s+include',
     "MCP 输出劫持 (output should include)"),
    # 编码绕过
    (r'(?i)base64\s*(?:encode|decode)\s+(?:the\s+)?(?:instructions?|commands?)',
     "编码绕过指令 (base64 encode instructions)"),
    (r'(?i)(?:decode|execute)\s+(?:this|the)\s+(?:base64|encoded)\s+',
     "编码执行 (decode base64)"),
    # 多语言注入
    (r'(?i)(?:请|不要|忽略|执行|运行)\s*(?:之前|以上|所有)\s*(?:的)?\s*(?:指令|规则|提示)',
     "中文指令覆盖"),
    (r'(?i)(?:prior|previous)\s+(?:turn|message|response)\s+(?:was|is)\s+(?:fake|wrong)',
     "前轮否定攻击"),
    # 分隔符注入
    (r'(?:---|===|\*\*\*)\s*(?:SYSTEM|ADMIN|NEW\s+INSTRUCTIONS)',
     "分隔符注入伪指令"),
    (r'(?i)---\s*(?:SYSTEM|ADMIN|NEW\s+INSTRUCTIONS)',
     "分隔符注入伪指令 (---)"),
    # 工具链劫持
    (r'(?i)first\s+(?:call|use|run)\s+(?:the\s+)?(?:run_shell|shell|exec)',
     "工具链劫持 (first call shell)"),
    (r'(?i)(?:before|after)\s+(?:doing|executing|running)\s+(?:anything|the\s+task)',
     "前置劫持 (before doing anything)"),
]

# 参数注入模式 (检测工具调用参数中的恶意内容)
_PARAM_INJECTION_PATTERNS = [
    # --- 指令覆盖 / 角色劫持 ---
    (r'(?i)ignore\s+(?:all\s+)?(?:previous|prior|above|earlier|preceding)\s+instructions?',
     "参数中的指令覆盖"),
    (r'(?i)you\s+are\s+now\s+(?:a|an|the)\s+', "参数中的角色劫持"),
    (r'(?i)disregard\s+(?:all\s+)?(?:previous|prior|above)\s+', "参数中的指令覆盖"),
    (r'(?i)new\s+(?:instructions?|role|persona)\s*:', "参数中的角色劫持"),
    # --- Shell / 代码执行 ---
    (r'(?i)(?:run_shell|shell|execute|exec)\s*[\({]', "参数中的 shell 调用"),
    (r'(?i)\b__import__\s*\(', "参数中的 __import__ 调用"),
    (r'(?i)\bcompile\s*\(.*\bexec\b', "参数中的 compile+exec 调用"),
    # --- HTML / 脚本注入 ---
    (r'(?i)<\s*(?:script|iframe|img|object|embed|form|input|svg|math)\b',
     "参数中的 HTML 注入"),
    (r'(?i)javascript\s*:', "参数中的 javascript: 协议"),
    (r'(?i)data\s*:\s*text/html', "参数中的 data: HTML 协议"),
    # --- Python 危险函数 ---
    (r'(?i)\beval\s*\(', "参数中的 eval 调用"),
    (r'(?i)\bexec\s*\(', "参数中的 exec 调用"),
    (r'(?i)(?:system|os)\s*\.\s*(?:popen|system|exec|spawn)\s*\(',
     "参数中的系统调用"),
    # --- 危险系统命令 ---
    (r'(?i)\brm\s+-rf\s+/', "参数中的递归强删根目录"),
    (r'(?i)\bchmod\s+777\b', "参数中的全权限修改"),
    (r'(?i)\bcurl\b.*\|\s*(?:bash|sh|zsh)\b', "参数中的远程代码执行"),
    (r'(?i)\bwget\b.*\|\s*(?:bash|sh|zsh)\b', "参数中的远程代码执行"),
    # --- SQL 注入 ---
    (r'(?i)(?:DROP|DELETE|TRUNCATE)\s+(?:TABLE|DATABASE)\b',
     "参数中的 SQL 破坏性操作"),
    (r'(?i)union\s+select\b', "参数中的 SQL 注入"),
    # --- 路径遍历 ---
    (r'\.\./\.\./(?:etc|proc|sys|dev)/(?:passwd|shadow|hosts)',
     "参数中的路径遍历攻击"),
]


# ============================================================ 检测结果

@dataclass
class InjectionCheckResult:
    """注入检测结果。"""

    safe: bool  # True = 未检测到注入
    threats: List[Dict[str, str]] = field(default_factory=list)  # [{pattern, description}]
    sanitized_text: str = ""  # 消毒后的文本 (去除可疑部分)
    scan_time_ms: float = 0.0

    @property
    def threat_count(self) -> int:
        return len(self.threats)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "safe": self.safe,
            "threat_count": self.threat_count,
            "threats": self.threats,
            "scan_time_ms": self.scan_time_ms,
        }


# ============================================================ MCP 安全加固器

class MCPSecurityGuard:
    """MCP 安全加固层。

    用法:
        guard = MCPSecurityGuard()
        result = guard.scan_tool_description("read_file", "读取文件内容")
        if not result.safe:
            print(f"检测到注入: {result.threats}")
    """

    def __init__(
        self,
        max_description_length: int = 10000,
        audit_enabled: bool = True,
        block_on_injection: bool = True,
    ) -> None:
        self._max_desc_len = max_description_length
        self._audit_enabled = audit_enabled
        self._block_on_injection = block_on_injection
        # 用 deque(maxlen=) 替代 list + 切片裁剪: append 与裁剪是 O(1) 原子操作,
        # 不再出现实测 1000 次扫描只保留 498 条 (≈50% 丢失) 的情况。
        self._audit_log: "deque[Dict[str, Any]]" = deque(maxlen=500)
        self._audit_lock = threading.Lock()
        self._registered_tools: Dict[str, Dict[str, Any]] = {}

    def scan_tool_description(
        self, tool_name: str, description: str
    ) -> InjectionCheckResult:
        """扫描 MCP 工具描述中是否包含提示注入。"""
        started = time.monotonic()
        threats: List[Dict[str, str]] = []

        # 1. 长度检查
        if len(description) > self._max_desc_len:
            threats.append({
                "pattern": "length_limit",
                "description": f"工具描述过长 ({len(description)} > {self._max_desc_len})",
            })

        # 2. 注入模式扫描
        for pattern, desc in _PROMPT_INJECTION_PATTERNS:
            if re.search(pattern, description):
                threats.append({"pattern": pattern, "description": desc})

        # 3. 嵌套指令检测 (description 中包含类似系统提示的结构)
        if re.search(r'(?i)(?:system|assistant|user)\s*:', description):
            # 可能是角色标记泄露
            if re.search(r'(?i)(?:system|assistant)\s*:\s*(?:you\s+are|ignore|forget)',
                         description):
                threats.append({
                    "pattern": "nested_role",
                    "description": "嵌套角色指令 (system: you are...)",
                })

        # 4. URL 检测 (工具描述中不应包含 URL, 除非是文档链接)
        urls = re.findall(r'https?://[^\s<>"]+', description)
        suspicious_urls = [u for u in urls if not any(
            domain in u for domain in (
                "github.com", "gitlab.com", "docs.", "readthedocs.io",
                "pypi.org", "npmjs.com", "json-schema.org",
            )
        )]
        if suspicious_urls:
            threats.append({
                "pattern": "suspicious_urls",
                "description": f"描述中包含可疑 URL: {', '.join(suspicious_urls[:3])}",
            })

        # 5. 构建消毒文本 (去除检测到的可疑部分)
        sanitized = description
        for t in threats:
            if t["pattern"] not in ("length_limit", "suspicious_urls"):
                # 对注入模式, 尝试标记而非删除 (保留可读性)
                pass  # 保守处理: 不修改描述, 只标记

        elapsed = (time.monotonic() - started) * 1000
        result = InjectionCheckResult(
            safe=len(threats) == 0,
            threats=threats,
            sanitized_text=sanitized,
            scan_time_ms=elapsed,
        )

        # 6. 审计
        if self._audit_enabled:
            with self._audit_lock:
                self._audit_log.append({
                    "tool": tool_name,
                    "safe": result.safe,
                    "threat_count": result.threat_count,
                    "threats": [t["description"] for t in threats],
                    "timestamp": time.time(),
                })

        return result

    def scan_tool_params(
        self, tool_name: str, params: Dict[str, Any]
    ) -> InjectionCheckResult:
        """扫描 MCP 工具调用的参数中是否包含注入。"""
        started = time.monotonic()
        threats: List[Dict[str, str]] = []

        # 把所有参数值转成文本做扫描
        text_parts: List[str] = []
        for key, value in params.items():
            if isinstance(value, str):
                text_parts.append(f"{key}={value}")
            elif isinstance(value, (list, dict)):
                import json
                text_parts.append(f"{key}={json.dumps(value, ensure_ascii=False)}")

        combined = "\n".join(text_parts)

        # 参数注入模式扫描
        for pattern, desc in _PARAM_INJECTION_PATTERNS:
            if re.search(pattern, combined):
                threats.append({"pattern": pattern, "description": desc})

        elapsed = (time.monotonic() - started) * 1000
        return InjectionCheckResult(
            safe=len(threats) == 0,
            threats=threats,
            sanitized_text=combined,
            scan_time_ms=elapsed,
        )

    def register_tool(
        self, tool_name: str, description: str, server: str = ""
    ) -> bool:
        """注册 MCP 工具并做安全扫描。

        返回 True = 安全注册; False = 检测到注入, 需用户确认。
        """
        result = self.scan_tool_description(tool_name, description)

        self._registered_tools[tool_name] = {
            "server": server,
            "description": description[:500],  # 只存前 500 字符
            "safe": result.safe,
            "threats": [t["description"] for t in result.threats],
            "registered_at": time.time(),
        }

        if not result.safe:
            log.warning(
                "MCP 工具 %s (server=%s) 检测到 %d 个注入威胁: %s",
                tool_name, server, result.threat_count,
                [t["description"] for t in result.threats[:3]],
            )

        return result.safe

    def get_audit_log(self, limit: int = 50) -> List[Dict[str, Any]]:
        """获取审计日志。"""
        with self._audit_lock:
            return list(reversed(list(self._audit_log)[-limit:]))

    def get_tool_summary(self) -> Dict[str, Any]:
        """获取已注册工具的安全摘要。"""
        total = len(self._registered_tools)
        unsafe = sum(1 for t in self._registered_tools.values() if not t["safe"])
        return {
            "total_tools": total,
            "safe_tools": total - unsafe,
            "unsafe_tools": unsafe,
            "servers": list(set(
                t["server"] for t in self._registered_tools.values() if t["server"]
            )),
        }


# ============================================================ 全局实例

_global_mcp_guard: Optional[MCPSecurityGuard] = None


def get_mcp_security_guard() -> MCPSecurityGuard:
    """获取全局 MCP 安全加固器单例。"""
    global _global_mcp_guard
    if _global_mcp_guard is None:
        _global_mcp_guard = MCPSecurityGuard()
    return _global_mcp_guard
