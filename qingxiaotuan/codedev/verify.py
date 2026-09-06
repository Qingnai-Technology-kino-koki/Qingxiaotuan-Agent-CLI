"""codedev.verify —— 确定性验证闸门（build / test / lint）。

设计目标（非 loop）：
    弱模型写完代码不会自己编译。Claude Code 的「写→跑→看错→改」之所以强, 关键不在模型
    聪明, 而在**反馈是结构化的、精确到 file:line 的**。本模块把 pytest/mypy/ruff/tsc/...
    的输出解析成统一 Diagnostic, 并附「常见错误 → 修复提示」, 让模型只需对着具体报错改,
    而不是凭空「再优化一下」。

成本特征：纯 subprocess + 正则解析, 零额外模型调用。一次验证 ≈ 一次工具调用。
"""

from __future__ import annotations

import os
import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# 每种检测器的「命令探测 + 输出解析」描述
_DETECTORS = [
    # name, 探测文件, 命令, 解析函数(行->Diagnostic|None), 失败是否致命
    ("ruff", ("pyproject.toml", "ruff.toml", ".ruff.toml"), ["ruff", "check", "."], "ruff"),
    ("mypy", ("mypy.ini", "pyproject.toml"), ["mypy", "."], "mypy"),
    ("pytest", ("pytest.ini", "pyproject.toml", "tests"), ["python", "-m", "pytest", "-q"], "pytest"),
    ("flake8", ("setup.cfg", ".flake8", "tox.ini"), ["flake8", "."], "flake8"),
    ("tsc", ("tsconfig.json",), ["npx", "tsc", "--noEmit"], "tsc"),
    ("eslint", (".eslintrc", ".eslintrc.js", ".eslintrc.json"), ["npx", "eslint", "."], "eslint"),
    ("go_vet", ("go.mod",), ["go", "vet", "./..."], "go"),
    ("cargo_check", ("Cargo.toml",), ["cargo", "check"], "cargo"),
]


@dataclass
class Diagnostic:
    file: str
    line: int
    col: int
    severity: str            # error | warning | info
    code: str               # 规则号, 如 F821 / TS2304
    message: str
    hint: str = ""          # 常见修复提示

    def as_line(self) -> str:
        loc = f"{self.file}:{self.line}" + (f":{self.col}" if self.col else "")
        base = f"[{self.severity}] {loc} ({self.code}) {self.message}"
        return base + (f"  → {self.hint}" if self.hint else "")


@dataclass
class VerificationReport:
    cwd: str
    ran: List[str] = field(default_factory=list)        # 实际执行的检测器
    diagnostics: List[Diagnostic] = field(default_factory=list)
    elapsed_ms: float = 0.0
    raw: Dict[str, str] = field(default_factory=dict)   # 检测器 → 原始输出(截断)

    @property
    def ok(self) -> bool:
        return not any(d.severity == "error" for d in self.diagnostics)

    @property
    def error_count(self) -> int:
        return sum(1 for d in self.diagnostics if d.severity == "error")

    def by_file(self) -> Dict[str, List[Diagnostic]]:
        out: Dict[str, List[Diagnostic]] = {}
        for d in self.diagnostics:
            out.setdefault(d.file, []).append(d)
        return out

    def summary(self, max_lines: int = 40) -> str:
        head = f"# 验证报告 (cwd={self.cwd}, 检测器={','.join(self.ran) or '无'})\n"
        if not self.diagnostics:
            head += "✅ 无错误/警告（或所选检测器未报告问题）\n"
            return head
        head += f"共 {len(self.diagnostics)} 条诊断, 其中 {self.error_count} 个 error。\n"
        for d in self.diagnostics[:max_lines]:
            head += "- " + d.as_line() + "\n"
        if len(self.diagnostics) > max_lines:
            head += f"... 另有 {len(self.diagnostics) - max_lines} 条省略\n"
        return head


# 常见错误 → 修复提示（按 code 命中；弱模型拿到直接照做）
_HINTS: Dict[str, str] = {
    "F401": "该导入未使用, 删除或移动到 __all__",
    "F811": "重复定义/重名, 改名或删除其一",
    "F821": "未定义名称, 需 import 或拼写修正",
    "F841": "局部变量赋值后未使用, 删除或使用它",
    "E501": "行超长, 拆行或提高 max-line-length",
    "E302": "函数/类前缺两个空行",
    "E501": "行超长",
    "W293": "空白行含尾随空格, 剔除",
    "TS2304": "找不到名称, 添加 import 或声明类型",
    "TS2322": "类型不匹配, 修正赋值两边类型",
    "TS7006": "隐式 any 参数, 显式标注类型",
    "go_undef": "未定义标识符, 检查拼写/导入",
    "E999": "语法错误, 先修复语法",
}

