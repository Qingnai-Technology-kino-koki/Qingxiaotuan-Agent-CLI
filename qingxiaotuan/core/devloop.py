"""自主开发循环 (DevLoop) —— 借鉴 Claude Code 的「持续自主迭代」。

增强: 集成 Reflector 反思引擎, 形成 Plan→Execute→Reflect→Re-plan 双层闭环。

一轮完整循环覆盖: 分析代码库 → 规划最小改动 → 实施 → 反思验证 → 汇报。
循环一直跑, 直到:
  (a) 模型在某轮明确判定「功能与质量达标」(输出 【已完成】 标记), 且
  (b) 用户确认满意 (stop_on_user_ok)。
中间遇到歧义, 在检查点主动问用户。每轮都会汇报进度, 不让用户黑盒等待。

Reflector 增强:
  - 每轮实施后自动运行验证 (编译/测试/静态检查)
  - 验证失败时自动诊断、建议修正方案
  - 连续失败时自动降级: 缩小子任务粒度, 或请求用户确认
"""

from __future__ import annotations

import re
from typing import Any, Callable, Dict, List, Optional

from .markers import DONE_MARKERS, is_done
from .reflector import Reflector, ReflectDecision, ReflectResult

IterationHook = Callable[[int, int, str], None]            # (n, total, task)
CheckpointHook = Callable[[str], str]                       # (report) -> decision
VerifyHook = Callable[[str, bool], None]                   # (tool_name, passed)


