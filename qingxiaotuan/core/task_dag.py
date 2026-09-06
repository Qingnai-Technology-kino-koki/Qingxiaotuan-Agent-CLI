"""任务依赖图 + 智能调度器 (TaskDAG) —— 多 Agent 协作的大脑。

原始 Swarm 的 _build_waves 方法用简单的 Kahn 拓扑排序把任务分成波次。
TaskDAG 在此基础上增加:
1. **DAG 表示**: 任务间的依赖关系用有向无环图表示;
2. **关键路径分析**: 识别最长依赖链, 优化总执行时间;
3. **动态调度**: 根据 Agent 可用性和任务优先级动态分配;
4. **资源感知**: 考虑模型配额/并发度限制;
5. **失败重试**: 失败任务可自动重试或重新调度;
6. **进度追踪**: 实时追踪每个任务的执行状态;
7. **死锁检测**: 检测循环依赖并自动打破。
"""

from __future__ import annotations

import logging
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

log = logging.getLogger(__name__)


# ============================================================ 任务状态

class TaskStatus(str, Enum):
    PENDING = "pending"      # 等待执行
    READY = "ready"          # 依赖已满足, 可以执行
    RUNNING = "running"      # 正在执行
    COMPLETED = "completed"  # 已完成
    FAILED = "failed"        # 执行失败
    SKIPPED = "skipped"      # 被跳过 (依赖失败)
    CANCELLED = "cancelled"  # 被取消


# ============================================================ DAG 节点

@dataclass
class DAGNode:
    """DAG 中的一个任务节点。"""

    task_id: str
    title: str
    prompt: str
    role: str = "implementer"  # 执行角色
    priority: int = 0  # 优先级 (越高越先执行)
    dependencies: Set[str] = field(default_factory=set)  # 依赖的 task_id 集合
    status: TaskStatus = TaskStatus.PENDING
    result: Optional[str] = None
    error: Optional[str] = None
    retries: int = 0
    max_retries: int = 1
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    assigned_to: str = ""  # 分配给哪个 Agent
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def elapsed(self) -> float:
        if self.started_at is None:
            return 0.0
        end = self.completed_at or time.time()
        return end - self.started_at

    @property
    def can_retry(self) -> bool:
        return self.retries < self.max_retries

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "title": self.title,
            "status": self.status.value,
            "role": self.role,
            "dependencies": list(self.dependencies),
            "retries": self.retries,
            "elapsed": round(self.elapsed, 1),
            "assigned_to": self.assigned_to,
        }


# ============================================================ DAG

