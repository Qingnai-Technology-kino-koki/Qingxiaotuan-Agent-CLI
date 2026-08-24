"""自主反思循环 (Reflector) —— Plan → Execute → Reflect → Re-plan 闭环。

核心职责:
1. 读取任务与当前状态
2. 运行验证 (编译/测试/静态检查)
3. 分析验证结果, 自动诊断失败原因
4. 决策: 继续 / 修正 / 降级 / 请求用户帮助
5. 连续失败时自动降级: 缩小子任务粒度, 或请求用户确认

与 DevLoop 的关系:
- DevLoop 负责整体循环控制 (进度汇报/用户确认)
- Reflector 负责每轮执行后的反思与决策 (验证/诊断/降级)
- 两者协作形成: DevLoop(外层) + Reflector(内层反思) 的双层闭环
"""

from __future__ import annotations

import logging
import re
import subprocess
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

log = logging.getLogger("qingxiaotuan.reflector")


class ReflectDecision(str, Enum):
    """反思后的决策类型。"""
    CONTINUE = "continue"        # 验证通过, 继续下一轮
    FIX = "fix"                  # 验证失败, 需要修正
    DEGRADE = "degrade"          # 连续失败, 缩小子任务粒度
    ASK_USER = "ask_user"       # 无法自行解决, 请求用户帮助
    DONE = "done"                # 任务已完成


@dataclass
class VerificationResult:
    """单项验证结果。"""
    name: str                    # 验证名称 (如 "pytest", "ruff", "mypy")
    passed: bool
    exit_code: int = 0
    output: str = ""
    duration: float = 0.0       # 耗时 (秒)
    error_summary: str = ""     # 提取的关键错误信息


@dataclass
class ReflectResult:
    """反思结果。"""
    decision: ReflectDecision
    verifications: List[VerificationResult] = field(default_factory=list)
    diagnosis: str = ""          # 自动诊断说明
    suggested_fix: str = ""      # 建议的修正方案
    failure_count: int = 0       # 连续失败次数
    degraded_task: str = ""      # 降级后的子任务描述
    summary: str = ""            # 反思总结


