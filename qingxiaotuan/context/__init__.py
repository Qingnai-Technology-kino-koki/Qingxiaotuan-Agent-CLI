"""上下文子系统 —— 借鉴 Claude Code 的大上下文机制。

两个部件:
- indexer: 一次性扫描工作区, 生成"代码库地图" (结构 / 规模 / 关键文件)。
- manager: 在长会话里按优先级管理上下文, 智能压缩旧历史, 关键信息不丢。
"""

from .indexer import CodebaseIndexer
from .manager import ContextManager

__all__ = ["CodebaseIndexer", "ContextManager"]