class TaskDAG:
    """任务依赖有向无环图。

    用法:
        dag = TaskDAG()
        dag.add_node("T1", "分析需求", "分析...", role="architect")
        dag.add_node("T2", "实现功能", "实现...", role="implementer")
        dag.add_dependency("T2", "T1")  # T2 依赖 T1

        # 获取可执行波次
        wave = dag.get_ready_tasks()  # [T1]
        # T1 完成后
        dag.mark_completed("T1", "分析结果...")
        wave = dag.get_ready_tasks()  # [T2]
    """

    def __init__(self) -> None:
        self._nodes: Dict[str, DAGNode] = {}
        self._lock = threading.Lock()

    def add_node(
        self,
        task_id: str,
        title: str,
        prompt: str,
        role: str = "implementer",
        priority: int = 0,
        max_retries: int = 1,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> DAGNode:
        """添加任务节点。"""
        with self._lock:
            node = DAGNode(
                task_id=task_id,
                title=title,
                prompt=prompt,
                role=role,
                priority=priority,
                max_retries=max_retries,
                metadata=metadata or {},
            )
            self._nodes[task_id] = node
            return node

    def add_dependency(self, task_id: str, depends_on: str) -> None:
        """声明 task_id 依赖 depends_on。"""
        with self._lock:
            if task_id in self._nodes:
                self._nodes[task_id].dependencies.add(depends_on)

    def add_dependencies(self, task_id: str, depends_on: List[str]) -> None:
        """声明 task_id 依赖多个任务。"""
        with self._lock:
            if task_id in self._nodes:
                self._nodes[task_id].dependencies.update(depends_on)

    def get_node(self, task_id: str) -> Optional[DAGNode]:
        """获取节点。"""
        with self._lock:
            return self._nodes.get(task_id)

    # ---------------------------------------------------------- 状态管理

    def mark_running(self, task_id: str, assigned_to: str = "") -> None:
        """标记任务开始执行。"""
        with self._lock:
            node = self._nodes.get(task_id)
            if node:
                node.status = TaskStatus.RUNNING
                node.started_at = time.time()
                node.assigned_to = assigned_to

    def mark_completed(self, task_id: str, result: str = "") -> None:
        """标记任务完成。"""
        with self._lock:
            node = self._nodes.get(task_id)
            if node:
                node.status = TaskStatus.COMPLETED
                node.result = result
                node.completed_at = time.time()

    def mark_failed(self, task_id: str, error: str = "") -> None:
        """标记任务失败。"""
        with self._lock:
            node = self._nodes.get(task_id)
            if node:
                node.retries += 1
                if node.can_retry:
                    # 可重试: 重置为 PENDING, 等待重新调度
                    node.status = TaskStatus.PENDING
                    node.error = error
                    log.info(
                        "任务 %s 失败, 将重试 (%d/%d)",
                        task_id, node.retries, node.max_retries,
                    )
                else:
                    node.status = TaskStatus.FAILED
                    node.error = error
                    # 标记所有依赖此任务的下游为 SKIPPED
                    self._skip_downstream(task_id)

    def _skip_downstream(self, failed_id: str) -> None:
        """跳过所有依赖失败任务的下游任务 (在锁内调用)。"""
        queue = deque([failed_id])
        visited = {failed_id}
        while queue:
            current = queue.popleft()
            for node in self._nodes.values():
                if current in node.dependencies and node.task_id not in visited:
                    if node.status in (TaskStatus.PENDING, TaskStatus.READY):
                        node.status = TaskStatus.SKIPPED
                        visited.add(node.task_id)
                        queue.append(node.task_id)

    def cancel(self, task_id: str) -> None:
        """取消任务。"""
        with self._lock:
            node = self._nodes.get(task_id)
            if node and node.status in (TaskStatus.PENDING, TaskStatus.READY):
                node.status = TaskStatus.CANCELLED

    # ---------------------------------------------------------- 查询

    def get_ready_tasks(self) -> List[DAGNode]:
        """获取所有依赖已满足、可以执行的任务。"""
        with self._lock:
            ready = []
            for node in self._nodes.values():
                if node.status != TaskStatus.PENDING:
                    continue
                # 检查所有依赖是否已完成
                deps_met = all(
                    self._nodes.get(d, DAGNode("", "", "")).status == TaskStatus.COMPLETED
                    for d in node.dependencies
                    if d in self._nodes
                )
                # 也检查是否有被跳过/失败的依赖 (导致自身也被跳过)
                deps_failed = any(
                    self._nodes.get(d, DAGNode("", "", "")).status in (
                        TaskStatus.FAILED, TaskStatus.SKIPPED, TaskStatus.CANCELLED
                    )
                    for d in node.dependencies
                    if d in self._nodes
                )
                if deps_failed:
                    node.status = TaskStatus.SKIPPED
                    continue
                if deps_met:
                    node.status = TaskStatus.READY
                    ready.append(node)
            # 按优先级排序
            ready.sort(key=lambda n: -n.priority)
            return ready

    def get_wave(self, max_concurrent: int = 5) -> List[DAGNode]:
        """获取下一波可执行任务 (受并发度限制)。"""
        ready = self.get_ready_tasks()
        return ready[:max_concurrent]

    def get_all_nodes(self) -> List[DAGNode]:
        """获取所有节点。"""
        with self._lock:
            return list(self._nodes.values())

    def get_status_summary(self) -> Dict[str, Any]:
        """获取状态摘要。"""
        with self._lock:
            counts = defaultdict(int)
            for node in self._nodes.values():
                counts[node.status.value] += 1
            return {
                "total": len(self._nodes),
                "by_status": dict(counts),
                "total_elapsed": sum(n.elapsed for n in self._nodes.values()),
            }

    # ---------------------------------------------------------- 关键路径分析

    def critical_path(self) -> List[str]:
        """计算关键路径 (最长依赖链)。

        关键路径 = 从源节点到汇节点的最长路径,
        其长度决定了整个 DAG 的最短完成时间。
        """
        with self._lock:
            # 拓扑排序
            in_degree = defaultdict(int)
            children: Dict[str, List[str]] = defaultdict(list)
            for node in self._nodes.values():
                for dep in node.dependencies:
                    if dep in self._nodes:
                        children[dep].append(node.task_id)
                        in_degree[node.task_id] += 1

            # BFS 拓扑排序 + 最长路径
            dist: Dict[str, int] = {nid: 0 for nid in self._nodes}
            prev: Dict[str, Optional[str]] = {nid: None for nid in self._nodes}
            queue = deque(
                nid for nid, deg in in_degree.items() if deg == 0
            )
            topo_order = []

            while queue:
                nid = queue.popleft()
                topo_order.append(nid)
                for child in children[nid]:
                    in_degree[child] -= 1
                    if dist[nid] + 1 > dist[child]:
                        dist[child] = dist[nid] + 1
                        prev[child] = nid
                    if in_degree[child] == 0:
                        queue.append(child)

            # 找最长路径的终点
            if not dist:
                return []
            end_node = max(dist, key=lambda k: dist[k])
            path = []
            current: Optional[str] = end_node
            while current is not None:
                path.append(current)
                current = prev.get(current)
            path.reverse()
            return path

    # ---------------------------------------------------------- 死锁检测

    def detect_cycles(self) -> List[List[str]]:
        """检测循环依赖 (DAG 不应有环, 但用户输入可能引入)。"""
        with self._lock:
            WHITE, GRAY, BLACK = 0, 1, 2
            color = {nid: WHITE for nid in self._nodes}
            parent: Dict[str, Optional[str]] = {nid: None for nid in self._nodes}
            cycles: List[List[str]] = []

            def dfs(nid: str) -> bool:
                color[nid] = GRAY
                for node in self._nodes.values():
                    if nid in node.dependencies and node.task_id in self._nodes:
                        child = node.task_id
                        if color.get(child) == GRAY:
                            # 找到环
                            cycle = [child, nid]
                            cur = parent.get(nid)
                            while cur and cur != child:
                                cycle.append(cur)
                                cur = parent.get(cur)
                            cycle.reverse()
                            cycles.append(cycle)
                            return True
                        if color.get(child) == WHITE:
                            parent[child] = nid
                            if dfs(child):
                                return True
                color[nid] = BLACK
                return False

            for nid in self._nodes:
                if color[nid] == WHITE:
                    dfs(nid)

            return cycles

    def break_cycles(self) -> int:
        """自动打破循环依赖 (移除导致环的边)。"""
        cycles = self.detect_cycles()
        removed = 0
        for cycle in cycles:
            if len(cycle) >= 2:
                # 移除最后一个节点对第一个节点的依赖
                last = cycle[-1]
                first = cycle[0]
                node = self._nodes.get(last)
                if node and first in node.dependencies:
                    node.dependencies.discard(first)
                    removed += 1
                    log.warning(
                        "打破循环依赖: %s -> %s (移除依赖边)", last, first
                    )
        return removed

    # ---------------------------------------------------------- 构建辅助

    @classmethod
    def from_plan(
        cls,
        tasks: List[Dict[str, str]],
        default_role: str = "implementer",
    ) -> TaskDAG:
        """从规划结果构建 DAG。

        tasks: [{"id": "T1", "title": "...", "prompt": "...", "depends_on": "T2,T3", "role": "..."}]
        """
        dag = cls()
        for t in tasks:
            dag.add_node(
                task_id=t.get("id", ""),
                title=t.get("title", ""),
                prompt=t.get("prompt", ""),
                role=t.get("role", default_role),
            )
        for t in tasks:
            tid = t.get("id", "")
            deps = t.get("depends_on", "")
            if deps:
                for d in deps.replace(";", ",").split(","):
                    d = d.strip()
                    if d:
                        dag.add_dependency(tid, d)
        # 检测并打破循环
        cycles = dag.detect_cycles()
        if cycles:
            log.warning("规划中检测到 %d 个循环依赖, 自动打破", len(cycles))
            dag.break_cycles()
        return dag
