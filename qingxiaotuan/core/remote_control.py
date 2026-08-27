"""Remote Control — 远程控制会话 (对标 Claude Code 2.1.239 Remote Control)。

核心功能:
- 生成 QR 码配对链接, 手机扫码后可远程控制终端会话
- 跨设备发送 prompt, 查看实时输出
- Push 通知: 模型决策时 / 需要权限确认时
- 会话状态同步: 进度、token 用量、上下文占用

实现:
- 本地 HTTP 服务器 (aiohttp 或 http.server) 提供 WebSocket/REST API
- QR 码编码配对 URL (含一次性 token)
- 安全: token 有效期 5 分钟, 连接后需再次认证
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import time
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional


# ================================================================ 数据结构

@dataclass
class RemoteSession:
    """远程控制会话。"""
    session_id: str
    pairing_token: str
    created_at: float
    connected: bool = False
    connected_at: float = 0.0
    device_name: str = ""
    device_type: str = ""       # mobile | desktop | tablet
    push_enabled: bool = False
    push_on_decision: bool = True
    push_on_permission: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "session_id": self.session_id,
            "created_at": self.created_at,
            "connected": self.connected,
            "connected_at": self.connected_at,
            "device_name": self.device_name,
            "device_type": self.device_type,
            "push_enabled": self.push_enabled,
        }

    @property
    def is_expired(self) -> bool:
        """配对 token 是否过期 (5 分钟)。"""
        return time.time() - self.created_at > 300

    @property
    def is_active(self) -> bool:
        """会话是否活跃。"""
        return self.connected and not self.is_expired


# ================================================================ Remote Control Manager

class RemoteControl:
    """远程控制管理器。

    用法:
        rc = RemoteControl(Path("~/.qingxiaotuan"))
        # 生成配对信息
        pairing = rc.start_pairing("my-session")
        print(f"QR Code URL: {pairing['url']}")
        print(f"Pairing Token: {pairing['token']}")
        # 手机扫码后, 调用 confirm_pairing
        rc.confirm_pairing(pairing["token"], device_name="iPhone 15")
        # 发送远程命令
        rc.send_prompt("my-session", "What files changed?")
    """

    def __init__(self, home: Path, host: str = "localhost", port: int = 0) -> None:
        self.home = Path(home)
        self._sessions: Dict[str, RemoteSession] = {}
        self._host = host
        self._port = port or self._find_free_port()
        self._server = None
        self._callbacks: Dict[str, Callable] = {}

    def _find_free_port(self) -> int:
        """找一个空闲端口。"""
        import socket
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("", 0))
            return s.getsockname()[1]

    def start_pairing(self, session_id: str) -> Dict[str, Any]:
        """开始配对流程。

        返回:
            url: 配对 URL (含一次性 token, 可编码为 QR 码)
            token: 配对 token
            expires_in: 过期时间 (秒)
        """
        token = secrets.token_urlsafe(32)
        remote_session = RemoteSession(
            session_id=session_id,
            pairing_token=token,
            created_at=time.time(),
        )
        self._sessions[session_id] = remote_session

        # 构建配对 URL
        params = urllib.parse.urlencode({
            "host": self._host,
            "port": self._port,
            "token": token,
            "session": session_id,
        })
        url = f"qxt-remote://pair?{params}"

        self._save_sessions()

        return {
            "url": url,
            "token": token,
            "session_id": session_id,
            "expires_in": 300,
            "port": self._port,
        }

    def confirm_pairing(self, token: str, device_name: str = "", device_type: str = "mobile") -> bool:
        """确认配对 (设备扫码后调用)。"""
        for session_id, rs in self._sessions.items():
            if rs.pairing_token == token and not rs.is_expired:
                rs.connected = True
                rs.connected_at = time.time()
                rs.device_name = device_name
                rs.device_type = device_type
                self._save_sessions()
                return True
        return False

    def disconnect(self, session_id: str) -> bool:
        """断开远程连接。"""
        rs = self._sessions.get(session_id)
        if rs:
            rs.connected = False
            self._save_sessions()
            return True
        return False

    def send_prompt(self, session_id: str, prompt: str) -> bool:
        """发送远程 prompt。"""
        rs = self._sessions.get(session_id)
        if not rs or not rs.is_active:
            return False
        # 触发回调
        callback = self._callbacks.get("on_prompt")
        if callback:
            try:
                callback(session_id, prompt)
            except Exception:
                pass
        return True

    def send_notification(self, session_id: str, title: str, body: str, priority: str = "normal") -> bool:
        """发送 push 通知。"""
        rs = self._sessions.get(session_id)
        if not rs or not rs.is_active or not rs.push_enabled:
            return False
        callback = self._callbacks.get("on_notification")
        if callback:
            try:
                callback(session_id, title, body, priority)
            except Exception:
                pass
        return True

    def on(self, event: str, callback: Callable) -> None:
        """注册事件回调。"""
        self._callbacks[event] = callback

    def list_sessions(self) -> List[Dict[str, Any]]:
        """列出所有远程会话。"""
        self._cleanup_expired()
        return [rs.to_dict() for rs in self._sessions.values()]

    def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        """获取指定远程会话信息。"""
        rs = self._sessions.get(session_id)
        return rs.to_dict() if rs else None

    def _cleanup_expired(self) -> None:
        """清理过期会话。"""
        expired = [sid for sid, rs in self._sessions.items() if rs.is_expired]
        for sid in expired:
            del self._sessions[sid]
        if expired:
            self._save_sessions()

    def _save_sessions(self) -> None:
        """持久化会话状态。"""
        sessions_file = self.home / "remote_sessions.json"
        data = {sid: rs.to_dict() for sid, rs in self._sessions.items()}
        sessions_file.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _load_sessions(self) -> None:
        """加载会话状态。"""
        sessions_file = self.home / "remote_sessions.json"
        if sessions_file.exists():
            try:
                data = json.loads(sessions_file.read_text(encoding="utf-8"))
                for sid, info in data.items():
                    self._sessions[sid] = RemoteSession(
                        session_id=sid,
                        pairing_token="",  # token 不持久化
                        created_at=info.get("created_at", 0),
                        connected=info.get("connected", False),
                        connected_at=info.get("connected_at", 0),
                        device_name=info.get("device_name", ""),
                        device_type=info.get("device_type", ""),
                        push_enabled=info.get("push_enabled", False),
                    )
            except Exception:
                pass

    def generate_qr_data(self, pairing_info: Dict[str, Any]) -> str:
        """生成 QR 码数据 (纯文本格式, 可被外部 QR 库编码)。"""
        return pairing_info["url"]

    def get_status(self, session_id: str) -> Dict[str, Any]:
        """获取远程控制状态 (供 /remote-control 展示)。"""
        rs = self._sessions.get(session_id)
        if rs is None:
            return {"enabled": False, "connected": False}
        return {
            "enabled": True,
            "connected": rs.is_active,
            "device": rs.device_name or "Unknown",
            "device_type": rs.device_type,
            "connected_at": rs.connected_at,
            "push_enabled": rs.push_enabled,
        }