_LINE_PATTERNS: Dict[str, re.Pattern] = {
    "ruff": re.compile(r"^(?P<file>[^:\n]+):(?P<line>\d+):(?P<col>\d+): (?:(?P<sev>error|warning): )?(?P<code>\w+) (?P<msg>.*)$"),
    "flake8": re.compile(r"^(?P<file>[^:\n]+):(?P<line>\d+):(?P<col>\d+): (?:(?P<sev>error|warning): )?(?P<code>\w+) (?P<msg>.*)$"),
    "mypy": re.compile(r"^(?P<file>[^:\n]+):(?P<line>\d+):(?P<col>\d+): (?P<sev>error|warning|note): (?P<msg>.*)$"),
    "tsc": re.compile(r"^(?P<file>[^(]+\.tsx?)\((?P<line>\d+),(?P<col>\d+)\): (?P<sev>error|warning) (?P<code>TS\d+): (?P<msg>.*)$"),
    "eslint": re.compile(r"^(?P<file>[^:\n]+):(?P<line>\d+):(?P<col>\d+): (?P<sev>error|warning) (?P<code>[^/\s]+) (?P<msg>.*)$"),
    "go": re.compile(r"^(?P<file>[^:\n]+):(?P<line>\d+):(?P<col>\d+): (?P<sev>error|warning):? (?P<msg>.*)$"),
    "cargo": re.compile(r"^(?P<file>[^:\n]+):(?P<line>\d+):(?P<col>\d+) (?P<sev>error|warning)\[(?P<code>[^\]]+)\] (?P<msg>.*)$"),
}


def _parse(name: str, text: str) -> List[Diagnostic]:
    pat = _LINE_PATTERNS.get(name)
    if not pat:
        return []
    out: List[Diagnostic] = []
    for line in text.splitlines():
        m = pat.match(line.strip())
        if not m:
            continue
        d = m.groupdict()
        code = d.get("code") or ""
        sev = d.get("sev")
        if sev in ("error",):
            severity = "error"
        elif sev in ("warning",):
            severity = "warning"
        else:
            # 无显式 severity（ruff/flake8）：W 前缀为 warning, 其余为 error
            severity = "warning" if code.startswith("W") else "error"
        out.append(Diagnostic(
            file=d.get("file", ""),
            line=int(d.get("line") or 0),
            col=int(d.get("col") or 0),
            severity=severity,
            code=code,
            message=d.get("msg", "").strip(),
            hint=_HINTS.get(code, ""),
        ))
    return out


class Verifier:
    """运行项目检测器并解析成统一 Diagnostic。"""

    def __init__(self, allow: Optional[List[str]] = None, deny: Optional[List[str]] = None,
                 timeout: float = 120.0) -> None:
        self.allow = allow
        self.deny = deny
        self.timeout = timeout

    def _select(self, cwd: str) -> List[Tuple[str, List[str]]]:
        chosen: List[Tuple[str, List[str]]] = []
        for name, markers, cmd, _ in _DETECTORS:
            if self.allow and name not in self.allow:
                continue
            if self.deny and name in self.deny:
                continue
            if any((Path(cwd) / m).exists() for m in markers):
                chosen.append((name, cmd))
        return chosen

    def verify(self, cwd: str | Path, extra_commands: Optional[List[List[str]]] = None) -> VerificationReport:
        cwd = str(cwd)
        t0 = time.time()
        report = VerificationReport(cwd=cwd)
        commands = self._select(cwd)
        if extra_commands:
            commands += [("custom", c) for c in extra_commands]

        for name, cmd in commands:
            try:
                proc = subprocess.run(
                    cmd, cwd=cwd, capture_output=True, text=True,
                    timeout=self.timeout, shell=False,
                )
            except (FileNotFoundError, subprocess.TimeoutExpired) as e:
                report.raw[name] = f"(跳过: {type(e).__name__})"
                continue
            out = (proc.stdout or "") + "\n" + (proc.stderr or "")
            report.ran.append(name)
            report.raw[name] = out[:4000]
            diag = _parse(name, out)
            report.diagnostics.extend(diag)
        report.elapsed_ms = (time.time() - t0) * 1000
        return report

    @staticmethod
    def available() -> List[str]:
        """当前环境实际可用的检测器（命令在 PATH 中）。"""
        import shutil
        out: List[str] = []
        for name, _, cmd, _ in _DETECTORS:
            exe = cmd[0]
            if exe in ("npx", "go", "cargo"):
                # 这些通常已装, 但需项目标记才启用, 这里只探测可执行
                if shutil.which(exe):
                    out.append(name)
            elif shutil.which(exe):
                out.append(name)
        return out
