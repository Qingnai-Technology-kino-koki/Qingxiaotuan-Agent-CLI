"""Python/TypeScript 工程诊断工具。

该工具只执行项目约定的本地质量命令，不接受任意 shell 字符串：Python 使用
`py_compile`/pytest，TypeScript 使用 package scripts 中的 typecheck/build/test。
命令输出用于 Agent 的验证闭环，失败只返回诊断文本，不抛出到主循环。
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any, Dict, List

from ..core.kernel import Kernel, Plugin
from .base import Tool, ToolContext


_COMMAND_TIMEOUT = 120
_MAX_OUTPUT = 12000


def _run(command: List[str], cwd: Path) -> str:
    try:
        result = subprocess.run(command, cwd=cwd, capture_output=True, text=True,
                                timeout=_COMMAND_TIMEOUT, errors="replace")
    except FileNotFoundError:
        return f"[未安装] {command[0]}"
    except subprocess.TimeoutExpired:
        return f"[超时] {' '.join(command)} ({_COMMAND_TIMEOUT}s)"
    output = (result.stdout or "") + (result.stderr or "")
    if len(output) > _MAX_OUTPUT:
        output = output[:_MAX_OUTPUT] + "\n...[输出已截断]"
    return f"$ {' '.join(command)}\nexit={result.returncode}\n{output.strip()}"


def project_languages(ctx: ToolContext) -> str:
    """识别工作区中的 Python/TypeScript 工程及其可用质量入口。"""
    root = Path(ctx.workspace)
    result: Dict[str, Any] = {"workspace": str(root), "languages": []}
    if (root / "pyproject.toml").exists() or any(root.rglob("*.py")):
        result["languages"].append({"name": "python", "checks": ["compile", "pytest"]})
    package = root / "package.json"
    if package.exists():
        try:
            data = json.loads(package.read_text(encoding="utf-8"))
            scripts = data.get("scripts", {}) if isinstance(data, dict) else {}
        except (OSError, json.JSONDecodeError):
            scripts = {}
        result["languages"].append({
            "name": "typescript" if (root / "tsconfig.json").exists() else "javascript",
            "checks": sorted(set(scripts) & {"typecheck", "build", "test", "lint"}),
        })
    return json.dumps(result, ensure_ascii=False, indent=2)


def language_checks(ctx: ToolContext, language: str = "auto") -> str:
    """运行 Python 或 TypeScript 项目的固定质量门禁。"""
    root = Path(ctx.workspace)
    available = []
    if (root / "pyproject.toml").exists() or any(root.rglob("*.py")):
        available.append("python")
    if (root / "package.json").exists() and (root / "tsconfig.json").exists():
        available.append("typescript")
    targets = available if language == "auto" else [language]
    if not targets:
        return "[提示] 未识别 Python 或 TypeScript 工程。"
    reports: List[str] = []
    for target in targets:
        if target == "python":
            reports.append(_run(["python", "-m", "compileall", "-q", "."], root))
            if (root / "tests").is_dir():
                reports.append(_run(["python", "-m", "pytest", "-q"], root))
        elif target == "typescript":
            reports.append(_run(["npm", "exec", "--", "tsc", "--noEmit"], root))
            reports.append(_run(["npm", "test", "--", "--runInBand"], root))
        else:
            reports.append(f"[错误] 不支持的语言: {target}")
    return "\n\n".join(reports)


class LanguagePlugin(Plugin):
    name = "tools.languages"
    requires = ["tool_registry"]

    def activate(self, kernel: Kernel) -> None:
        registry = kernel.require("tool_registry")
        registry.register(Tool(
            name="project_languages",
            description="识别工作区中的 Python/TypeScript 工程和质量检查入口",
            parameters={"type": "object", "properties": {}, "required": []},
            handler=project_languages, group="code",
        ))
        registry.register(Tool(
            name="language_checks",
            description="运行 Python 或 TypeScript 的固定编译/测试质量门禁",
            parameters={
                "type": "object",
                "properties": {"language": {"type": "string", "enum": ["auto", "python", "typescript"]}},
                "required": [],
            },
            handler=language_checks, group="code",
        ))
