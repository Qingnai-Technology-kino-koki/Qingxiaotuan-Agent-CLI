"""审计日志后端 —— 记录模型 / 工具 / 权限 / 任务生命周期事件, 支持过滤查询与敏感脱敏。

设计要点 (对标 Claude Code 的审计能力):
- 事件落盘为 JSONL (一行一条), 同时保留进程内环形缓冲供即时查询。
- 落盘前强制脱敏: API Key、token、密码、私钥等敏感字段绝不写明文。
- 通配订阅内核事件总线 ("*"), 不侵入各业务模块; 任何内核事件自动归档。
- 查询支持按类型 / 时间窗 / 关键词过滤, 返回结构化记录 (不暴露已脱敏原文)。
- 异常隔离: 审计写入失败只记日志, 绝不中断 Agent 主循环 (可观测性是"锦上添花")。
"""

from __future__ import annotations

import json
import logging
import re
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger("qingxiaotuan.audit")

# 进程内环形缓冲上限: 防止超长会话无限占用内存。
MAX_BUFFER = 2000

# 需要脱敏的字段名 (键名命中即脱敏其值)。
_SENSITIVE_KEYS = (
    "api_key", "apikey", "api-key", "token", "access_token", "refresh_token",
    "secret", "password", "passwd", "private_key", "authorization", "auth",
    "bearer", "credential", "credentials", "key", "cookie", "session",
)

# 命令行/参数里可能泄露密钥的正则 (值侧兜底脱敏)。
_SENSITIVE_PATTERNS = (
    re.compile(r"(sk-[A-Za-z0-9]{8,})"),                          # OpenAI 风格
    re.compile(r"(AKIA[0-9A-Z]{16})"),                            # AWS
    re.compile(r"([Bb]earer\s+[A-Za-z0-9._-]{12,})"),             # Bearer token
    re.compile(r"([Aa]pi[_-]?[Kk]ey[\"'=:\s]+[A-Za-z0-9._-]{8,})"),
    re.compile(r"([Pp]assword[\"'=:\s]+[^\s\"']{6,})"),
)

_MASK = "***"


def _redact_text(text: str) -> str:
    """对自由文本做兜底脱敏: 命中密钥正则的部分打码。"""
    if not text:
        return text
    for pat in _SENSITIVE_PATTERNS:
        text = pat.sub(lambda m: m.group(1)[:5] + _MASK, text)
    return text


def _redact_value(key: str, value: Any) -> Any:
    """按键名或内容判断是否敏感, 命中则返回脱敏串。"""
    low = (key or "").lower().replace(" ", "").replace("-", "").replace("_", "")
    if low in _SENSITIVE_KEYS or low.endswith("key") or low.endswith("token") \
            or low.endswith("secret") or low.endswith("password"):
        if value in (None, "", _MASK):
            return value
        return _MASK
    # 字符串值做正则兜底 (即便键名不敏感, 内容含密钥也脱敏)
    if isinstance(value, str):
        return _redact_text(value)
    return value


def redact(payload: Dict[str, Any]) -> Dict[str, Any]:
    """递归脱敏一个事件载荷, 返回新的脱敏副本 (不修改原对象)。"""
    if not isinstance(payload, dict):
        return payload
    out: Dict[str, Any] = {}
    for key, value in payload.items():
        if isinstance(value, dict):
            out[key] = redact(value)
        elif isinstance(value, list):
            out[key] = [
                redact(v) if isinstance(v, dict) else _redact_value(key, v)
                for v in value
            ]
        else:
            out[key] = _redact_value(key, value)
    return out


@dataclass
class AuditEvent:
    """一条审计记录。"""
    seq: int
    ts: float
    type: str
    payload: Dict[str, Any]
    raw: Dict[str, Any] = field(default_factory=dict)  # 脱敏前原文 (仅内存, 不落盘)

    def to_line(self) -> str:
        """落盘行: 仅含脱敏后的 payload。"""
        rec = {
            "seq": self.seq,
            "ts": self.ts,
            "iso": datetime.fromtimestamp(self.ts, tz=timezone.utc).isoformat(),
            "type": self.type,
            "payload": self.payload,
        }
        return json.dumps(rec, ensure_ascii=False, default=str)

    @staticmethod
    def from_line(line: str) -> "AuditEvent":
        rec = json.loads(line)
        return AuditEvent(
            seq=rec.get("seq", 0),
            ts=rec.get("ts", 0.0),
            type=rec.get("type", ""),
            payload=rec.get("payload", {}),
        )

    def matches(self, query: "AuditQuery") -> bool:
        return query.match(self)


@dataclass
class AuditQuery:
    """审计查询条件。"""
    types: Tuple[str, ...] = ()
    since: Optional[float] = None
    until: Optional[float] = None
    keyword: str = ""
    limit: int = 0  # >0 时仅返回最早的前 N 条 (0 = 不限)

    def match(self, ev: AuditEvent) -> bool:
        if self.types and ev.type not in self.types:
            return False
        if self.since is not None and ev.ts < self.since:
            return False
        if self.until is not None and ev.ts > self.until:
            return False
        if self.keyword:
            hay = f"{ev.type} {json.dumps(ev.payload, ensure_ascii=False, default=str)}".lower()
            if self.keyword.lower() not in hay:
                return False
        return True


