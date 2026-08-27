"""跨会话消息总线 (对标 Claude Code Cross-Session Messaging)。

Claude Code 2.1.239 的跨会话通信:
- SendMessage: 从当前会话向另一个会话发送消息
- ListAgents: 列出当前活跃的会话/代理
- 跨机器: Windows 也支持 (2.1.239 新增)

青小团实现:
- 基于文件的消息总线 (~/.qingxiaotuan/messages/), 支持本地多进程
- 每个活跃会话注册一个 inbox (JSONL 文件)
- SendMessage 写入目标 inbox, ListAgents 扫描活跃 inbox
- 消息 TTL: 默认 5 分钟过期 (防止垃圾堆积)
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

# 消息 TTL (秒): 超过此时限未被读取的消息视为过期
_DEFAULT_TTL = 300  # 5 分钟

# 会话注册信息保留时限 (秒): 超过此时限无心跳的会话视为已退出
_SESSION_STALE = 120  # 2 分钟


@dataclass
class BusMessage:
    """一条跨会话消息。"""
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    from_session: str = ""
    to_session: str = ""
    content: str = ""
    ts: float = field(default_factory=time.time)
    ttl: float = _DEFAULT_TTL
    read: bool = False


class MessageBus:
    """跨会话消息总线。"""

    def __init__(self, home: Optional[Path] = None, ttl: float = _DEFAULT_TTL) -> None:
        from ..config.loader import home_dir
        self._home = home or home_dir()
        self._msg_dir = self._home / "messages"
        self._inbox_dir = self._msg_dir / "inboxes"
        self._registry_dir = self._msg_dir / "sessions"
        self._ttl = ttl
        # 确保目录存在
        self._msg_dir.mkdir(parents=True, exist_ok=True)
        self._inbox_dir.mkdir(parents=True, exist_ok=True)
        self._registry_dir.mkdir(parents=True, exist_ok=True)

    def register(self, session_id: str, meta: Optional[Dict[str, Any]] = None) -> None:
        """注册当前会话 (写入心跳文件, 暴露 session_id/模型/工作区)。"""
        reg_file = self._registry_dir / f"{session_id}.json"
        info = {
            "session_id": session_id,
            "pid": os.getpid(),
            "registered_at": time.time(),
            "last_heartbeat": time.time(),
            "meta": meta or {},
        }
        try:
            reg_file.write_text(json.dumps(info, ensure_ascii=False), encoding="utf-8")
        except OSError as exc:
            log.debug("会话注册失败: %s", exc)

    def heartbeat(self, session_id: str) -> None:
        """更新心跳。"""
        reg_file = self._registry_dir / f"{session_id}.json"
        try:
            if reg_file.exists():
                info = json.loads(reg_file.read_text(encoding="utf-8"))
                info["last_heartbeat"] = time.time()
                reg_file.write_text(json.dumps(info, ensure_ascii=False), encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass

    def unregister(self, session_id: str) -> None:
        """注销会话。"""
        reg_file = self._registry_dir / f"{session_id}.json"
        try:
            reg_file.unlink(missing_ok=True)
        except OSError:
            pass

    def send(self, from_session: str, to_session: str, content: str) -> str:
        """向目标会话 inbox 发送消息。返回消息 ID。"""
        msg = BusMessage(
            from_session=from_session,
            to_session=to_session,
            content=content,
            ttl=self._ttl,
        )
        inbox = self._inbox_dir / f"{to_session}.jsonl"
        entry = {
            "id": msg.id,
            "from": msg.from_session,
            "content": msg.content,
            "ts": msg.ts,
            "ttl": msg.ttl,
            "read": False,
        }
        try:
            with open(inbox, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError as exc:
            log.warning("发送消息失败: %s", exc)
            return ""
        log.info("消息已发送: %s → %s (id=%s)", from_session, to_session, msg.id)
        return msg.id

    def receive(self, session_id: str, mark_read: bool = True) -> List[BusMessage]:
        """读取指定会话的所有未过期、未读消息。"""
        inbox = self._inbox_dir / f"{session_id}.jsonl"
        if not inbox.exists():
            return []

        now = time.time()
        messages: List[BusMessage] = []
        remaining_lines: List[str] = []

        try:
            with open(inbox, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    age = now - entry.get("ts", 0)
                    ttl = entry.get("ttl", self._ttl)
                    is_read = entry.get("read", False)

                    # 过期或已读: 不返回, 不保留
                    if age > ttl:
                        continue
                    if is_read and mark_read:
                        continue

                    msg = BusMessage(
                        id=entry.get("id", ""),
                        from_session=entry.get("from", ""),
                        to_session=session_id,
                        content=entry.get("content", ""),
                        ts=entry.get("ts", 0),
                        ttl=ttl,
                        read=is_read,
                    )
                    messages.append(msg)

                    # 标记已读
                    if mark_read and not is_read:
                        entry["read"] = True
                        remaining_lines.append(json.dumps(entry, ensure_ascii=False))
                    else:
                        remaining_lines.append(line)
        except OSError as exc:
            log.debug("读取消息失败: %s", exc)
            return []

        # 回写 (清理过期+已读)
        try:
            inbox.write_text("\n".join(remaining_lines) + ("\n" if remaining_lines else ""), encoding="utf-8")
        except OSError:
            pass

        return messages

    def list_agents(self, include_self: bool = True, session_id: str = "") -> List[Dict[str, Any]]:
        """列出所有活跃的会话/代理 (心跳在 STALE 时限内)。"""
        now = time.time()
        agents: List[Dict[str, Any]] = []

        for reg_file in self._registry_dir.glob("*.json"):
            try:
                info = json.loads(reg_file.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                continue

            if not include_self and info.get("session_id") == session_id:
                continue

            # 检查是否存活
            last_hb = info.get("last_heartbeat", 0)
            if now - last_hb > _SESSION_STALE:
                # 超时: 清理
                try:
                    reg_file.unlink(missing_ok=True)
                except OSError:
                    pass
                continue

            agents.append({
                "session_id": info.get("session_id", ""),
                "pid": info.get("pid", 0),
                "uptime": int(now - info.get("registered_at", now)),
                "meta": info.get("meta", {}),
            })

        return agents

    def cleanup(self) -> int:
        """清理过期消息和死会话。返回清理的会话数。"""
        now = time.time()
        cleaned = 0

        # 清理死会话
        for reg_file in self._registry_dir.glob("*.json"):
            try:
                info = json.loads(reg_file.read_text(encoding="utf-8"))
                if now - info.get("last_heartbeat", 0) > _SESSION_STALE * 2:
                    reg_file.unlink()
                    cleaned += 1
            except Exception:  # noqa: BLE001
                reg_file.unlink(missing_ok=True)

        return cleaned


# 全局单例 (进程内共享)
_bus: Optional[MessageBus] = None


def get_message_bus() -> MessageBus:
    """获取全局消息总线单例。"""
    global _bus
    if _bus is None:
        _bus = MessageBus()
    return _bus
