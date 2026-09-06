"""桥接层 —— 把现有 qingxiaotuan.memory 的 SessionStore 适配为 ContextMemory + WireStore。

设计：
- 现有 SessionStore 以 JSONL 事件流持久化会话（user/assistant/tool ...）。
- KernelSessionBridge 从 SessionStore 文件中载入已有消息，灌入 ContextMemory，
  并把这些消息以 wire.jsonl 形式在旁落地，从而可用本模块的 replay() 离线重建。
- create_session_memory(session_id, home_dir) 便捷工厂：返回一对全新的
  (ContextMemory, WireStore)，WireStore 持久化到 <home>/sessions/<session_id>.wire.jsonl。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, List, Optional

from ...memory.sessions import SessionStore
from .contracts import ContextMessage
from .memory import ContextMemory, build_messages
from .wire import WireStore, replay


class KernelSessionBridge:
    """把现有 SessionStore 适配为 kernel session 子系统。"""

    def __init__(self, session_store: SessionStore) -> None:
        self.session_store = session_store
        base = Path(session_store.file)
        # 与 session 文件同目录、同名的 wire.jsonl（仅承载上下文记录）
        self.wire_path = base.parent / (base.stem + ".wire.jsonl")
        self.wire_store = WireStore(self.wire_path)
        self.context_memory = ContextMemory()
        self.sync_from_session()

    # ---------------------------------------------------------- 同步
    def sync_from_session(self) -> ContextMemory:
        """读取现有 SessionStore 的事件流，把 user/assistant/tool 消息灌入 ContextMemory。

        同时把这些消息作为 context.append_message 记录写入（重置后的）wire.jsonl，
        使 replay() 能重建出相同上下文。
        """
        raw_messages = SessionStore.load_messages(self.session_store.file)
        messages: List[ContextMessage] = []
        for raw in raw_messages:
            if not isinstance(raw, dict):
                continue
            try:
                messages.append(ContextMessage.from_dict(raw))
            except Exception:
                continue

        # 重建上下文
        self.context_memory = ContextMemory(messages)

        # 落地到 wire.jsonl（重置后写入，保持上下文记录与 session 一致）
        if self.wire_path.exists():
            try:
                self.wire_path.unlink()
            except OSError:
                pass
        self.wire_store.seal()
        for cm in messages:
            self.wire_store.append_record(
                {"type": "context.append_message", "message": cm.to_dict()}
            )
        return self.context_memory

    # ---------------------------------------------------------- 重建
    def replay(self) -> ContextMemory:
        """从 wire.jsonl 重现上下文（离线重建）。"""
        return replay(self.wire_store.read_journal())

    # ---------------------------------------------------------- 便捷
    def build_messages(self, system_prompt: str) -> List[ContextMessage]:
        return build_messages(system_prompt, self.context_memory.get())


def create_session_memory(
    session_id: str, home_dir: Any
) -> tuple[ContextMemory, WireStore]:
    """创建一对全新的 (ContextMemory, WireStore)。"""
    home = Path(home_dir)
    sessions_dir = home / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    wire_path = sessions_dir / f"{session_id}.wire.jsonl"
    wire = WireStore(wire_path)
    memory = ContextMemory()
    return memory, wire
