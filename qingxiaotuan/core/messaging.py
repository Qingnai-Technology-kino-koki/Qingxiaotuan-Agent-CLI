"""Cross-Session Messaging — [已弃用] 请使用 core.message_bus。

.. deprecated:: 0.2.015
    此模块已弃用, 生产代码请使用 core.message_bus (更简单、已被 tools/messaging_tool.py 使用)。
    此处保留仅供 test_new_features_v2.py 的向后兼容测试。

跨会话消息通信 (对标 Claude Code 2.1.239 SendMessage / ListAgents)。

核心概念:
- 每个 Agent 会话有一个唯一 session_id 和可选的 session_name
- 会话间可通过 SendMessage 互相发送消息
- ListAgents 发现同一台机器上的活跃会话
- 支持同机多会话和跨机通信 (via file-based message queue)

存储:
- 每个会话在 QXT_HOME/sessions/<session_id>/ 下维护一个 messaging.json
- 消息队列: QXT_HOME/messaging/<target_session_id>.jsonl
- 会话注册: QXT_HOME/messaging/_registry.json
"""

from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional


# ================================================================ 数据结构

@dataclass
class PeerSession:
    """活跃会话信息。"""
    session_id: str
    name: str
    working_dir: str
    started_at: float
    last_active: float
    status: str = "active"          # active | idle | background
    pid: int = 0
    model: str = ""
    capabilities: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "session_id": self.session_id,
            "name": self.name,
            "working_dir": self.working_dir,
            "started_at": self.started_at,
            "last_active": self.last_active,
            "status": self.status,
            "pid": self.pid,
            "model": self.model,
            "capabilities": self.capabilities,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PeerSession":
        return cls(
            session_id=str(data.get("session_id", "")),
            name=str(data.get("name", "")),
            working_dir=str(data.get("working_dir", "")),
            started_at=float(data.get("started_at", 0)),
            last_active=float(data.get("last_active", 0)),
            status=str(data.get("status", "active")),
            pid=int(data.get("pid", 0)),
            model=str(data.get("model", "")),
            capabilities=data.get("capabilities", []) or [],
        )


@dataclass
class CrossMessage:
    """跨会话消息。"""
    message_id: str
    from_session: str
    from_name: str
    to_session: str
    content: str
    timestamp: float
    message_type: str = "text"       # text | task | result | error
    read: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "message_id": self.message_id,
            "from_session": self.from_session,
            "from_name": self.from_name,
            "to_session": self.to_session,
            "content": self.content,
            "timestamp": self.timestamp,
            "message_type": self.message_type,
            "read": self.read,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CrossMessage":
        return cls(
            message_id=str(data.get("message_id", "")),
            from_session=str(data.get("from_session", "")),
            from_name=str(data.get("from_name", "")),
            to_session=str(data.get("to_session", "")),
            content=str(data.get("content", "")),
            timestamp=float(data.get("timestamp", 0)),
            message_type=str(data.get("message_type", "text")),
            read=bool(data.get("read", False)),
            metadata=data.get("metadata", {}) or {},
        )


# ================================================================ 消息总线

