"""安全审计溯源器 (Security Auditor) —— 每条安全决策带完整 provenance, 审计日志 AEAD 加密落盘。

核心理念:
1. **决策链追溯**: 每条安全决策记录完整链路 —— 谁(来源模块)在什么条件下(输入上下文)
   做了什么决策(action/severity)、基于什么证据(reasons/features)、触发了什么后果。
2. **AEAD 加密落盘**: 审计日志用 AES-GCM 加密后写入磁盘, 防止篡改/泄露。
   密钥由 PBKDF2 从主密码派生, 每条日志独立 nonce, 支持追加写 (每条独立加密)。
3. **防篡改链**: 每条日志包含前一条的 HMAC (类似区块链), 任何篡改都会导致链断裂。
4. **可查询**: 内存中保留最近 N 条明文, 支持按类型/严重度/时间范围/来源查询。
5. **与 SecurityEventBus 集成**: 自动订阅总线事件, 写入审计日志。

与现有模块的关系:
- SecurityEventBus: 实时事件流 (内存 + 明文 JSONL), 本模块在其之上加 AEAD 加密 + provenance
- SecurityGate: 裁决入口, 本模块在其输出上加审计记录
- audit/store.py: 工具调用审计, 本模块是安全决策审计 (更高层级)

用法::

    auditor = SecurityAuditor(
        home=Path("~/.qingxiaotuan"),
        passphrase="user-master-password",  # 可选, 缺省用设备指纹
    )

    # 记录安全决策 (自动加密落盘)
    record = auditor.record(
        module="security_gate",
        action="deny",
        severity="critical",
        input_summary="rm -rf /tmp/demo",
        reasons=["命中致命红线 (文件系统/OS 级毁灭操作)"],
        context={"trust_level": "trusted", "yolo": False},
    )

    # 查询审计日志
    records = auditor.query(severity="critical", last_n=50)

    # 验证日志完整性 (防篡改)
    integrity = auditor.verify_integrity()
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import struct
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)


# ============================================================ 审计记录

@dataclass
class AuditRecord:
    """一条安全审计记录 —— 完整 provenance。"""
    seq: int                              # 全局递增序号
    ts: float                             # 时间戳
    module: str                           # 决策来源模块 (security_gate/safety_engine/classifier/...)
    action: str                           # allow / confirm / deny
    severity: str                         # none / low / medium / high / critical
    input_summary: str                    # 输入摘要 (命令/参数/内容, 截断到 500 字符)
    reasons: List[str] = field(default_factory=list)  # 决策理由
    context: Dict[str, Any] = field(default_factory=dict)  # 决策上下文 (trust_level/yolo/...)
    features: Dict[str, Any] = field(default_factory=dict)  # 分类器特征 (如有)
    session_id: str = ""                  # 所属会话
    agent_id: str = ""                    # Agent 标识
    prev_hash: str = ""                   # 前一条记录的 HMAC (防篡改链)
    record_hash: str = ""                 # 本条记录的 HMAC

    def to_dict(self) -> Dict[str, Any]:
        return {
            "seq": self.seq, "ts": self.ts, "module": self.module,
            "action": self.action, "severity": self.severity,
            "input_summary": self.input_summary, "reasons": self.reasons,
            "context": self.context, "features": self.features,
            "session_id": self.session_id, "agent_id": self.agent_id,
            "prev_hash": self.prev_hash, "record_hash": self.record_hash,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "AuditRecord":
        return cls(
            seq=d.get("seq", 0), ts=d.get("ts", 0.0),
            module=d.get("module", ""), action=d.get("action", ""),
            severity=d.get("severity", ""),
            input_summary=d.get("input_summary", ""),
            reasons=d.get("reasons", []),
            context=d.get("context", {}),
            features=d.get("features", {}),
            session_id=d.get("session_id", ""),
            agent_id=d.get("agent_id", ""),
            prev_hash=d.get("prev_hash", ""),
            record_hash=d.get("record_hash", ""),
        )

    def compute_hash(self, key: bytes) -> str:
        """计算本条记录的 HMAC (不含 record_hash 字段本身)。"""
        payload = {
            "seq": self.seq, "ts": self.ts, "module": self.module,
            "action": self.action, "severity": self.severity,
            "input_summary": self.input_summary, "reasons": self.reasons,
            "context": self.context, "features": self.features,
            "session_id": self.session_id, "agent_id": self.agent_id,
            "prev_hash": self.prev_hash,
        }
        data = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
        return hmac.new(key, data, hashlib.sha256).hexdigest()


# ============================================================ AEAD 加密层

# AEAD 加密层已下沉到 harden.crypto_provider 的可插拔 CryptoProvider
# (SoftwareProvider: AES-GCM 优先/CTR+HMAC 回退; GmsslProvider: 国密 SM4; HsmProvider: 硬件)。
# SecurityAuditor 通过 get_crypto_provider() 选后端, seal_raw/open_raw 与旧 _AeadCrypto 字节级兼容。


# =========================================================== SecurityAuditor

class SecurityAuditor:
    """安全审计溯源器 —— AEAD 加密审计日志 + 防篡改链 + 完整 provenance。"""

    # 内存中保留最近 N 条明文记录 (查询用)
    _IN_MEMORY_LIMIT = 2000

    def __init__(
        self,
        home: Optional[Path] = None,
        passphrase: Optional[str] = None,
        session_id: str = "",
        agent_id: str = "",
        crypto_provider: str = "software",
    ):
        self._home = Path(home) if home else Path.home() / ".qingxiaotuan"
        self._session_id = session_id
        self._agent_id = agent_id
        self._seq_counter = 0
        self._prev_hash = ""
        self._lock = threading.Lock()

        # 内存缓冲 (最近 N 条明文, 查询用)
        self._buffer: List[AuditRecord] = []

        # 密钥派生 (用于防篡改链的 HMAC, 与加密后端共用同一主密钥)
        self._crypto_key = self._derive_key(passphrase)

        # 可插拔加密后端: 默认 software (AES-GCM/CTR-HMAC); gmssl=国密 SM4; hsm=硬件(不适用流式日志时降级)
        # seal_raw/open_raw 与旧 _AeadCrypto 字节级兼容, 既有审计日志仍可读。
        self._provider = self._resolve_provider(crypto_provider)

        # 审计日志路径
        self._log_dir = self._home / "audit"
        self._log_dir.mkdir(parents=True, exist_ok=True)
        self._log_file = self._log_dir / "security_audit.aead"

        # 加载已有记录的序号和 prev_hash
        self._load_existing()

    @staticmethod
    def _resolve_provider(name: str):
        """解析加密后端; 依赖缺失或不适用时降级到 software 并明确告警 (绝不静默)。"""
        from ..harden.crypto_provider import get_crypto_provider
        try:
            return get_crypto_provider(name)
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "审计加密后端 %r 不可用 (%s); 降级到 software 后端。", name, exc
            )
            return get_crypto_provider("software")

    @property
    def algorithm(self) -> str:
        """当前加密后端名 + 算法, 如 'software/aes-gcm' / 'gmssl/sm4-ctr-hmac-sm3'。"""
        return f"{self._provider.name}/{self._provider.algorithm}"

    def _derive_key(self, passphrase: Optional[str]) -> bytes:
        """从密码派生加密密钥。"""
        if passphrase:
            salt = b"qxt-security-auditor-v1"
            return hashlib.pbkdf2_hmac("sha256", passphrase.encode(), salt, 100000)
        # 无密码时用设备指纹 (hostname + username + home 路径)
        fingerprint = f"{os.getlogin() if hasattr(os, 'getlogin') else 'unknown'}@{os.uname().nodename if hasattr(os, 'uname') else os.environ.get('COMPUTERNAME', 'unknown')}"
        return hashlib.pbkdf2_hmac("sha256", fingerprint.encode(), b"qxt-audit-device", 100000)

    def _load_existing(self):
        """加载已有审计日志, 恢复序号和 prev_hash。"""
        if not self._log_file.exists():
            return
        try:
            with open(self._log_file, "rb") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        raw = base64.b64decode(line)
                        plaintext = self._provider.open_raw(raw, self._crypto_key)
                        data = json.loads(plaintext)
                        rec = AuditRecord.from_dict(data)
                        if rec.seq > self._seq_counter:
                            self._seq_counter = rec.seq
                        self._prev_hash = rec.record_hash or ""
                        # 保留最近 N 条在内存
                        self._buffer.append(rec)
                        if len(self._buffer) > self._IN_MEMORY_LIMIT:
                            self._buffer = self._buffer[-self._IN_MEMORY_LIMIT:]
                    except Exception:
                        continue
        except Exception:
            pass

    # ---------------------------------------------------------- 写入

    def record(
        self,
        module: str,
        action: str,
        severity: str,
        input_summary: str,
        reasons: Optional[List[str]] = None,
        context: Optional[Dict[str, Any]] = None,
        features: Optional[Dict[str, Any]] = None,
    ) -> AuditRecord:
        """记录一条安全决策, AEAD 加密落盘, 返回记录。"""
        with self._lock:
            self._seq_counter += 1
            rec = AuditRecord(
                seq=self._seq_counter,
                ts=time.time(),
                module=module,
                action=action,
                severity=severity,
                input_summary=input_summary[:500],
                reasons=reasons or [],
                context=context or {},
                features=features or {},
                session_id=self._session_id,
                agent_id=self._agent_id,
                prev_hash=self._prev_hash,
            )

            # 计算本条 HMAC (用于下一条的 prev_hash)
            rec.record_hash = rec.compute_hash(self._crypto_key)
            self._prev_hash = rec.record_hash

            # 内存缓冲
            self._buffer.append(rec)
            if len(self._buffer) > self._IN_MEMORY_LIMIT:
                self._buffer = self._buffer[-self._IN_MEMORY_LIMIT:]

            # AEAD 加密落盘
            self._append_encrypted(rec)

            return rec

    def _append_encrypted(self, rec: AuditRecord):
        """AEAD 加密并追加写入审计日志。"""
        try:
            data = json.dumps(rec.to_dict(), ensure_ascii=False).encode()
            encrypted = self._provider.seal_raw(data, self._crypto_key)
            line = base64.b64encode(encrypted) + b"\n"
            with open(self._log_file, "ab") as f:
                f.write(line)
                f.flush()
                os.fsync(f.fileno())
        except Exception as exc:
            log.warning("审计日志写入失败: %s", exc)

    # ---------------------------------------------------------- 查询

    def query(
        self,
        *,
        module: Optional[str] = None,
        action: Optional[str] = None,
        severity: Optional[str] = None,
        since: Optional[float] = None,
        until: Optional[float] = None,
        last_n: int = 100,
    ) -> List[AuditRecord]:
        """查询审计记录 (从内存缓冲)。"""
        with self._lock:
            results = list(self._buffer)

        if module:
            results = [r for r in results if r.module == module]
        if action:
            results = [r for r in results if r.action == action]
        if severity:
            results = [r for r in results if r.severity == severity]
        if since:
            results = [r for r in results if r.ts >= since]
        if until:
            results = [r for r in results if r.ts <= until]

        return results[-last_n:]

    def count(self, *, severity: Optional[str] = None) -> int:
        """统计记录数。"""
        with self._lock:
            if severity:
                return sum(1 for r in self._buffer if r.severity == severity)
            return len(self._buffer)

    def stats(self) -> Dict[str, Any]:
        """审计统计。"""
        with self._lock:
            records = list(self._buffer)
        actions = {}
        severities = {}
        modules = {}
        for r in records:
            actions[r.action] = actions.get(r.action, 0) + 1
            severities[r.severity] = severities.get(r.severity, 0) + 1
            modules[r.module] = modules.get(r.module, 0) + 1
        return {
            "total": len(records),
            "by_action": actions,
            "by_severity": severities,
            "by_module": modules,
            "algorithm": self.algorithm,
            "log_file": str(self._log_file),
        }

    # ---------------------------------------------------------- 完整性验证

    def verify_integrity(self) -> Dict[str, Any]:
        """验证审计日志的防篡改链完整性。

        逐条校验 HMAC: 每条记录的 prev_hash 必须等于前一条的 record_hash,
        且每条记录的 record_hash 必须与其内容匹配。
        """
        with self._lock:
            records = list(self._buffer)

        if not records:
            return {"valid": True, "checked": 0, "message": "审计日志为空"}

        broken_at = -1
        for i, rec in enumerate(records):
            # 校验 prev_hash 链
            if i > 0 and rec.prev_hash != records[i - 1].record_hash:
                broken_at = i
                break
            # 校验本条 hash
            expected = rec.compute_hash(self._crypto_key)
            if rec.record_hash != expected:
                broken_at = i
                break

        if broken_at >= 0:
            return {
                "valid": False,
                "checked": broken_at + 1,
                "broken_at_seq": records[broken_at].seq,
                "message": f"防篡改链在第 {broken_at + 1} 条记录处断裂 (seq={records[broken_at].seq})",
            }

        return {
            "valid": True,
            "checked": len(records),
            "message": f"全部 {len(records)} 条记录完整性校验通过",
        }

    # ---------------------------------------------------------- 导出

    def export_markdown(self, last_n: int = 50) -> str:
        """导出为 Markdown 格式的审计报告。"""
        records = self.query(last_n=last_n)
        lines = [
            "# 安全审计报告",
            "",
            f"- 算法: {self.algorithm}",
            f"- 总记录数: {self.count()}",
            f"- 本报告包含最近 {len(records)} 条记录",
            "",
        ]

        # 按严重度分组
        by_severity: Dict[str, List[AuditRecord]] = {}
        for r in records:
            by_severity.setdefault(r.severity, []).append(r)

        for sev in ["critical", "high", "medium", "low", "none"]:
            recs = by_severity.get(sev, [])
            if not recs:
                continue
            lines.append(f"## {sev.upper()} ({len(recs)} 条)")
            lines.append("")
            for r in recs:
                ts_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(r.ts))
                lines.append(f"### [{ts_str}] {r.module} → {r.action}")
                lines.append(f"- 输入: `{r.input_summary[:200]}`")
                if r.reasons:
                    lines.append(f"- 理由: {'; '.join(r.reasons[:3])}")
                if r.context:
                    lines.append(f"- 上下文: {json.dumps(r.context, ensure_ascii=False)[:200]}")
                lines.append("")

        return "\n".join(lines)

    def export_json(self, last_n: int = 100) -> str:
        """导出为 JSON 格式。"""
        records = self.query(last_n=last_n)
        return json.dumps(
            {"stats": self.stats(), "records": [r.to_dict() for r in records]},
            ensure_ascii=False, indent=2,
        )

    # ---------------------------------------------------------- 与 SecurityEventBus 集成

    def subscribe_bus(self, bus) -> None:
        """订阅 SecurityEventBus, 自动将安全事件写入审计日志。"""
        def _on_security_event(event):
            # 映射 SecurityEvent → AuditRecord
            severity_map = {
                "critical": "critical", "high": "high",
                "medium": "medium", "low": "low", "info": "none",
            }
            action_map = {
                "security.command.blocked": "deny",
                "security.command.allowed": "allow",
                "security.command.confirmed": "confirm",
                "security.redline.hit": "deny",
                "security.redline.hard_hit": "deny",
                "security.network.blocked": "deny",
                "security.network.confirmed": "confirm",
                "security.mcp.injection": "deny",
                "security.classifier.deny": "deny",
                "security.classifier.confirm": "confirm",
                "security.sandbox.blocked": "deny",
                "security.sandbox.isolated": "isolate",
                "security.sandbox.confirmed": "confirm",
            }
            self.record(
                module=event.source or "security_bus",
                action=action_map.get(event.event_type, "allow"),
                severity=severity_map.get(event.severity, "none"),
                input_summary=str(event.payload.get("command", ""))[:500],
                reasons=[event.payload.get("reason", "")] if event.payload.get("reason") else [],
                context={"event_type": event.event_type, "bus_timestamp": event.timestamp},
            )

        bus.on("*", _on_security_event)
        log.debug("SecurityAuditor 已订阅 SecurityEventBus")
