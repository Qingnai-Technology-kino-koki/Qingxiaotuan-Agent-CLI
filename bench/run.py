"""青小团 vs Claude Code 软件工程能力评测基准 (harness)。

设计原则 (不依赖外网即可跑通框架):
- 任务用声明式 YAML 描述: 每个任务包含「工作区初始化文件 / 自然语言指令 / 验收脚本」。
- 验收脚本退出码 0 = 通过, 非 0 = 失败 (与 CI 一致, 可跑 pytest/python -c/shell)。
- 运行器在临时工作区里启动 `qxt run "<instruction>"` (真实子进程, 与线上一致),
  再执行验收脚本, 记录 通过/耗时/估算成本。
- 通过 `--model` / 环境变量接入真实模型端点后即可量化对比 Claude Code;
  无端点时 `--dry` 只校验框架与任务定义本身。

用法:
  python bench/run.py                      # 跑全部任务 (需已配置模型端点)
  python bench/run.py --task refactor_util # 只跑单个
  python bench/run.py --dry                # 校验框架/任务, 不真正调用模型
  python bench/run.py --qxt .venv/Scripts/qxt.exe
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None


ROOT = Path(__file__).resolve().parent
TASKS_FILE = ROOT / "tasks.yaml"


@dataclass
class Task:
    id: str
    instruction: str
    files: Dict[str, str] = field(default_factory=dict)   # 相对路径 -> 内容
    check: str = "python -c \"import sys; sys.exit(0)\""   # 验收 shell 命令
    timeout: int = 600


def load_tasks(path: Path) -> List[Task]:
    if yaml is None:
        raise SystemExit("需要 PyYAML: pip install pyyaml")
    if not path.exists():
        raise SystemExit(f"任务文件不存在: {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    tasks = []
    for raw in data.get("tasks", []):
        tasks.append(Task(
            id=raw["id"],
            instruction=raw["instruction"],
            files=raw.get("files", {}) or {},
            check=raw.get("check", 'python -c "import sys; sys.exit(0)"'),
            timeout=int(raw.get("timeout", 600)),
        ))
    return tasks


@dataclass
class Result:
    task_id: str
    passed: bool
    elapsed: float
    error: str = ""


def run_task(task: Task, qxt: str, model: Optional[str], dry: bool) -> Result:
    """在临时工作区里跑一个任务, 返回结果。"""
    work = Path(tempfile.mkdtemp(prefix=f"qxt-bench-{task.id}-"))
    try:
        for rel, content in task.files.items():
            p = work / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")

        if dry:
            # 仅校验框架: 执行验收脚本, 只要命令能正常跑起来 (不崩溃) 即视为框架 OK,
            # 不关心退出码 (因为没真正让 agent 改代码, 验收本就该失败)。
            try:
                proc = subprocess.run(task.check, cwd=work, shell=True,
                                      capture_output=True, text=True, timeout=120)
                note = f"check 退出码={proc.returncode} (dry 模式不要求通过)"
                return Result(task.id, True, 0.0, note)
            except Exception as exc:  # noqa: BLE001
                return Result(task.id, False, 0.0, f"框架异常: {exc}")

        cmd = [qxt, "run", task.instruction]
        if model:
            cmd += ["--model", model]
        t0 = time.time()
        proc = subprocess.run(
            cmd, cwd=work, capture_output=True, text=True, timeout=task.timeout,
            env={**os.environ, "QXT_HOME": str(work / ".qxt-home")},
        )
        elapsed = time.time() - t0
        if proc.returncode != 0:
            return Result(task.id, False, elapsed,
                          f"qxt 退出码 {proc.returncode}\n{proc.stderr[-2000:]}")
        ok, err = _run_check(task, work)
        return Result(task.id, ok, elapsed, err if not ok else "")
    except subprocess.TimeoutExpired:
        return Result(task.id, False, task.timeout, "超时")
    except Exception as exc:  # noqa: BLE001
        return Result(task.id, False, 0.0, str(exc))
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _run_check(task: Task, work: Path) -> tuple[bool, str]:
    proc = subprocess.run(
        task.check, cwd=work, shell=True, capture_output=True, text=True, timeout=120,
    )
    return proc.returncode == 0, (proc.stderr or proc.stdout)[-1500:]


def main() -> int:
    ap = argparse.ArgumentParser(description="青小团 软件工程能力评测基准")
    ap.add_argument("--tasks", type=Path, default=TASKS_FILE)
    ap.add_argument("--task", help="只跑指定 id 的任务")
    ap.add_argument("--qxt", default="qxt", help="qxt 可执行文件路径")
    ap.add_argument("--model", default=None, help="覆盖使用的模型 (provider/model)")
    ap.add_argument("--dry", action="store_true", help="只校验框架/任务, 不调模型")
    ap.add_argument("--json", action="store_true", help="输出 JSON 而非表格")
    args = ap.parse_args()

    tasks = load_tasks(args.tasks)
    if args.task:
        tasks = [t for t in tasks if t.id == args.task]
    if not tasks:
        raise SystemExit("没有可跑的任务")

    results: List[Result] = [run_task(t, args.qxt, args.model, args.dry) for t in tasks]

    if args.json:
        import json
        print(json.dumps([vars(r) for r in results], ensure_ascii=False, indent=2))
    else:
        passed = sum(1 for r in results if r.passed)
        print(f"{'任务':<22}{'结果':<8}{'耗时(s)':<10}说明")
        print("-" * 70)
        for r in results:
            status = "PASS" if r.passed else "FAIL"
            print(f"{r.task_id:<22}{status:<8}{r.elapsed:<10.1f}{r.error[:40]}")
        print("-" * 70)
        print(f"通过 {passed}/{len(results)}" + ("  (dry 模式: 仅校验框架)" if args.dry else ""))

    return 0 if all(r.passed for r in results) or args.dry else 1


if __name__ == "__main__":
    raise SystemExit(main())