class Verifier:
    """验证运行器: 自动运行编译/测试/静态检查, 返回结构化结果。

    支持的验证类型:
    - pytest: Python 测试
    - ruff: Python linter
    - mypy: Python 类型检查
    - npm_test: Node.js 测试
    - eslint: JavaScript/TypeScript linter
    - go_test: Go 测试
    - cargo_test: Rust 测试
    """

    def __init__(self, workspace: str, timeout: int = 120) -> None:
        self.workspace = Path(workspace)
        self.timeout = timeout

    def detect_available(self) -> List[str]:
        """检测工作区中可用的验证工具。"""
        available: List[str] = []
        root = self.workspace

        # Python 测试
        if (root / "pyproject.toml").exists() or (root / "pytest.ini").exists() \
                or (root / "tests").is_dir() or list(root.glob("test_*.py")):
            available.append("pytest")

        # Python linter
        if (root / "pyproject.toml").exists():
            try:
                content = (root / "pyproject.toml").read_text(encoding="utf-8")
                if "ruff" in content:
                    available.append("ruff")
                if "mypy" in content:
                    available.append("mypy")
                if "flake8" in content or (root / ".flake8").exists():
                    available.append("flake8")
            except OSError:
                pass

        # Node.js
        if (root / "package.json").exists():
            available.append("npm_test")
            if (root / ".eslintrc.json").exists() or (root / ".eslintrc.js").exists():
                available.append("eslint")

        # Go
        if (root / "go.mod").exists():
            available.append("go_test")

        # Rust
        if (root / "Cargo.toml").exists():
            available.append("cargo_test")

        return available

    def run_verification(self, name: str) -> VerificationResult:
        """运行单个验证工具并返回结构化结果。"""
        import time
        start = time.time()

        commands = {
            "pytest": "python -m pytest -x -q --tb=short 2>&1",
            "ruff": "python -m ruff check . --output-format=concise 2>&1",
            "mypy": "python -m mypy --ignore-missing-imports --no-error-summary . 2>&1",
            "flake8": "python -m flake8 --max-line-length=120 . 2>&1",
            "npm_test": "npm test 2>&1",
            "eslint": "npx eslint . --format=compact 2>&1",
            "go_test": "go test ./... 2>&1",
            "cargo_test": "cargo test 2>&1",
        }

        cmd = commands.get(name)
        if not cmd:
            return VerificationResult(
                name=name, passed=False, output=f"未知验证工具: {name}",
                error_summary=f"不支持的验证工具: {name}"
            )

        try:
            proc = subprocess.run(
                cmd, shell=True, cwd=str(self.workspace),
                capture_output=True, text=True, timeout=self.timeout,
                errors="replace",
            )
            output = (proc.stdout or "") + (proc.stderr or "")
            duration = time.time() - start
            passed = proc.returncode == 0
            error_summary = "" if passed else self._extract_error_summary(output, name)

            return VerificationResult(
                name=name, passed=passed, exit_code=proc.returncode,
                output=output[-4000:] if len(output) > 4000 else output,  # 截断过长输出
                duration=round(duration, 2), error_summary=error_summary,
            )
        except subprocess.TimeoutExpired:
            return VerificationResult(
                name=name, passed=False, output=f"[超时] {name} 在 {self.timeout}s 内未完成",
                error_summary=f"验证超时 ({self.timeout}s)",
                duration=float(self.timeout),
            )
        except Exception as exc:
            return VerificationResult(
                name=name, passed=False, output=str(exc),
                error_summary=f"执行异常: {type(exc).__name__}: {exc}",
                duration=time.time() - start,
            )

    def run_all(self, tools: Optional[List[str]] = None) -> List[VerificationResult]:
        """运行所有 (或指定) 验证工具。"""
        if tools is None:
            tools = self.detect_available()
        results: List[VerificationResult] = []
        for tool in tools:
            result = self.run_verification(tool)
            results.append(result)
            log.info("验证 %s: %s (exit=%d, %.1fs)",
                     tool, "✓ 通过" if result.passed else "✗ 失败",
                     result.exit_code, result.duration)
        return results

    @staticmethod
    def _extract_error_summary(output: str, tool: str) -> str:
        """从验证输出中提取关键错误信息。"""
        lines = output.strip().splitlines()
        error_lines: List[str] = []

        for line in lines[-30:]:  # 只看最后 30 行
            lower = line.lower()
            if any(kw in lower for kw in ("error", "failed", "failure", "traceback",
                                           "assert", "expected", "actual", "mismatch",
                                           "cannot find", "undefined", "not found")):
                error_lines.append(line.strip()[:200])

        if not error_lines and lines:
            # 没找到明确错误, 取最后几行
            error_lines = [l.strip()[:200] for l in lines[-5:] if l.strip()]

        return "\n".join(error_lines[:10])


