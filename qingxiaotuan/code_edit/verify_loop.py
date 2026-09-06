"""预测—反馈闭环：改完代码必须验证。

提供可注入的 run_tests（默认用 subprocess 跑 shell 命令并解析 pytest 风格输出），
以及 VerifyLoop：失败时调用诊断回调（由 Agent 据此修代码），最多重试 N 次。
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from typing import Callable, Optional

_PASS_RE = re.compile(r"(\d+)\s+passed")
_FAIL_RE = re.compile(r"(\d+)\s+failed")
_ERR_RE = re.compile(r"(\d+)\s+error")


@dataclass
class TestResult:
    returncode: int
    passed: int
    failed: int
    errored: int
    output: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and self.failed == 0 and self.errored == 0

    def summary(self) -> str:
        return (f"通过 {self.passed} / 失败 {self.failed} / 错误 {self.errored} "
                f"(rc={self.returncode})")


def run_tests(cmd: str, cwd: Optional[str] = None, timeout: int = 600) -> TestResult:
    """运行测试命令并解析结果。默认解析 pytest 的 'X passed / Y failed' 摘要。"""
    try:
        proc = subprocess.run(
            cmd, shell=True, cwd=cwd, capture_output=True, text=True, timeout=timeout
        )
    except subprocess.TimeoutExpired as e:
        out = (e.stdout or "") + (e.stderr or "")
        return TestResult(returncode=124, passed=0, failed=0, errored=0,
                         output=(out or "")[:4000])
    out = (proc.stdout or "") + "\n" + (proc.stderr or "")
    passed = _first_int(_PASS_RE, out)
    failed = _first_int(_FAIL_RE, out)
    errored = _first_int(_ERR_RE, out)
    # 无 pytest 摘要时，用返回码兜底
    if passed == 0 and failed == 0 and errored == 0:
        if proc.returncode == 0:
            passed = 1
        else:
            failed = 1
    return TestResult(returncode=proc.returncode, passed=passed, failed=failed,
                     errored=errored, output=out[:4000])


def _first_int(rx, text: str) -> int:
    m = rx.search(text)
    return int(m.group(1)) if m else 0


class VerifyLoop:
    """预测—反馈闭环引擎。

    run_fn: 执行测试的函数 (cmd, cwd) -> TestResult，默认 run_tests。
    diagnose_fn: 测试失败后由 Agent 据此定位并修复代码的回调 (TestResult) -> None。
    """

    def __init__(
        self,
        run_fn: Optional[Callable[[str, Optional[str]], TestResult]] = None,
        diagnose_fn: Optional[Callable[[TestResult], None]] = None,
        max_retries: int = 3,
    ):
        self._run_fn = run_fn or run_tests
        self._diagnose_fn = diagnose_fn
        self.max_retries = max_retries

    def run(self, test_cmd: str, cwd: Optional[str] = None) -> TestResult:
        last: Optional[TestResult] = None
        for attempt in range(self.max_retries + 1):
            res = self._run_fn(test_cmd, cwd)
            last = res
            if res.ok:
                return res
            # 失败：进入反馈分支
            if attempt < self.max_retries and self._diagnose_fn is not None:
                self._diagnose_fn(res)
            else:
                break
        return last  # type: ignore[return-value]
