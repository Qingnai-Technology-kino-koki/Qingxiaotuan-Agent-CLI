"""代码评审自动化 —— 对生成/修改的代码做静态分析 + 逻辑审查 + 安全扫描。

能力:
- 静态分析: 检测代码风格、复杂度、重复代码
- 逻辑审查: 检查常见逻辑错误、边界条件
- 安全扫描: 检测潜在安全漏洞 (硬编码密钥、SQL 注入、XSS 等)
- 输出结构化评审报告, 不通过则可自动返工
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from .base import Tool, ToolContext, string_prop

log = logging.getLogger("qingxiaotuan.code_review")


@dataclass
class ReviewIssue:
    severity: str          # critical / warning / info
    category: str          # style / logic / security / complexity / performance
    file: str = ""
    line: int = 0
    message: str = ""
    suggestion: str = ""


@dataclass
class ReviewReport:
    issues: List[ReviewIssue] = field(default_factory=list)
    files_reviewed: int = 0
    total_lines: int = 0
    score: float = 100.0

    @property
    def critical_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == "critical")

    @property
    def warning_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == "warning")

    @property
    def info_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == "info")

    @property
    def passed(self) -> bool:
        return self.critical_count == 0

    def to_report(self) -> str:
        lines = [
            f"代码评审报告",
            f"{'=' * 40}",
            f"评审文件: {self.files_reviewed} 个, 共 {self.total_lines} 行",
            f"评分: {self.score:.0f}/100 {'✓ 通过' if self.passed else '✗ 需修复'}",
            f"问题: {self.critical_count} 严重 / {self.warning_count} 警告 / {self.info_count} 提示",
            "",
        ]

        if not self.issues:
            lines.append("  🎉 未发现任何问题!")
            return "\n".join(lines)

        for severity in ("critical", "warning", "info"):
            issues = [i for i in self.issues if i.severity == severity]
            if not issues:
                continue
            icon = {"critical": "🔴", "warning": "🟡", "info": "🔵"}[severity]
            lines.append(f"\n{icon} {severity.upper()} ({len(issues)}):")
            for issue in issues:
                loc = f"{issue.file}:{issue.line}" if issue.file else ""
                lines.append(f"  [{issue.category}] {loc} {issue.message}")
                if issue.suggestion:
                    lines.append(f"    💡 {issue.suggestion}")

        return "\n".join(lines)


_SECURITY_PATTERNS = [
    (r"""(?:password|passwd|pwd)\s*[:=]\s*['"][^'"]+['"]""", "硬编码密码"),
    (r"""(?:api_key|apikey|api[-_]?secret)\s*[:=]\s*['"][^'"]+['"]""", "硬编码 API 密钥"),
    (r"""(?:secret|token)\s*[:=]\s*['"][^'"]+['"]""", "硬编码密钥/令牌"),
    (r"""eval\s*\(""", "使用 eval() (潜在代码注入)"),
    (r"""exec\s*\(""", "使用 exec() (潜在代码注入)"),
    (r"""\bos\.system\s*\(""", "使用 os.system() (建议用 subprocess)"),
    (r"""subprocess\.call\s*\(.*shell\s*=\s*True""", "subprocess shell=True (潜在命令注入)"),
    (r"""SELECT\s+.*\s+FROM\s+.*\s+WHERE\s+.*\+""", "SQL 拼接 (潜在 SQL 注入)"),
    (r"""innerHTML\s*=""", "直接设置 innerHTML (潜在 XSS)"),
    (r"""document\.write\s*\(""", "使用 document.write() (潜在 XSS)"),
    (r"""pickle\.loads?\s*\(""", "反序列化 pickle (潜在安全风险)"),
    (r"""yaml\.load\s*\((?!.*Loader)""", "yaml.load 未指定 Loader (潜在安全风险)"),
]


def _scan_security(content: str, filepath: str) -> List[ReviewIssue]:
    issues: List[ReviewIssue] = []
    lines = content.splitlines()
    for line_num, line in enumerate(lines, 1):
        stripped = line.strip()
        if stripped.startswith("#") or stripped.startswith("//") or stripped.startswith("*"):
            continue
        for pattern, desc in _SECURITY_PATTERNS:
            if re.search(pattern, line, re.IGNORECASE):
                if "test" in filepath.lower() or "example" in filepath.lower():
                    continue
                issues.append(ReviewIssue(
                    severity="critical" if "密钥" in desc or "注入" in desc else "warning",
                    category="security",
                    file=filepath, line=line_num,
                    message=desc,
                    suggestion="避免在代码中硬编码敏感信息或使用危险函数",
                ))
                break
    return issues


def _scan_logic(content: str, filepath: str) -> List[ReviewIssue]:
    issues: List[ReviewIssue] = []
    lines = content.splitlines()
    for line_num, line in enumerate(lines, 1):
        stripped = line.strip()
        if re.search(r"==\s*None\b", stripped) or re.search(r"!=\s*None\b", stripped):
            issues.append(ReviewIssue(
                severity="warning", category="logic",
                file=filepath, line=line_num,
                message="使用 == None 而非 is None",
                suggestion="Python 中应使用 'is None' 或 'is not None'",
            ))
        if re.match(r"except\s*:", stripped) or re.match(r"except\s+Exception\s*:", stripped):
            if "# noqa" not in stripped:
                issues.append(ReviewIssue(
                    severity="info", category="logic",
                    file=filepath, line=line_num,
                    message="宽泛的异常捕获",
                    suggestion="考虑捕获更具体的异常类型",
                ))
        if re.search(r"#\s*(TODO|FIXME|HACK|XXX)\b", stripped, re.IGNORECASE):
            issues.append(ReviewIssue(
                severity="info", category="logic",
                file=filepath, line=line_num,
                message=f"待处理标记: {stripped[:80]}",
            ))
    return issues


def _scan_style(content: str, filepath: str) -> List[ReviewIssue]:
    issues: List[ReviewIssue] = []
    lines = content.splitlines()
    ext = Path(filepath).suffix
    for line_num, line in enumerate(lines, 1):
        max_len = 120
        if len(line) > max_len and not line.strip().startswith("#"):
            issues.append(ReviewIssue(
                severity="info", category="style",
                file=filepath, line=line_num,
                message=f"行长度 {len(line)} 超过 {max_len} 字符",
            ))
        if ext == ".py" and "\t" in line and "    " in line:
            issues.append(ReviewIssue(
                severity="warning", category="style",
                file=filepath, line=line_num,
                message="Tab 与空格混用",
            ))
    return issues


def _scan_complexity(content: str, filepath: str) -> List[ReviewIssue]:
    issues: List[ReviewIssue] = []
    lines = content.splitlines()
    func_start = 0
    func_name = ""
    in_func = False

    for line_num, line in enumerate(lines, 1):
        stripped = line.strip()
        if re.match(r"^(?:async\s+)?def\s+\w+", stripped):
            if in_func and (line_num - func_start) > 50:
                issues.append(ReviewIssue(
                    severity="warning", category="complexity",
                    file=filepath, line=func_start,
                    message=f"函数 '{func_name}' 长度 {line_num - func_start} 行, 建议拆分",
                ))
            func_start = line_num
            m = re.match(r"(?:async\s+)?def\s+(\w+)", stripped)
            func_name = m.group(1) if m else "unknown"
            in_func = True

    if in_func and (len(lines) - func_start) > 50:
        issues.append(ReviewIssue(
            severity="warning", category="complexity",
            file=filepath, line=func_start,
            message=f"函数 '{func_name}' 长度 {len(lines) - func_start} 行, 建议拆分",
        ))
    return issues


def _calculate_score(issues: List[ReviewIssue]) -> float:
    score = 100.0
    for issue in issues:
        if issue.severity == "critical":
            score -= 15
        elif issue.severity == "warning":
            score -= 5
        elif issue.severity == "info":
            score -= 1
    return max(0, score)


def _get_review_files(ctx: ToolContext, path: str = "", patterns: Optional[List[str]] = None) -> List[Path]:
    root = Path(ctx.workspace) / path if path else Path(ctx.workspace)
    if not root.exists():
        return []
    if patterns is None:
        patterns = ["*.py", "*.js", "*.ts", "*.jsx", "*.tsx", "*.go", "*.rs", "*.java"]
    files: List[Path] = []
    skip_dirs = {".git", ".venv", "venv", "node_modules", "__pycache__",
                 ".tox", ".mypy_cache", ".pytest_cache", "dist", "build"}
    for current, dirs, filenames in os.walk(root):
        dirs[:] = [d for d in dirs if d not in skip_dirs]
        for fname in filenames:
            fpath = Path(current) / fname
            for pat in patterns:
                if fpath.match(pat):
                    files.append(fpath)
                    break
        if len(files) > 100:
            break
    return files


def code_review(
    ctx: ToolContext,
    path: str = "",
    files: str = "",
    categories: str = "security,logic,style,complexity",
) -> str:
    """对代码进行自动评审。"""
    cats = [c.strip() for c in categories.split(",") if c.strip()]
    if files:
        file_list = [Path(ctx.workspace) / f.strip() for f in files.split(",")]
        file_list = [f for f in file_list if f.exists() and f.is_file()]
    else:
        file_list = _get_review_files(ctx, path)

    if not file_list:
        return "[提示] 未找到可评审的文件"

    report = ReviewReport(files_reviewed=len(file_list))

    for fpath in file_list:
        try:
            content = fpath.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        rel = str(fpath.relative_to(ctx.workspace))
        lines = content.splitlines()
        report.total_lines += len(lines)
        if "security" in cats:
            report.issues.extend(_scan_security(content, rel))
        if "logic" in cats:
            report.issues.extend(_scan_logic(content, rel))
        if "style" in cats:
            report.issues.extend(_scan_style(content, rel))
        if "complexity" in cats:
            report.issues.extend(_scan_complexity(content, rel))

    report.score = _calculate_score(report.issues)
    return report.to_report()


def code_review_quick(ctx: ToolContext, file_path: str = "") -> str:
    """快速评审单个文件。"""
    if not file_path:
        return "[错误] 请指定文件路径"
    fpath = Path(ctx.workspace) / file_path
    if not fpath.exists():
        return f"[错误] 文件不存在: {file_path}"
    try:
        content = fpath.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return f"[错误] 读取文件失败: {exc}"

    issues: List[ReviewIssue] = []
    issues.extend(_scan_security(content, file_path))
    issues.extend(_scan_logic(content, file_path))

    report = ReviewReport(
        issues=issues, files_reviewed=1,
        total_lines=len(content.splitlines()),
        score=_calculate_score(issues),
    )
    return report.to_report()


class CodeReviewPlugin:
    def __init__(self):
        pass

    def activate(self, kernel):
        pass


from ..core.kernel import Plugin as _Plugin  # noqa: E402


class CodeReviewPlugin(_Plugin):
    name = "tools.code_review"
    requires = ["tool_registry"]

    def activate(self, kernel) -> None:
        registry = kernel.require("tool_registry")
        registry.register(Tool(
            name="code_review",
            description="自动评审代码: 静态分析 + 逻辑审查 + 安全扫描, 输出评分报告",
            parameters={
                "type": "object",
                "properties": {
                    "path": string_prop("评审路径 (相对于工作区)"),
                    "files": string_prop("逗号分隔的文件路径列表"),
                    "categories": string_prop("评审类别 (security/logic/style/complexity)"),
                },
                "required": [],
            },
            handler=code_review, group="code",
        ))
        registry.register(Tool(
            name="code_review_quick",
            description="快速评审单个文件 (安全+逻辑检查)",
            parameters={
                "type": "object",
                "properties": {
                    "file_path": string_prop("文件路径 (相对于工作区)"),
                },
                "required": ["file_path"],
            },
            handler=code_review_quick, group="code",
        ))