class Reflector:
    """自主反思引擎: 验证→诊断→决策→降级。

    连续失败策略:
    - 第 1 次失败: 尝试自动修正 (fix)
    - 第 2 次失败: 缩小子任务粒度 (degrade)
    - 第 3 次及以上: 请求用户帮助 (ask_user)
    """

    def __init__(
        self,
        workspace: str,
        verify_tools: Optional[List[str]] = None,
        max_auto_fix: int = 2,
        timeout: int = 120,
    ) -> None:
        self.workspace = workspace
        self.verify_tools = verify_tools
        self.max_auto_fix = max_auto_fix
        self.verifier = Verifier(workspace, timeout=timeout)
        self._failure_streak = 0
        self._history: List[ReflectResult] = []

    @property
    def failure_count(self) -> int:
        return self._failure_streak

    @property
    def history(self) -> List[ReflectResult]:
        return list(self._history)

    def reset(self) -> None:
        """重置失败计数 (新任务开始时调用)。"""
        self._failure_streak = 0
        self._history.clear()

    def reflect(
        self,
        task: str,
        last_output: str = "",
        tools: Optional[List[str]] = None,
        on_verify: Optional[Callable[[str, bool], None]] = None,
    ) -> ReflectResult:
        """执行一轮反思: 运行验证 → 分析结果 → 做出决策。

        Args:
            task: 当前任务描述
            last_output: 上一轮的输出/汇报
            tools: 指定验证工具列表, None 则自动检测
            on_verify: 验证回调 (tool_name, passed) → UI 展示

        Returns:
            ReflectResult 包含决策、诊断和建议
        """
        # 1. 运行验证
        verify_tools = tools or self.verify_tools or self.verifier.detect_available()
        verifications: List[VerificationResult] = []

        if verify_tools:
            verifications = self.verifier.run_all(verify_tools)
            for v in verifications:
                if on_verify:
                    on_verify(v.name, v.passed)

        # 2. 分析结果
        all_passed = all(v.passed for v in verifications) if verifications else True
        failed = [v for v in verifications if not v.passed]

        # 3. 决策
        if all_passed:
            self._failure_streak = 0
            result = ReflectResult(
                decision=ReflectDecision.CONTINUE,
                verifications=verifications,
                diagnosis="所有验证通过",
                failure_count=0,
                summary=self._format_summary(verifications, "通过"),
            )
        else:
            self._failure_streak += 1
            diagnosis = self._diagnose_failures(failed, last_output)
            suggested_fix = self._suggest_fix(failed, diagnosis)

            if self._failure_streak >= 3:
                decision = ReflectDecision.ASK_USER
                summary = (f"连续 {self._failure_streak} 次验证失败, 需要用户介入。"
                           f"\n诊断: {diagnosis}")
            elif self._failure_streak >= self.max_auto_fix:
                decision = ReflectDecision.DEGRADE
                degraded_task = self._degrade_task(task, diagnosis)
                summary = (f"验证连续失败 {self._failure_streak} 次, 建议缩小子任务粒度。"
                           f"\n诊断: {diagnosis}\n降级任务: {degraded_task}")
            else:
                decision = ReflectDecision.FIX
                summary = (f"验证失败, 尝试自动修正 (第 {self._failure_streak} 次)。"
                           f"\n诊断: {diagnosis}\n建议: {suggested_fix}")

            result = ReflectResult(
                decision=decision,
                verifications=verifications,
                diagnosis=diagnosis,
                suggested_fix=suggested_fix,
                failure_count=self._failure_streak,
                degraded_task=self._degrade_task(task, diagnosis) if decision == ReflectDecision.DEGRADE else "",
                summary=summary,
            )

        self._history.append(result)
        return result

    def _diagnose_failures(
        self, failed: List[VerificationResult], last_output: str
    ) -> str:
        """自动诊断失败原因。"""
        diagnoses: List[str] = []

        for v in failed:
            if v.name == "pytest":
                if "ModuleNotFoundError" in v.output:
                    # 提取缺失模块名
                    match = re.search(r"ModuleNotFoundError: No module named '([^']+)'", v.output)
                    mod = match.group(1) if match else "unknown"
                    diagnoses.append(f"缺失 Python 模块: {mod} (需 pip install)")
                elif "ImportError" in v.output:
                    match = re.search(r"ImportError: cannot import name '([^']+)'", v.output)
                    name = match.group(1) if match else "unknown"
                    diagnoses.append(f"导入错误: 无法导入 '{name}', 可能接口已变更")
                elif "AssertionError" in v.output or "assert" in v.output.lower():
                    diagnoses.append("断言失败: 实际输出与预期不符")
                elif "FAILED" in v.output:
                    # 提取失败的测试名
                    fails = re.findall(r"FAILED\s+(\S+)", v.output)
                    if fails:
                        diagnoses.append(f"测试失败: {', '.join(fails[:3])}")
                    else:
                        diagnoses.append("测试用例失败")
            elif v.name in ("ruff", "flake8"):
                error_count = len(re.findall(r"^\S+:\d+:\d+: \w+", v.output, re.MULTILINE))
                diagnoses.append(f"Lint 错误: {error_count} 处需修复")
            elif v.name == "mypy":
                error_count = v.output.count("\n") - v.output.count(": note:")
                diagnoses.append(f"类型错误: {error_count} 处")
            elif v.name == "npm_test":
                if "Cannot find module" in v.output:
                    diagnoses.append("Node.js 模块缺失 (需 npm install)")
                elif "SyntaxError" in v.output:
                    diagnoses.append("JavaScript 语法错误")
                else:
                    diagnoses.append("npm 测试失败")
            elif v.name == "eslint":
                diagnoses.append("ESLint 检查失败")
            elif v.name == "go_test":
                if "undefined:" in v.output:
                    diagnoses.append("Go 编译错误: 未定义的符号")
                elif "FAIL" in v.output:
                    diagnoses.append("Go 测试失败")
            elif v.name == "cargo_test":
                if "error[" in v.output:
                    diagnoses.append("Rust 编译错误")
                elif "FAILED" in v.output:
                    diagnoses.append("Rust 测试失败")

        if not diagnoses:
            diagnoses.append("验证失败, 但无法自动识别具体原因")

        # 结合上一轮输出补充诊断
        if last_output:
            if "Traceback" in last_output:
                diagnoses.append("上一轮输出包含异常堆栈")
            if "Error" in last_output:
                diagnoses.append("上一轮输出包含错误信息")

        return "; ".join(diagnoses)

    def _suggest_fix(self, failed: List[VerificationResult], diagnosis: str) -> str:
        """根据诊断结果建议修正方案。"""
        suggestions: List[str] = []

        for v in failed:
            if v.name == "pytest":
                if "ModuleNotFoundError" in v.output:
                    match = re.search(r"No module named '([^']+)'", v.output)
                    mod = match.group(1) if match else "xxx"
                    suggestions.append(f"pip install {mod}")
                elif "SyntaxError" in v.output:
                    suggestions.append("检查并修复 Python 语法错误")
                elif "AssertionError" in v.output:
                    suggestions.append("检查测试预期值是否需要更新")
            elif v.name in ("ruff", "flake8"):
                suggestions.append("运行 ruff check --fix 自动修复 (或手动修复 lint 错误)")
            elif v.name == "mypy":
                suggestions.append("检查类型注解, 修复类型不匹配")
            elif v.name == "npm_test":
                if "Cannot find module" in v.output:
                    suggestions.append("npm install")
                else:
                    suggestions.append("检查 JavaScript/TypeScript 测试代码")
            elif v.name == "go_test":
                suggestions.append("go mod tidy && go test ./...")
            elif v.name == "cargo_test":
                suggestions.append("cargo build 检查编译错误")

        return "; ".join(suggestions) if suggestions else "请检查验证输出中的错误详情"

    def _degrade_task(self, task: str, diagnosis: str) -> str:
        """将任务降级为更小的子任务。"""
        # 简单的降级策略: 在原任务前加限定词
        if "测试" in task or "test" in task.lower():
            return f"仅修复验证失败项, 不做其他改动: {diagnosis}"
        if "编译" in task or "build" in task.lower():
            return f"仅修复编译错误: {diagnosis}"
        return f"缩小范围, 逐步修复以下问题: {diagnosis}"

    def _format_summary(self, verifications: List[VerificationResult], status: str) -> str:
        """格式化验证摘要。"""
        parts = []
        for v in verifications:
            icon = "✓" if v.passed else "✗"
            parts.append(f"  {icon} {v.name} ({v.duration:.1f}s)")
        return f"验证{status}:\n" + "\n".join(parts)

    def build_reflection_prompt(
        self, task: str, result: ReflectResult, last_output: str
    ) -> str:
        """构建反思提示, 供 Agent 在下一轮思考时参考。"""
        prompt_parts = [
            f"[反思报告] (连续失败 {result.failure_count} 次)",
            f"决策: {result.decision.value}",
        ]

        if result.verifications:
            prompt_parts.append("\n验证结果:")
            for v in result.verifications:
                icon = "✓" if v.passed else "✗"
                prompt_parts.append(f"  {icon} {v.name}: {'通过' if v.passed else '失败'}")
                if not v.passed and v.error_summary:
                    for line in v.error_summary.splitlines()[:3]:
                        prompt_parts.append(f"    {line}")

        if result.diagnosis:
            prompt_parts.append(f"\n诊断: {result.diagnosis}")

        if result.suggested_fix:
            prompt_parts.append(f"建议修正: {result.suggested_fix}")

        if result.decision == ReflectDecision.DEGRADE and result.degraded_task:
            prompt_parts.append(f"\n⚠️ 连续失败, 建议降级为更小的任务:")
            prompt_parts.append(f"  {result.degraded_task}")
            prompt_parts.append("请按此降级任务重新规划, 逐步修复问题。")

        if result.decision == ReflectDecision.ASK_USER:
            prompt_parts.append(f"\n🚨 连续 {result.failure_count} 次失败, 无法自行解决。")
            prompt_parts.append("请向用户说明问题, 请求帮助或指示。")

        return "\n".join(prompt_parts)
