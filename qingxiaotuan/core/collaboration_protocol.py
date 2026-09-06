"""协作协议 (CollaborationProtocol) —— 多 Agent 间结构化通信的升级版黑板。

原始 Blackboard (core/swarm.py) 是一个简单的 key→value 字典 + 事件流。
协作协议在此基础上增加:
1. **结构化消息**: Agent 间通信不是纯文本, 而是带类型/优先级/引用的消息;
2. **消息路由**: 消息可以定向发给特定角色 (planner/reviewer/worker);
3. **依赖声明**: 消息可以声明「我依赖 X 的结果」, 调度器据此安排执行顺序;
4. **冲突检测**: 当两个 Agent 对同一 key 写入不同值时, 触发冲突检测;
5. **版本控制**: 每次写入都带版本号, 支持 CAS (Compare-And-Swap) 语义;
6. **订阅/通知**: Agent 可以订阅特定 key, 当值变化时收到通知;
7. **审计追溯**: 完整的消息历史, 支持事后审计。
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set

log = logging.getLogger(__name__)


# ============================================================ 消息类型

class MessageType(str, Enum):
    """协作消息类型。"""

    # 数据消息
    RESULT = "result"        # 子任务结果
    ARTIFACT = "artifact"    # 产出物 (代码/文档/报告)
    FEEDBACK = "feedback"    # 反馈 (review 意见)

    # 控制消息
    REQUEST = "request"      # 请求其他 Agent 执行操作
    DEPENDENCY = "dependency"  # 声明依赖
    CONFlict = "conflict"    # 冲突声明

    # 状态消息
    STATUS = "status"        # 状态更新
    PROGRESS = "progress"    # 进度更新
    ERROR = "error"          # 错误报告


class MessagePriority(str, Enum):
    """消息优先级。"""

    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    URGENT = "urgent"


# ============================================================ 协作消息

@dataclass
class CollabMessage:
    """一条协作消息。"""

    msg_id: str
    msg_type: MessageType
    sender: str  # 发送者 Agent ID
    recipient: str  # 接收者 Agent ID 或 "*" (广播)
    key: str  # 消息关联的黑板 key
    value: Any  # 消息内容
    priority: MessagePriority = MessagePriority.NORMAL
    depends_on: List[str] = field(default_factory=list)  # 依赖的 key 列表
    timestamp: float = field(default_factory=time.time)
    version: int = 0  # key 的版本号
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "msg_id": self.msg_id,
            "msg_type": self.msg_type.value,
            "sender": self.sender,
            "recipient": self.recipient,
            "key": self.key,
            "value": str(self.value)[:1000],
            "priority": self.priority.value,
            "depends_on": self.depends_on,
            "timestamp": self.timestamp,
            "version": self.version,
        }


# ============================================================ 冲突

@dataclass
class Conflict:
    """两个 Agent 对同一 key 写入不同值的冲突。"""

    key: str
    value_a: Any  # 第一个写入值
    value_b: Any  # 第二个写入值
    sender_a: str  # 第一个写入者
    sender_b: str  # 第二个写入者
    timestamp: float = field(default_factory=time.time)
    resolved: bool = False
    resolved_value: Any = None  # 解决后的值
    resolver: str = ""  # 谁解决的

    def to_dict(self) -> Dict[str, Any]:
        return {
            "key": self.key,
            "sender_a": self.sender_a,
            "sender_b": self.sender_b,
            "resolved": self.resolved,
            "resolver": self.resolver,
        }


# ============================================================ 协作协议

class CollaborationProtocol:
    """升级版共享黑板 + 协作协议。

    用法:
        protocol = CollaborationProtocol()

        # Agent A 写入结果
        protocol.write("analysis:T1", "分析结果...", sender="agent_a")

        # Agent B 声明依赖并读取
        result = protocol.read("analysis:T1", requester="agent_b")

        # 检测冲突
        conflicts = protocol.get_unresolved_conflicts()
    """

    def __init__(self) -> None:
        self._store: Dict[str, Any] = {}
        self._versions: Dict[str, int] = {}
        self._messages: List[CollabMessage] = []
        self._conflicts: List[Conflict] = []
        self._subscribers: Dict[str, List[Callable[[str, Any], None]]] = {}
        self._lock = threading.Lock()
        self._msg_counter = 0

    def _gen_msg_id(self) -> str:
        self._msg_counter += 1
        return f"msg_{self._msg_counter}"

    # ---------------------------------------------------------- 读写

    def write(
        self,
        key: str,
        value: Any,
        sender: str = "unknown",
        msg_type: MessageType = MessageType.RESULT,
        priority: MessagePriority = MessagePriority.NORMAL,
        depends_on: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> CollabMessage:
        """写入黑板, 带版本控制和冲突检测。"""
        with self._lock:
            version = self._versions.get(key, 0) + 1
            self._versions[key] = version

            # 冲突检测: 如果已有值且来自不同 sender, 触发冲突
            if key in self._store and sender != "__system__":
                existing_meta = None
                for msg in reversed(self._messages):
                    if msg.key == key and msg.msg_type in (
                        MessageType.RESULT, MessageType.ARTIFACT
                    ):
                        existing_meta = msg
                        break
                if existing_meta and existing_meta.sender != sender:
                    # 值不同 = 冲突
                    if str(existing_meta.value) != str(value):
                        conflict = Conflict(
                            key=key,
                            value_a=existing_meta.value,
                            value_b=value,
                            sender_a=existing_meta.sender,
                            sender_b=sender,
                        )
                        self._conflicts.append(conflict)
                        log.warning(
                            "协作冲突检测: key=%s, sender_a=%s, sender_b=%s",
                            key, existing_meta.sender, sender,
                        )

            self._store[key] = value

        # 创建消息
        msg = CollabMessage(
            msg_id=self._gen_msg_id(),
            msg_type=msg_type,
            sender=sender,
            recipient="*",
            key=key,
            value=value,
            priority=priority,
            depends_on=depends_on or [],
            version=version,
            metadata=metadata or {},
        )
        with self._lock:
            self._messages.append(msg)
            if len(self._messages) > 10000:
                self._messages = self._messages[-5000:]

        # 通知订阅者
        self._notify_subscribers(key, value)

        return msg

    def read(
        self, key: str, requester: str = "unknown"
    ) -> Optional[Any]:
        """读取黑板值。"""
        with self._lock:
            return self._store.get(key)

    def read_version(self, key: str) -> int:
        """获取 key 的当前版本号。"""
        with self._lock:
            return self._versions.get(key, 0)

    def compare_and_swap(
        self, key: str, expected_version: int, new_value: Any, sender: str = "unknown"
    ) -> bool:
        """CAS (Compare-And-Swap) 写入: 只有版本号匹配时才写入。"""
        with self._lock:
            current = self._versions.get(key, 0)
            if current != expected_version:
                return False
            self._store[key] = new_value
            self._versions[key] = current + 1
            msg = CollabMessage(
                msg_id=self._gen_msg_id(),
                msg_type=MessageType.RESULT,
                sender=sender,
                recipient="*",
                key=key,
                value=new_value,
                version=current + 1,
            )
            self._messages.append(msg)
            self._notify_subscribers(key, new_value)
            return True

    # ---------------------------------------------------------- 订阅

    def subscribe(self, key: str, callback: Callable[[str, Any], None]) -> None:
        """订阅 key 变更通知。key 为 "*" 时匹配所有变更。"""
        with self._lock:
            self._subscribers.setdefault(key, []).append(callback)

    def _notify_subscribers(self, key: str, value: Any) -> None:
        """通知订阅者 (在锁外调用)。"""
        callbacks = []
        with self._lock:
            callbacks = list(self._subscribers.get(key, []))
            callbacks += list(self._subscribers.get("*", []))
        for cb in callbacks:
            try:
                cb(key, value)
            except Exception:  # noqa: BLE001
                pass

    # ---------------------------------------------------------- 冲突解决

    def get_unresolved_conflicts(self) -> List[Conflict]:
        """获取未解决的冲突。"""
        with self._lock:
            return [c for c in self._conflicts if not c.resolved]

    def resolve_conflict(
        self, conflict_idx: int, resolved_value: Any, resolver: str = "planner"
    ) -> bool:
        """解决冲突: 选择一个值作为最终结果。"""
        with self._lock:
            if conflict_idx < 0 or conflict_idx >= len(self._conflicts):
                return False
            conflict = self._conflicts[conflict_idx]
            conflict.resolved = True
            conflict.resolved_value = resolved_value
            conflict.resolver = resolver
            # 写入解决后的值
            self._store[conflict.key] = resolved_value
            self._versions[conflict.key] = self._versions.get(conflict.key, 0) + 1
            return True

    def auto_resolve_conflicts(self, strategy: str = "last_write") -> int:
        """自动解决所有未解决的冲突。

        strategy:
        - "last_write": 后写入的覆盖先写入的
        - "sender_priority": 高优先级 sender 覆盖低优先级
        """
        conflicts = self.get_unresolved_conflicts()
        resolved_count = 0
        for i, conflict in enumerate(self._conflicts):
            if conflict.resolved:
                continue
            if strategy == "last_write":
                resolved_value = conflict.value_b  # 后写入的
                resolver = conflict.sender_b
            else:
                resolved_value = conflict.value_b
                resolver = "auto_resolver"
            self.resolve_conflict(i, resolved_value, resolver)
            resolved_count += 1
        return resolved_count

    # ---------------------------------------------------------- 查询

    def get_all(self) -> Dict[str, Any]:
        """获取所有黑板内容。"""
        with self._lock:
            return dict(self._store)

    def get_messages(
        self,
        sender: Optional[str] = None,
        msg_type: Optional[MessageType] = None,
        limit: int = 100,
    ) -> List[CollabMessage]:
        """查询消息历史。"""
        with self._lock:
            msgs = self._messages
            if sender:
                msgs = [m for m in msgs if m.sender == sender]
            if msg_type:
                msgs = [m for m in msgs if m.msg_type == msg_type]
            return list(reversed(msgs[-limit:]))

    def context_block(self, requester: str = "") -> str:
        """生成可读的黑板快照 (供 Agent 消费)。"""
        with self._lock:
            if not self._store:
                return "(黑板暂无内容)"
            parts = []
            for k, v in self._store.items():
                version = self._versions.get(k, 0)
                parts.append(f"## 黑板 {k} (v{version})\n{v}")
            # 附加未解决冲突
            unresolved = [c for c in self._conflicts if not c.resolved]
            if unresolved:
                parts.append("## ⚠️ 未解决冲突")
                for c in unresolved:
                    parts.append(
                        f"- key={c.key}: {c.sender_a} vs {c.sender_b}"
                    )
            return "\n\n".join(parts)

    def stats(self) -> Dict[str, Any]:
        """返回统计信息。"""
        with self._lock:
            return {
                "keys": len(self._store),
                "messages": len(self._messages),
                "conflicts_total": len(self._conflicts),
                "conflicts_unresolved": sum(
                    1 for c in self._conflicts if not c.resolved
                ),
                "subscribers": len(self._subscribers),
            }

    def clear(self) -> None:
        """清空黑板。"""
        with self._lock:
            self._store.clear()
            self._versions.clear()
            self._messages.clear()
            self._conflicts.clear()
            self._subscribers.clear()
