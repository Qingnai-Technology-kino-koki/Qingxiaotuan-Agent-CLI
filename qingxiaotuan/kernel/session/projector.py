"""Projector —— 把上下文投影成可发送给模型的消息）。

默认原样返回；policy.structure == 'strict' 时做 tool_use / tool_result 邻接修正
（本实现简化为：移除没有对应 assistant tool_calls 的孤立 tool 消息）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, List, Optional

from ..contract import Role
from .contracts import ContextMessage
from .memory import build_messages


@dataclass
class ProjectionPolicy:
    structure: str = "none"  # 'none' | 'strict'
    media: Any = None  # 'degraded' | dict


class Projector:
    """上下文投影器。"""

    def project(
        self,
        messages: List[ContextMessage],
        policy: Optional[ProjectionPolicy] = None,
    ) -> List[ContextMessage]:
        if policy is None or getattr(policy, "structure", "none") != "strict":
            return list(messages)
        return self._project_strict(list(messages))

    @staticmethod
    def _project_strict(messages: List[ContextMessage]) -> List[ContextMessage]:
        out: List[ContextMessage] = []
        for message in messages:
            if message.role == Role.TOOL:
                # 仅当已存在带 tool_calls 的 assistant 时才保留 tool 消息
                has_assistant_with_calls = any(
                    m.role == Role.ASSISTANT and m.tool_calls for m in out
                )
                if not has_assistant_with_calls:
                    continue
            out.append(message)
        return out

    @staticmethod
    def build_messages(system_prompt: str, history: List[ContextMessage]) -> List[ContextMessage]:
        return build_messages(system_prompt, history)
