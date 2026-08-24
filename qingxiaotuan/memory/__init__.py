"""记忆系统 —— Hermes Agent 三层记忆架构:

1. 会话上下文 (Session context): 当前对话的消息列表, 超长自动压缩
2. 持久事实记忆 (Persistent facts): MEMORY.md / USER.md, 跨会话保留
3. 程序性技能记忆 (Procedural skills): 见 skills/ 模块

外加 SQLite FTS5 全文索引, 支持跨会话记忆检索 (Hermes 的 cross-session recall)。
"""

from .store import MemoryStore
from .sessions import SessionStore

__all__ = ["MemoryStore", "SessionStore"]
