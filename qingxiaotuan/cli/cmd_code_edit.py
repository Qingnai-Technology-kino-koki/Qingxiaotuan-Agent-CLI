"""qxt code-edit —— 代码编辑助手 CLI。

子命令（均为可直接使用、不改动原生行为）：
  parse          把任务文本/文件解析为四要素简报，缺失项交互补全
  verify         运行测试命令并走预测—反馈闭环（失败诊断重试）
  rules          加载并展示项目级规则（.qingxiaotuan/rules.md / AGENTS.md）
  check-version  校验版本变更是否符合迭代策略（版本锁死）
  gate           对一个动作描述做风险分级与五层闸门判定
  edit           解析+补全+加载规则+（可选）验证，输出标准化任务简报交 Agent 执行
"""

from __future__ import annotations

import sys
from pathlib import Path

from ..code_edit import (Action, CodeEditAssistant, RiskLevel, TaskSpec,
                         check_version_lock, classify_risk, load_project_rules,
                         parse_task_spec, run_tests)


def _read_task(task: str) -> str:
    p = Path(task)
    if p.is_file():
        return p.read_text(encoding="utf-8", errors="ignore")
    return task


def _ask_interactive(missing: list) -> dict:
    print("任务缺失以下要素，请逐项补充（回车跳过）:")
    filled = {}
    label_key = {
        "Goal（目标）": "goal", "Context（上下文）": "context",
        "Constraints（约束）": "constraints", "Done when（完成标准）": "done_when",
    }
    for m in missing:
        val = input(f"  {m}: ").strip()
        if val:
            filled[label_key.get(m, m)] = val
    return filled


def cmd_code_edit(args) -> int:
    sub = getattr(args, "code_edit_cmd", None)
    ws = getattr(args, "workspace", None) or "."

    if sub == "parse":
        spec = parse_task_spec(_read_task(args.task))
        if not spec.is_complete() and not getattr(args, "no_ask", False):
            spec = CodeEditAssistant(ask_fn=_ask_interactive, workspace=ws).plan(_read_task(args.task))
        print(spec.render())
        return 0

    if sub == "verify":
        print(f"[验证] 运行: {args.command}")
        res = run_tests(args.command, cwd=ws, timeout=args.timeout)
        print(res.summary())
        if not res.ok:
            print("---- 失败输出前 40 行 ----")
            print("\n".join(res.output.splitlines()[:40]))
            return 1
        return 0

    if sub == "rules":
        text = load_project_rules(ws)
        if text.strip():
            print(text)
        else:
            print("(未找到项目级规则文件: .qingxiaotuan/rules.md / AGENTS.md)")
        return 0

    if sub == "check-version":
        ok, reason = check_version_lock(args.old, args.new, args.strategy)
        print(("✅ " if ok else "⛔ ") + reason)
        return 0 if ok else 1

    if sub == "gate":
        action = Action(
            kind=args.kind,
            description=args.description or args.kind,
            targets=args.target or [],
            command=args.command,
            uncommitted=getattr(args, "uncommitted", False),
        )
        lvl = classify_risk(action)
        need = lvl.value >= RiskLevel.HIGH.value
        print(f"动作: {action.kind} | 风险等级: {lvl.name} ({lvl.value}) | 需确认: {need}")
        return 0

    if sub == "edit":
        assistant = CodeEditAssistant(
            ask_fn=_ask_interactive, workspace=ws,
            version_strategy=args.version_strategy,
            max_verify_retries=args.max_retries,
        )
        report = assistant.run(
            _read_task(args.task),
            test_cmd=args.test,
            action=Action(kind="edit_code", description=args.task[:80]) if args.gate else None,
        )
        print(report.render())
        if report.denied:
            return 1
        if report.verify is not None and not report.verify.ok:
            return 1
        return 0

    print("未知子命令，使用 `qxt code-edit -h` 查看用法。", file=sys.stderr)
    return 2
