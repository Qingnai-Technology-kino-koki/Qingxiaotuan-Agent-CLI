"""codedev.decompose —— 规格驱动的确定性任务分解。

设计目标（非 loop）：
    把「一个大任务」拆成「几个聚焦、可并行、各自带上下文的子任务」, 是 Claude Code 用弱模型
    也能拿下大需求的核心手法。这里**不调用模型做拆分**——用检索结果 + 简单启发式（按文件聚类 +
    固定补齐 tests / 验证 两类子任务）完成分解, 因此成本可忽略, 且可复现。

产物：
    - DevSpec: 子任务列表 + 一份 markdown 计划, 给宿主 Agent 看。
    - to_agent_specs(): 转成 arch.orchestration.AgentSpec 列表, 交给 Orchestrator 并发执行。
      每个子代理只拿到「自己那部分」的 context pack, 而非整库——上下文越窄, 弱模型越不容易跑偏。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List

from .retrieval import RetrievalResult

try:
    from ..arch.orchestration import AgentSpec
except Exception:  # noqa: BLE001
    AgentSpec = None  # type: ignore


@dataclass
class Subtask:
    id: str
    goal: str
    focus_files: List[str]
    context_pack: str


@dataclass
class DevSpec:
    task: str
    subtasks: List[Subtask] = field(default_factory=list)
    plan_md: str = ""

    def to_agent_specs(self, tool_set: str = "standard", max_depth: int = 0) -> List["AgentSpec"]:
        if AgentSpec is None:
            return []
        specs: List[AgentSpec] = []
        for st in self.subtasks:
            specs.append(AgentSpec(
                id=st.id,
                goal=st.goal + "\n\n上下文:\n" + st.context_pack,
                depth=max_depth,
                tools=[],  # 空 = 使用宿主 Agent 的默认工具集
            ))
        return specs


class Decomposer:
    """基于检索结果做确定性分解。"""

    def __init__(self, max_subtasks: int = 5, add_tests: bool = True,
                 add_verify: bool = True) -> None:
        self.max_subtasks = max_subtasks
        self.add_tests = add_tests
        self.add_verify = add_verify

    def decompose(self, task: str, retrieval: RetrievalResult) -> DevSpec:
        subtasks: List[Subtask] = []

        # 1) 按文件聚类检索命中 → 每个文件一个实现子任务
        by_file: dict[str, list] = {}
        for h in retrieval.hits:
            by_file.setdefault(h.symbol.file, []).append(h)

        impl_slots = max(1, self.max_subtasks - (2 if (self.add_tests and self.add_verify) else 0))
        file_order = sorted(by_file.keys(), key=lambda f: -len(by_file[f]))[:impl_slots]

        for i, f in enumerate(file_order, 1):
            hits = by_file[f]
            pack = self._pack_for_files(retrieval, [f])
            goal = (
                f"在 {f} 中推进任务「{task}」。\n"
                f"已检索到该文件 {len(hits)} 个相关符号, 优先改动它们。\n"
                f"保持与现有代码风格/接口一致, 改动完成后不要做无关重构。"
            )
            subtasks.append(Subtask(id=f"impl_{i}", goal=goal, focus_files=[f], context_pack=pack))

        # 2) 测试子任务（固定补齐）
        if self.add_tests:
            test_files = self._guess_test_files(retrieval)
            pack = self._pack_for_files(retrieval, test_files) if test_files else retrieval.pack()
            subtasks.append(Subtask(
                id="tests",
                goal=(f"为本次改动补充/更新单元测试, 覆盖核心分支与边界。"
                      f"测试文件候选: {', '.join(test_files) or 'tests/ 下新建'}"),
                focus_files=test_files, context_pack=pack,
            ))

        # 3) 验证/收口子任务（固定补齐）
        if self.add_verify:
            subtasks.append(Subtask(
                id="verify",
                goal=("运行验证闸门（build/test/lint）。\n"
                      "读取报告中的 error, 逐个修复直到 error=0; 修复时只动报错处, 不顺手重构。"),
                focus_files=[], context_pack=retrieval.pack(),
            ))

        return DevSpec(task=task, subtasks=subtasks, plan_md=self._render_plan(task, subtasks))

    # ------------------------------------------------------------------ 辅助
    def _pack_for_files(self, retrieval: RetrievalResult, files: List[str]) -> str:
        hits = [h for h in retrieval.hits if h.symbol.file in set(files)]
        if not hits:
            return retrieval.pack()
        lines = ["# 聚焦上下文"] + [h.symbol.file + f":{h.symbol.start}" for h in hits]
        # 复用 RetrievalResult 渲染, 但只保留指定文件
        scoped = RetrievalResult(
            query=retrieval.query, hits=hits,
            files_scanned=retrieval.files_scanned,
            symbols_indexed=retrieval.symbols_indexed,
            elapsed_ms=retrieval.elapsed_ms,
        )
        return scoped.pack()

    def _guess_test_files(self, retrieval: RetrievalResult) -> List[str]:
        files = {h.symbol.file for h in retrieval.hits}
        tests = [f for f in files if ("test" in f.lower() or f.startswith("tests/"))]
        return tests[:3]

    def _render_plan(self, task: str, subtasks: List[Subtask]) -> str:
        lines = [f"# 开发计划：{task}", "", "分解为可并行子任务（每个子代理带独立上下文）:", ""]
        for i, st in enumerate(subtasks, 1):
            focus = ", ".join(st.focus_files) or "（全仓库验证）"
            lines.append(f"{i}. **{st.id}** — {focus}")
            lines.append(f"   - 目标: {st.goal.splitlines()[0]}")
        lines.append("")
        lines.append("执行策略：实现类子任务并行；verify 子任务最后跑, 修复其报告中的 error。")
        return "\n".join(lines)
