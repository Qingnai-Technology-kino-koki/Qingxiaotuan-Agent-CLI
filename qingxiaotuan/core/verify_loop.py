"""编码验证闭环 (Verify Loop) —— 对标 Claude Code 的编码验证能力。

设计:
- Agent 每次修改文件后 (write_file / str_replace / run_terminal_command 等写工具),
  可选触发 verify_loop: 自动跑 pytest / mypy / ruff, 失败则把错误信息喂回 Agent 让它自修复;
- 最多循环 MAX_HEAL_ROUNDS 轮, 超过则放弃并报告;
- 所有检查命令可按语言/框架自动推断, 也可由配置覆盖;
- 纯同步实现, 不引入重型依赖 (只用 subprocess).

配置 (config.yaml):
    verify:
      enabled: true           # 开关
      auto: true              # 写工具后自动触发 (需要 agent 主循环配合)
      max_heal_rounds: 3      # 最大自修复轮数
      checks:                 # 可覆盖检查命令 (留空=自动推断)
        test: ""
        typecheck: ""
        lint: ""
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, cast

log = logging.getLogger(__name__)

# 默认最大自修复轮数
MAX_HEAL_ROUNDS = 3

# 写类工具名集合: 这些工具执行后可触发验证闭环 (与 tools/ 实际注册名一致)
WRITE_TOOLS = {
    "write_file", "edit_file", "delete_file", "delete_dir", "move_file",
}


@dataclass
class VerifyConfig:
    """验证闭环配置。"""
    enabled: bool = True
    auto: bool = True
    max_heal_rounds: int = MAX_HEAL_ROUNDS
    test_cmd: str = ""
    typecheck_cmd: str = ""
    lint_cmd: str = ""

    @classmethod
    def from_dict(cls, cfg: Dict[str, Any]) -> "VerifyConfig":
        v = cfg.get("verify", {})
        checks = v.get("checks", {})
        return cls(
            enabled=v.get("enabled", True),
            auto=v.get("auto", True),
            max_heal_rounds=v.get("max_heal_rounds", MAX_HEAL_ROUNDS),
            test_cmd=checks.get("test", ""),
            typecheck_cmd=checks.get("typecheck", ""),
            lint_cmd=checks.get("lint", ""),
        )


@dataclass
class CheckResult:
    """单个检查的结果。"""
    name: str          # test / typecheck / lint
    cmd: str           # 实际执行的命令
    passed: bool
    output: str        # 标准输出 + 标准错误
    returncode: int = 0
    elapsed: float = 0.0


@dataclass
class VerifyRound:
    """一轮验证 (含多个检查) 的结果。"""
    round_num: int
    checks: List[CheckResult] = field(default_factory=list)

    @property
    def all_passed(self) -> bool:
        return all(c.passed for c in self.checks)

    @property
    def failures(self) -> List[CheckResult]:
        return [c for c in self.checks if not c.passed]

    def summary(self) -> str:
        parts = []
        for c in self.checks:
            status = "PASS" if c.passed else "FAIL"
            parts.append(f"  [{status}] {c.name}: {c.cmd}")
            if not c.passed:
                # 截取最后 N 行错误
                tail = c.output.strip().splitlines()[-20:]
                parts.append("    " + "\n    ".join(tail))
        return "\n".join(parts)


@dataclass
class VerifyReport:
    """验证闭环的完整报告。"""
    rounds: List[VerifyRound] = field(default_factory=list)
    healed: bool = False          # 是否通过自修复全部通过
    gave_up: bool = False        # 是否达到轮数上限放弃

    @property
    def total_rounds(self) -> int:
        return len(self.rounds)

    def summary(self) -> str:
        lines = [f"验证闭环: {self.total_rounds} 轮"]
        if self.healed:
            lines.append("  结果: 全部通过 (自修复成功)")
        elif self.gave_up:
            lines.append("  结果: 仍有失败 (已达修复上限)")
        else:
            lines.append("  结果: 首轮即全部通过")
        for rnd in self.rounds:
            lines.append(f"\n--- 第 {rnd.round_num} 轮 ---")
            lines.append(rnd.summary())
        return "\n".join(lines)


def detect_project_type(workspace: str) -> str:
    """检测项目类型: python / node / rust / go / mixed。"""
    ws = Path(workspace)
    indicators = {
        "python": ["pyproject.toml", "setup.py", "setup.cfg", "requirements.txt"],
        "node": ["package.json"],
        "rust": ["Cargo.toml"],
        "go": ["go.mod"],
    }
    found = []
    for lang, files in indicators.items():
        for f in files:
            if (ws / f).exists():
                found.append(lang)
                break
    if len(found) == 0:
        return "unknown"
    if len(found) == 1:
        return found[0]
    return "mixed"


def infer_check_commands(workspace: str, cfg: VerifyConfig) -> Dict[str, str]:
    """根据项目类型推断检查命令。配置优先, 留空则自动推断。"""
    lang = detect_project_type(workspace)
    ws = Path(workspace)
    checks: Dict[str, str] = {}

    # --- test ---
    if cfg.test_cmd:
        checks["test"] = cfg.test_cmd
    else:
        if lang in ("python", "mixed"):
            # 检测 pytest 是否可用
            if (ws / "pytest.ini").exists() or (ws / "pyproject.toml").exists():
                checks["test"] = "python -m pytest -x -q --tb=short"
            elif (ws / "setup.py").exists():
                checks["test"] = "python -m pytest -x -q --tb=short"
        if lang in ("node", "mixed") and "test" not in checks:
            pkg = _read_package_json(ws)
            if pkg and "test" in pkg.get("scripts", {}):
                checks["test"] = "npm test --silent"

    # --- typecheck ---
    if cfg.typecheck_cmd:
        checks["typecheck"] = cfg.typecheck_cmd
    else:
        if lang in ("python", "mixed"):
            checks["typecheck"] = "python -m mypy --ignore-missing-imports --no-error-summary ."

    # --- lint ---
    if cfg.lint_cmd:
        checks["lint"] = cfg.lint_cmd
    else:
        if lang in ("python", "mixed"):
            checks["lint"] = "python -m ruff check --select=E,F,W --quiet ."
        if lang in ("node", "mixed") and "lint" not in checks:
            pkg = _read_package_json(ws)
            if pkg and "lint" in pkg.get("scripts", {}):
                checks["lint"] = "npm run lint --silent"

    return checks


def _read_package_json(ws: Path) -> Optional[Dict[str, Any]]:
    """安全读取 package.json。"""
    pkg_path = ws / "package.json"
    if not pkg_path.exists():
        return None
    try:
        return cast(Dict[str, Any], json.loads(pkg_path.read_text(encoding="utf-8")))
    except Exception:  # noqa: BLE001
        return None


def run_check(name: str, cmd: str, workspace: str, timeout: float = 120.0) -> CheckResult:
    """执行单个检查命令, 返回 CheckResult。"""
    import time
    t0 = time.monotonic()
    try:
        proc = subprocess.run(
            cmd, shell=True, cwd=workspace,
            capture_output=True, text=True,
            timeout=timeout,
        )
        elapsed = time.monotonic() - t0
        output = (proc.stdout or "") + "\n" + (proc.stderr or "")
        return CheckResult(
            name=name, cmd=cmd, passed=(proc.returncode == 0),
            output=output.strip(), returncode=proc.returncode, elapsed=elapsed,
        )
    except subprocess.TimeoutExpired:
        elapsed = time.monotonic() - t0
        return CheckResult(
            name=name, cmd=cmd, passed=False,
            output=f"[超时] 命令在 {timeout:.0f}s 内未完成",
            returncode=-1, elapsed=elapsed,
        )
    except Exception as exc:  # noqa: BLE001
        elapsed = time.monotonic() - t0
        return CheckResult(
            name=name, cmd=cmd, passed=False,
            output=f"[错误] {type(exc).__name__}: {exc}",
            returncode=-1, elapsed=elapsed,
        )


def verify_once(workspace: str, cfg: VerifyConfig) -> VerifyRound:
    """执行一轮验证 (test + typecheck + lint), 返回 VerifyRound。"""
    commands = infer_check_commands(workspace, cfg)
    rnd = VerifyRound(round_num=1)
    for name, cmd in commands.items():
        result = run_check(name, cmd, workspace)
        rnd.checks.append(result)
        log.info("verify %s: %s (%.2fs)", name, "PASS" if result.passed else "FAIL", result.elapsed)
    return rnd


def verify_with_heal(
    workspace: str,
    cfg: VerifyConfig,
    heal_fn: Optional[Callable[[str], str]] = None,
    max_rounds: Optional[int] = None,
) -> VerifyReport:
    """带自修复的验证闭环。

    Args:
        workspace: 项目根目录
        cfg: 验证配置
        heal_fn: 自修复函数, 接收错误信息文本, 返回修复指令 (返回空串表示放弃)。
                 通常由 Agent 的一次 run() 调用来实现。
        max_rounds: 最大轮数 (覆盖配置)

    Returns:
        VerifyReport: 完整验证报告
    """
    max_r = max_rounds if max_rounds is not None else cfg.max_heal_rounds
    report = VerifyReport()

    for i in range(1, max_r + 1):
        rnd = verify_once(workspace, cfg)
        rnd.round_num = i
        report.rounds.append(rnd)

        if rnd.all_passed:
            if i == 1:
                pass  # 首轮即通过
            else:
                report.healed = True
            return report

        # 有失败 → 尝试自修复
        if heal_fn is None:
            report.gave_up = True
            return report

        failure_text = "\n\n".join(
            f"=== {c.name} 失败 (exit={c.returncode}) ===\n{c.output}"
            for c in rnd.failures
        )
        try:
            instruction = heal_fn(failure_text)
        except Exception as exc:  # noqa: BLE001
            log.debug("heal_fn 异常: %s", exc)
            report.gave_up = True
            return report

        if not instruction or not instruction.strip():
            report.gave_up = True
            return report

    report.gave_up = True
    return report