class DevLoop:
    def __init__(
        self,
        agent,
        config,
        on_iteration: Optional[IterationHook] = None,
        on_checkpoint: Optional[CheckpointHook] = None,
        on_verify: Optional[VerifyHook] = None,
    ) -> None:
        self.agent = agent
        self.config = config
        self.on_iteration = on_iteration
        self.on_checkpoint = on_checkpoint
        self.on_verify = on_verify

        # 初始化 Reflector 反思引擎
        self.reflector = Reflector(
            workspace=getattr(agent, "workspace", "."),
            max_auto_fix=config.get("reflector.max_auto_fix", 2),
            timeout=config.get("reflector.verify_timeout", 120),
        )

    # ------------------------------------------------------------ 主入口

    def run(
        self,
        task: str,
        feedback: str = "",
        stream: bool = True,
        on_token: Optional[Callable[[str], None]] = None,
        on_tool: Optional[Callable[[str, str], None]] = None,
        on_reason: Optional[Callable[[str], None]] = None,
    ) -> str:
        max_iter = self.config.get("loop.max_iterations", 12)
        ask_every = self.config.get("loop.ask_every", 1)
        auto_test = self.config.get("loop.auto_test", True)
        stop_on_ok = self.config.get("loop.stop_on_user_ok", True)
        reflect_enabled = self.config.get("reflector.enabled", True)
        reflect_every = max(1, int(self.config.get("loop.reflect_every", 2)))

        # YOLO 模式: 默认不主动打断用户
        yolo = self.config.is_yolo() if hasattr(self.config, "is_yolo") else False
        if yolo and ask_every == 1:
            ask_every = 0

        if self.on_iteration:
            self.on_iteration(1, max_iter, task)

        accumulated_feedback = feedback
        last_report = ""
        seen_snippets: set = set()

        for n in range(1, max_iter + 1):
            prompt = self._iteration_prompt(task, n, max_iter, auto_test, accumulated_feedback)
            answer = self.agent.run(
                prompt, stream=stream,
                on_token=on_token, on_tool=on_tool, on_reason=on_reason,
            )
            last_report = answer or ""
            self.agent.kernel.emit("loop.iteration", {"n": n, "report": last_report[:500]})

            # ---- Reflector 反思 (新增) ----
            if reflect_enabled and (n % reflect_every == 0 or n == max_iter - 1):
                reflect_result = self._reflect_and_decide(
                    task, last_report, auto_test, n, max_iter
                )
                if reflect_result is not None:
                    decision = reflect_result.decision

                    if decision == ReflectDecision.DONE:
                        return self._finalize(last_report, n, stopped_by_user=False)

                    if decision == ReflectDecision.DEGRADE:
                        # 降级: 用缩小后的子任务继续
                        accumulated_feedback = self._append_feedback(
                            accumulated_feedback,
                            f"⚠️ 连续验证失败, 请降级处理: {reflect_result.degraded_task}\n"
                            f"诊断: {reflect_result.diagnosis}"
                        )
                        continue

                    if decision == ReflectDecision.ASK_USER:
                        # 请求用户帮助
                        if self.on_checkpoint:
                            user_input = self.on_checkpoint(
                                f"验证连续失败 {reflect_result.failure_count} 次, 需要帮助:\n"
                                f"{reflect_result.diagnosis}\n\n"
                                f"请提供指导或指示:"
                            )
                            accumulated_feedback = self._append_feedback(
                                accumulated_feedback, user_input
                            )
                            continue
                        else:
                            # headless 模式: 记录并继续
                            accumulated_feedback = self._append_feedback(
                                accumulated_feedback,
                                f"验证失败诊断: {reflect_result.diagnosis}"
                            )

                    if decision == ReflectDecision.FIX:
                        # 自动修正: 注入诊断信息作为反馈
                        accumulated_feedback = self._append_feedback(
                            accumulated_feedback,
                            f"验证失败, 请修正:\n{reflect_result.diagnosis}\n"
                            f"建议: {reflect_result.suggested_fix}"
                        )
                        continue

            done = self._detect_done(last_report)

            # 收敛去重: 若本轮汇报与之前高度雷同 (模型卡住), 注入提示强制换思路
            snippet = last_report[:400]
            if snippet in seen_snippets:
                accumulated_feedback = self._append_feedback(
                    accumulated_feedback,
                    "你的汇报与之前轮次高度重复, 可能陷入循环。请换具体切入点, "
                    "或说明当前路径受阻的卡点。",
                )
            seen_snippets.add(snippet)

            # 检查点: 主动问用户
            need_ask = ask_every > 0 and (n % ask_every == 0 or n == max_iter)
            if self.on_checkpoint and (need_ask or done):
                decision = self.on_checkpoint(last_report).strip()
                lower = decision.lower()
                if lower in ("done", "d", "满意", "结束", "stop", "完成", "ok", "好的", "可以"):
                    if self.on_iteration:
                        self.on_iteration(-1, max_iter, "用户确认完成, 停止循环")
                    return self._finalize(last_report, n, stopped_by_user=True)
                if lower in ("c", "continue", "继续", "next", "下一轮"):
                    accumulated_feedback = ""
                    continue
                accumulated_feedback = self._append_feedback(accumulated_feedback, decision)
                continue

            if done and not stop_on_ok:
                return self._finalize(last_report, n, stopped_by_user=False)

            if done and not self.on_checkpoint:
                return self._finalize(last_report, n, stopped_by_user=False)

        return self._finalize(last_report, max_iter, stopped_by_user=False, exhausted=True)

    # ------------------------------------------------------------ Reflector 集成

    def _reflect_and_decide(
        self, task: str, last_output: str, auto_test: bool, n: int, max_iter: int
    ) -> Optional[ReflectResult]:
        """运行 Reflector 反思, 返回决策结果。

        仅在 auto_test 启用且不在最后一轮时运行反思 (最后一轮交给完成判定)。
        """
        if not auto_test or n >= max_iter:
            return None

        try:
            result = self.reflector.reflect(
                task=task,
                last_output=last_output,
                on_verify=self.on_verify,
            )
            self.agent.kernel.emit("loop.reflect", {
                "n": n,
                "decision": result.decision.value,
                "failure_count": result.failure_count,
                "diagnosis": result.diagnosis[:200] if result.diagnosis else "",
            })
            return result
        except Exception as exc:  # noqa: BLE001
            # 反思失败不应中断主循环
            self.agent.kernel.emit("loop.reflect_error", {"error": str(exc)})
            return None

    # ------------------------------------------------------------ 内部

    @staticmethod
    def _append_feedback(acc: str, new: str) -> str:
        """追加反馈并限制总长, 超长则丢弃最旧的, 防多轮越滚越大。"""
        MAX = 2000
        acc = (acc + "\n\n" + new).strip() if acc else new
        if len(acc) > MAX:
            acc = "...[早期反馈已折叠]\n" + acc[-MAX:]
        return acc

    @staticmethod
    def _iteration_prompt(task: str, n: int, total: int, auto_test: bool, feedback: str) -> str:
        test_line = (
            "4) 自测: run_tests 验证, 失败就读报错→定位→修复→再跑。"
            if auto_test else
            "4) 自测: 有测试尽量跑。"
        )
        fb = f"\n\n上一轮反馈:\n{feedback}" if feedback else ""
        return (
            f"[开发循环 {n}/{total}] 任务: {task}\n\n"
            "本轮顺序: 1)分析 2)规划最小改动 3)实施 4)自测。\n"
            "分析用 codebase_map/find_symbol/read_file/git_status; 实施用 write_file/edit_file, "
            "注意跨文件依赖 (find_references)。\n"
            f"{test_line}\n\n"
            "汇报固定结构 (中文简洁, 缺项视为未完成):\n"
            "## 改动\n- 文件: 路径 (新增/修改/删除) · 关键逻辑一句\n"
            "## 测试证据\n- 命令/exit码/通过数 (失败贴报错)\n"
            "## 风险\n- 限制/未覆盖/需你决策\n"
            "## 下一步\n- 未完成时的具体动作\n\n"
            "判定完成时在开头写【已完成】并给交付总结; 歧义写入『风险』等你确认。\n" + fb
        )

    @staticmethod
    def _detect_done(report: str) -> bool:
        return is_done(report)

    @staticmethod
    def _finalize(report: str, n: int, stopped_by_user: bool, exhausted: bool = False) -> str:
        tag = "用户确认完成" if stopped_by_user else ("达到迭代上限" if exhausted else "模型判定完成")
        return f"[循环结束 · {tag} · 共 {n} 轮]\n\n{report}"

    # ------------------------------------------------------------ 并行调研

    def parallel_research(self, task: str, aspects: List[str], stream: bool = True,
                          on_sub_tool: Optional[Callable[[str, str, str], None]] = None) -> str:
        """在开始实现前, 把任务的多个独立调研面并发派出隔离子 Agent 并行摸一遍。"""
        from .subagents import SubAgentPool, make_tasks

        pool = SubAgentPool(
            kernel=self.agent.kernel, config=self.config,
            workspace=self.agent.workspace, main_agent=self.agent,
            confirm=self.agent.ctx.confirm,
            default_timeout=float(self.config.get("agent.subagent_timeout", 180)),
            isolation=self.config.get("agent.subagent_isolation", "process"),
        )
        prompts = [f"围绕任务「{task}」, 调研以下方面并给出要点:\n{a}" for a in aspects]
        results = pool.dispatch(
            make_tasks(prompts, prefix="R"), stream=stream,
            on_sub_tool=on_sub_tool,
        )
        return SubAgentPool.aggregate(results, title=f"并行调研 · {task[:30]}")