class AuditStore:
    """审计日志存储: JSONL 持久化 + 进程内环形缓冲。"""

    def __init__(self, home: Path, enabled: bool = True, persist: bool = True) -> None:
        self.dir = home / "audit"
        self.enabled = enabled
        self.persist = persist
        self._buffer: List[AuditEvent] = []
        self._seq = 0
        self._lock = threading.Lock()
        self._path: Optional[Path] = None
        if self.persist and self.enabled:
            try:
                self.dir.mkdir(parents=True, exist_ok=True)
                self._path = self.dir / "audit.log"
            except OSError as exc:
                log.warning("审计目录创建失败 (降级为仅内存): %s", exc)
                self.persist = False

    # ------------------------------------------------------------- 写入

    def log(self, event_type: str, payload: Dict[str, Any]) -> None:
        """记录一条审计事件 (自动脱敏 + 落盘 + 入缓冲)。"""
        if not self.enabled:
            return
        safe = redact(payload)
        with self._lock:
            self._seq += 1
            ev = AuditEvent(seq=self._seq, ts=__import__("time").time(),
                            type=event_type, payload=safe, raw=payload)
            self._buffer.append(ev)
            if len(self._buffer) > MAX_BUFFER:
                self._buffer.pop(0)
            if self.persist and self._path is not None:
                try:
                    with self._path.open("a", encoding="utf-8") as fh:
                        fh.write(ev.to_line() + "\n")
                except OSError as exc:  # noqa: BLE001
                    log.warning("审计落盘失败 (已忽略): %s", exc)

    # ------------------------------------------------------------- 查询

    def query(self, query: Optional[AuditQuery] = None) -> List[AuditEvent]:
        """按条件查询审计记录 (先查内存缓冲, 若启用持久化则合并落盘记录)。"""
        q = query or AuditQuery()
        with self._lock:
            buffered = [ev for ev in self._buffer if q.match(ev)]
        # 合并持久化记录 (落盘的可能比缓冲更全, 但缓冲更新); seq 集合只建一次
        seen = {b.seq for b in buffered}
        if self.persist and self._path is not None and self._path.exists():
            try:
                with self._path.open("r", encoding="utf-8") as fh:
                    for line in fh:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            ev = AuditEvent.from_line(line)
                        except json.JSONDecodeError:
                            continue
                        if q.match(ev) and ev.seq not in seen:
                            seen.add(ev.seq)
                            buffered.append(ev)
            except OSError:
                pass
        buffered.sort(key=lambda e: e.ts)
        if q.limit and q.limit > 0:
            buffered = buffered[:q.limit]
        return buffered

    def recent(self, n: int = 20, types: Tuple[str, ...] = ()) -> List[AuditEvent]:
        q = AuditQuery(types=types) if types else AuditQuery()
        return self.query(q)[-n:]

    def _iter_disk_events(self) -> List[AuditEvent]:
        """读取全部落盘记录 (解析失败的行跳过)。"""
        out: List[AuditEvent] = []
        if self.persist and self._path is not None and self._path.exists():
            try:
                with self._path.open("r", encoding="utf-8") as fh:
                    for line in fh:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            out.append(AuditEvent.from_line(line))
                        except json.JSONDecodeError:
                            continue
            except OSError:
                pass
        return out

    def stats(self) -> Dict[str, Any]:
        """审计统计: 覆盖内存缓冲 + 落盘全量 (此前仅统计缓冲, 落盘后数字失真)。"""
        with self._lock:
            events = list(self._buffer)
            buffered_n = len(self._buffer)
        seen = {e.seq for e in events}
        for ev in self._iter_disk_events():
            if ev.seq not in seen:
                seen.add(ev.seq)
                events.append(ev)
        by_type: Dict[str, int] = {}
        for ev in events:
            by_type[ev.type] = by_type.get(ev.type, 0) + 1
        tss = sorted(e.ts for e in events)
        fmt_iso = lambda t: datetime.fromtimestamp(t, tz=timezone.utc).isoformat()
        return {
            "enabled": self.enabled,
            "persist": self.persist,
            "buffered": buffered_n,
            "total": len(events),
            "by_type": by_type,
            "oldest_iso": fmt_iso(tss[0]) if tss else None,
            "newest_iso": fmt_iso(tss[-1]) if tss else None,
        }

    def export(self, path, fmt: str = "jsonl",
               query: Optional[AuditQuery] = None) -> Dict[str, Any]:
        """导出审计记录到文件 (仅脱敏后 payload, 绝不含 raw 原文)。

        fmt: jsonl (默认, 一行一条) / json (数组) / csv (seq,ts,iso,type,payload_json)
        返回 {"path", "count", "fmt"}; 未知格式抛 ValueError。
        """
        fmt = (fmt or "").lower()
        if fmt not in ("jsonl", "json", "csv"):
            raise ValueError(f"不支持的导出格式: {fmt} (可选 jsonl/json/csv)")
        events = self.query(query)
        target = Path(path)
        if fmt == "csv":
            import csv
            with target.open("w", encoding="utf-8", newline="") as fh:
                writer = csv.writer(fh)
                writer.writerow(["seq", "ts", "iso", "type", "payload_json"])
                for ev in events:
                    iso = datetime.fromtimestamp(ev.ts, tz=timezone.utc).isoformat()
                    writer.writerow([ev.seq, ev.ts, iso, ev.type,
                                     json.dumps(ev.payload, ensure_ascii=False)])
        elif fmt == "json":
            records = []
            for ev in events:
                records.append({
                    "seq": ev.seq, "ts": ev.ts,
                    "iso": datetime.fromtimestamp(ev.ts, tz=timezone.utc).isoformat(),
                    "type": ev.type, "payload": ev.payload,
                })
            target.write_text(json.dumps(records, ensure_ascii=False, indent=2),
                              encoding="utf-8")
        else:
            with target.open("w", encoding="utf-8") as fh:
                for ev in events:
                    fh.write(ev.to_line() + "\n")
        return {"path": str(target), "count": len(events), "fmt": fmt}

    def clear_buffer(self) -> None:
        with self._lock:
            self._buffer.clear()

    def close(self) -> None:
        pass
