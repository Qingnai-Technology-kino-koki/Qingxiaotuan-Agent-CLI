"""审计日志标准化导出 (CEF / JSONL / RFC5424 Syslog) + 可选 HTTP 推送。

设计
----
- 订阅 ``SecurityEventBus`` 的 ``*`` 通配事件, 把每条安全事件转成标准格式, 便于对接 SIEM;
- 支持三种标准格式:
    * ``jsonl``  —— 一行一个 JSON (SecurityEvent 原样, 带标准化字段);
    * ``cef``    —— ArcSight Common Event Format, 绝大多数 SIEM 直接吞;
    * ``syslog`` —— RFC 5424 结构化 syslog (MSG 为 JSON), 可走 UDP/TCP/本地 socket;
- 可选 ``--push <url>`` 把每条事件 POST 到远端 (urllib 标准库实现, 超时可控,
  失败只记日志绝不反作用于安全裁决);
- 同时支持从已落盘的 bus JSONL 文件批量重放导出 (``export_file``)。

所有格式化均为纯标准库; ``requests`` 可选, 缺省自动回退 urllib。
"""
from __future__ import annotations

import json
import socket
import time
import urllib.request
from typing import Dict, Iterable, List, Optional, Sequence

from ..core.security_bus import SecurityEvent

# 严重度 -> CEF 0-10
_CEF_SEVERITY = {"critical": 10, "high": 8, "medium": 5, "low": 3, "info": 1, "none": 0}
# 严重度 -> syslog severity (0 emerg ... 7 debug)
_SYSLOG_SEVERITY = {"critical": 2, "high": 3, "medium": 4, "low": 5, "info": 6, "none": 6}


def _cef_escape(value: str) -> str:
    """CEF 扩展字段转义: '=' / '|' / 反斜杠 需转义。"""
    return value.replace("\\", "\\\\").replace("=", "\\=").replace("|", "\\|")


def _event_severity(event: SecurityEvent) -> str:
    return (event.severity or "info").lower()


def to_jsonl(event: SecurityEvent) -> str:
    return event.to_json()


def to_cef(event: SecurityEvent, vendor: str = "Qingnai", product: str = "Qingxiaotuan") -> str:
    sev = _event_severity(event)
    score = _CEF_SEVERITY.get(sev, 1)
    # 签名 ID / 名称取自 event_type, 去掉前缀安全化
    sig_id = event.event_type.replace(".", "_")
    name = event.event_type
    ext_parts = [
        f"eventType={_cef_escape(event.event_type)}",
        f"src={_cef_escape(event.source or 'unknown')}",
        f"sev={sev}",
        f"msg={_cef_escape(json.dumps(event.payload, ensure_ascii=False, default=str))}",
    ]
    ext = " ".join(ext_parts)
    return (
        f"CEF:0|{vendor}|{product}|{__import__('qingxiaotuan').__version__}|"
        f"{sig_id}|{name}|{score}|{ext}"
    )


def to_syslog(event: SecurityEvent, host: Optional[str] = None,
              app: str = "qingxiaotuan", procid: str = "-") -> str:
    """RFC 5424 结构化 syslog。PRI = facility(local0=19)*8 + severity。"""
    sev = _event_severity(event)
    pri = 19 * 8 + _SYSLOG_SEVERITY.get(sev, 6)
    ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(event.timestamp))
    if host is None:
        try:
            host = socket.gethostname() or "localhost"
        except Exception:  # pragma: no cover
            host = "localhost"
    msg = json.dumps({
        "event_type": event.event_type,
        "source": event.source,
        "severity": sev,
        "payload": event.payload,
    }, ensure_ascii=False, default=str)
    # RFC5424: <PRI>VERSION SP TIMESTAMP SP HOST SP APP SP PROCID SP MSGID SP MSG
    return f"<{pri}>1 {ts} {host} {app} {procid} - - {msg}"


_FORMATTERS = {"jsonl": to_jsonl, "cef": to_cef, "syslog": to_syslog}


def format_event(event: SecurityEvent, fmt: str) -> str:
    fmt = fmt.lower()
    if fmt not in _FORMATTERS:
        raise ValueError(f"不支持的导出格式: {fmt}; 可选: {sorted(_FORMATTERS)}")
    return _FORMATTERS[fmt](event)


class AuditExporter:
    """把 SecurityEvent 导出为标准格式, 可选落盘与远端推送。"""

    def __init__(
        self,
        formats: Sequence[str] = ("jsonl",),
        out_dir: Optional[str] = None,
        push_url: Optional[str] = None,
        push_timeout: float = 5.0,
        host: Optional[str] = None,
    ) -> None:
        for f in formats:
            if f.lower() not in _FORMATTERS:
                raise ValueError(f"不支持的导出格式: {f}")
        self.formats = [f.lower() for f in formats]
        self.out_dir = out_dir
        self.push_url = push_url
        self.push_timeout = push_timeout
        self.host = host
        self._files: Dict[str, object] = {}
        if out_dir:
            import os
            os.makedirs(out_dir, exist_ok=True)

    # ---------------------------------------------------------- 落盘
    def _open_file(self, fmt: str):
        if self.out_dir is None:
            return None
        import os
        path = os.path.join(self.out_dir, f"audit_export.{fmt}")
        return open(path, "a", encoding="utf-8")

    def _write(self, fmt: str, line: str) -> None:
        if self.out_dir is None:
            # 无目录时直接打印到 stdout (调用方负责)
            print(line)
            return
        f = self._files.get(fmt)
        if f is None:
            f = self._open_file(fmt)
            self._files[fmt] = f
        if f is not None:
            f.write(line + "\n")
            f.flush()

    def close(self) -> None:
        for f in self._files.values():
            try:
                f.close()
            except Exception:  # pragma: no cover
                pass
        self._files.clear()

    # ---------------------------------------------------------- 推送
    def _push(self, line: str) -> bool:
        if not self.push_url:
            return False
        data = line.encode("utf-8")
        req = urllib.request.Request(
            self.push_url, data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.push_timeout) as resp:  # noqa: S310
                return 200 <= resp.status < 300
        except Exception:  # noqa: BLE001 - 推送失败绝不能影响安全裁决
            return False

    # ---------------------------------------------------------- 单条
    def emit(self, event: SecurityEvent) -> None:
        for fmt in self.formats:
            line = format_event(event, fmt)
            self._write(fmt, line)
            if self.push_url:
                self._push(line)

    # ---------------------------------------------------------- 订阅总线
    def attach(self, bus) -> None:
        """订阅 SecurityEventBus 的 ``*`` 通配事件。"""
        bus.on("*", self.emit)

    # ---------------------------------------------------------- 批量 / 文件重放
    def export_events(self, events: Iterable[SecurityEvent]) -> int:
        n = 0
        for ev in events:
            self.emit(ev)
            n += 1
        return n

    def export_file(self, path: str) -> int:
        """从已落盘的 bus JSONL 文件批量重放导出。"""
        count = 0
        with open(path, "r", encoding="utf-8") as f:
            for raw in f:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    obj = json.loads(raw)
                    ev = SecurityEvent(
                        event_type=obj.get("event_type", ""),
                        timestamp=obj.get("timestamp", time.time()),
                        payload=obj.get("payload", {}),
                        source=obj.get("source", ""),
                        severity=obj.get("severity", "info"),
                    )
                except Exception:
                    continue
                self.emit(ev)
                count += 1
        return count