class MessageBus:
    """跨会话消息总线。

    用法:
        bus = MessageBus(Path("~/.qingxiaotuan"))
        bus.register("session-123", "my-session", "/workspace", pid=1234)
        bus.send("session-123", "session-456", "Hello!")
        messages = bus.receive("session-456")
    """

    def __init__(self, home: Path) -> None:
        self.home = Path(home)
        self._messaging_dir = self.home / "messaging"
        self._messaging_dir.mkdir(parents=True, exist_ok=True)
        self._registry_path = self._messaging_dir / "_registry.json"
        self._registry: Dict[str, PeerSession] = {}
        self._load_registry()

    def _load_registry(self) -> None:
        """加载会话注册表。"""
        if self._registry_path.exists():
            try:
                data = json.loads(self._registry_path.read_text(encoding="utf-8"))
                for sid, info in data.items():
                    self._registry[sid] = PeerSession.from_dict(info)
            except Exception:
                self._registry = {}

    def _save_registry(self) -> None:
        """保存会话注册表。"""
        data = {sid: s.to_dict() for sid, s in self._registry.items()}
        self._registry_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def register(
        self,
        session_id: str,
        name: str,
        working_dir: str,
        pid: int = 0,
        model: str = "",
        capabilities: Optional[List[str]] = None,
    ) -> None:
        """注册当前会话。"""
        now = time.time()
        self._registry[session_id] = PeerSession(
            session_id=session_id,
            name=name or session_id[:8],
            working_dir=working_dir,
            started_at=now,
            last_active=now,
            pid=pid,
            model=model,
            capabilities=capabilities or [],
        )
        self._save_registry()

    def unregister(self, session_id: str) -> None:
        """注销会话。"""
        self._registry.pop(session_id, None)
        self._save_registry()
        # 清理该会话的消息队列
        queue_path = self._messaging_dir / f"{session_id}.jsonl"
        if queue_path.exists():
            queue_path.unlink(missing_ok=True)

    def heartbeat(self, session_id: str) -> None:
        """更新会话活跃时间。"""
        if session_id in self._registry:
            self._registry[session_id].last_active = time.time()
            self._save_registry()

    def list_agents(self, include_self: bool = False, self_id: str = "") -> List[PeerSession]:
        """列出活跃会话。

        清理超过 5 分钟无心跳的会话。
        """
        now = time.time()
        stale_threshold = 300  # 5 分钟
        active: List[PeerSession] = []
        stale: List[str] = []

        for sid, session in self._registry.items():
            if now - session.last_active > stale_threshold:
                stale.append(sid)
                continue
            if not include_self and sid == self_id:
                continue
            active.append(session)

        # 清理过期会话
        for sid in stale:
            self._registry.pop(sid, None)
        if stale:
            self._save_registry()

        return sorted(active, key=lambda s: s.last_active, reverse=True)

    def send(
        self,
        from_session: str,
        to_session: str,
        content: str,
        message_type: str = "text",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> CrossMessage:
        """发送消息到目标会话。"""
        from_info = self._registry.get(from_session)
        from_name = from_info.name if from_info else from_session[:8]

        msg = CrossMessage(
            message_id=str(uuid.uuid4()),
            from_session=from_session,
            from_name=from_name,
            to_session=to_session,
            content=content,
            timestamp=time.time(),
            message_type=message_type,
            metadata=metadata or {},
        )

        # 写入目标会话的消息队列
        queue_path = self._messaging_dir / f"{to_session}.jsonl"
        with open(queue_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(msg.to_dict(), ensure_ascii=False) + "\n")

        return msg

    def receive(self, session_id: str, mark_read: bool = True) -> List[CrossMessage]:
        """接收目标会话的未读消息。"""
        queue_path = self._messaging_dir / f"{session_id}.jsonl"
        if not queue_path.exists():
            return []

        messages: List[CrossMessage] = []
        remaining: List[str] = []

        try:
            lines = queue_path.read_text(encoding="utf-8").splitlines()
            for line in lines:
                if not line.strip():
                    continue
                try:
                    data = json.loads(line)
                    msg = CrossMessage.from_dict(data)
                    if not msg.read:
                        messages.append(msg)
                        if mark_read:
                            msg.read = True
                    remaining.append(json.dumps(msg.to_dict(), ensure_ascii=False))
                except Exception:
                    remaining.append(line)

        except Exception:
            return []

        # 写回 (已标记已读的)
        if mark_read and messages:
            queue_path.write_text("\n".join(remaining) + "\n", encoding="utf-8")

        return messages

    def unread_count(self, session_id: str) -> int:
        """获取未读消息数。"""
        queue_path = self._messaging_dir / f"{session_id}.jsonl"
        if not queue_path.exists():
            return 0
        count = 0
        try:
            for line in queue_path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    data = json.loads(line)
                    if not data.get("read", False):
                        count += 1
                except Exception:
                    pass
        except Exception:
            pass
        return count

    def discard_messages(self, session_id: str) -> int:
        """丢弃目标会话的所有消息 (rate limiting 或队列满时)。"""
        queue_path = self._messaging_dir / f"{session_id}.jsonl"
        if not queue_path.exists():
            return 0
        count = 0
        try:
            lines = queue_path.read_text(encoding="utf-8").splitlines()
            count = sum(1 for l in lines if l.strip())
        except Exception:
            pass
        queue_path.write_text("", encoding="utf-8")
        return count

    def cleanup(self) -> int:
        """清理所有过期会话和空队列。"""
        now = time.time()
        stale_threshold = 600  # 10 分钟
        removed = 0
        stale = [sid for sid, s in self._registry.items()
                 if now - s.last_active > stale_threshold]
        for sid in stale:
            self._registry.pop(sid, None)
            queue_path = self._messaging_dir / f"{sid}.jsonl"
            if queue_path.exists():
                queue_path.unlink(missing_ok=True)
            removed += 1
        if stale:
            self._save_registry()
        return removed
