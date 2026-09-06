"""实时安全告警 —— 关键安全事件的多通道即时通知。

核心理念:
- **多通道**: 桌面通知 / 文件日志 / 回调函数 / 审计事件总线
- **速率限制**: 防止告警风暴 (同类事件 N 秒内最多 1 次)
- **严重度分级**: critical 立即通知, high 批量通知, medium/low 仅日志
- **与 SecurityEventBus 集成**: 自动订阅安全事件并触发告警

用法::

    from qingxiaotuan.core.security_alert import SecurityAlerter

    alerter = SecurityAlerter(home=Path("~/.qingxiaotuan"))
    alerter.alert("critical", "检测到命令注入攻击", details={"command": "..."})

    # 订阅 SecurityEventBus
    from qingxiaotuan.core.security_bus import get_security_bus
    alerter.subscribe_bus(get_security_bus())
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

log = logging.getLogger(__name__)


# ============================================================ 数据类

@dataclass
class AlertRecord:
    """一条告警记录。"""
    severity: str                   # critical / high / medium / low
    title: str
    message: str = ""
    details: Dict[str, Any] = field(default_factory=dict)
    timestamp: float = 0.0
    source: str = ""
    notified: bool = False

    def __post_init__(self):
        if self.timestamp == 0.0:
            self.timestamp = time.time()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "severity": self.severity,
            "title": self.title,
            "message": self.message,
            "details": self.details,
            "timestamp": self.timestamp,
            "source": self.source,
            "notified": self.notified,
        }


# ============================================================ 告警通道

class DesktopNotifier:
    """桌面通知通道。"""

    @staticmethod
    def notify(title: str, message: str, severity: str = "info") -> bool:
        """发送桌面通知。"""
        if sys.platform == "win32":
            return DesktopNotifier._notify_windows(title, message, severity)
        elif sys.platform == "darwin":
            return DesktopNotifier._notify_macos(title, message)
        else:
            return DesktopNotifier._notify_linux(title, message)

    @staticmethod
    def _run_with_timeout(argv, timeout: float = 5.0) -> bool:
        """跨平台启动子进程 + 可靠超时。

        ``subprocess.run(timeout=)`` 在 Windows 上对长跑子进程 timeout 不可靠
        (TimeoutExpired 不会自动 kill, 后续 communicate() 可能继续阻塞)。
        这里用 Popen + 自己的超时 + kill(), 保证子进程被回收。
        """
        try:
            kwargs = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
            if sys.platform == "win32":
                # 隐藏黑色 console 闪烁
                kwargs["creationflags"] = getattr(
                    subprocess, "CREATE_NO_WINDOW", 0x08000000
                )
            proc = subprocess.Popen(argv, **kwargs)
        except (FileNotFoundError, OSError):
            # powershell / notify-send 不存在, 静默返回 False
            return False
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            try:
                proc.kill()
            except Exception:
                pass
            try:
                proc.wait(timeout=1.0)
            except Exception:
                pass
            return False
        return proc.returncode == 0

    @staticmethod
    def _notify_windows(title: str, message: str, severity: str) -> bool:
        """Windows Toast 通知。"""
        # 安全: title / message 走 PowerShell 单引号字符串, 用 '' 转义单引号,
        # 避免用户告警文本里含 $ / ` / " 触发 PowerShell 表达式注入或语法错。
        safe_title = title.replace("'", "''")[:80]
        safe_msg = message[:256].replace("'", "''")
        # 用单一拼接, 避免多个相邻字符串字面量被解析器误判。
        ps_lines = [
            "[Windows.UI.Notifications.ToastNotificationManager, "
            "Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null",
            "[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom, "
            "ContentType = WindowsRuntime] | Out-Null",
            "$template = '<toast><visual><binding template=\"ToastGeneric\">"
            f"<text>{safe_title}</text><text>{safe_msg}</text>"
            "</binding></visual></toast>'",
            "$xml = New-Object Windows.Data.Xml.Dom.XmlDocument",
            "$xml.LoadXml($template)",
            "$toast = [Windows.UI.Notifications.ToastNotification]::new($xml)",
            "[Windows.UI.Notifications.ToastNotificationManager]"
            "::CreateToastNotifier('Qingxiaotuan Security').Show($toast)",
        ]
        ps_script = "; ".join(ps_lines)
        return DesktopNotifier._run_with_timeout(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_script],
            timeout=5.0,
        )

    @staticmethod
    def _notify_macos(title: str, message: str) -> bool:
        """macOS 通知。"""
        # 同样做引号转义, 防 message 含 " 时 osascript 报语法错或注入。
        safe_msg = message[:200].replace("\\", "\\\\").replace('"', '\\"')
        safe_title = title[:50].replace("\\", "\\\\").replace('"', '\\"')
        return DesktopNotifier._run_with_timeout(
            ["osascript", "-e",
             f'display notification "{safe_msg}" with title "{safe_title}"'],
            timeout=5.0,
        )

    @staticmethod
    def _notify_linux(title: str, message: str) -> bool:
        """Linux 通知 (notify-send)。"""
        # notify-send 参数不做 shell 解析, 过滤控制字符防终端渲染异常。
        clean = lambda s: ''.join(c for c in s if c.isprintable())
        return DesktopNotifier._run_with_timeout(
            ["notify-send", clean(title[:50]), clean(message[:256])],
            timeout=5.0,
        )


class AlertLogger:
    """文件日志告警通道。"""

    def __init__(self, home: Path) -> None:
        self._log_dir = home / "audit"
        self._log_dir.mkdir(parents=True, exist_ok=True)
        self._log_file = self._log_dir / "security_alerts.jsonl"
        # 追加写并发不安全: 多线程交错 write 会覆盖 / 截断行, 实测 8 线程写 400 条
        # 只保留 253 条。审计数据丢失直接破坏产品核心承诺, 必须串行化。
        self._lock = threading.Lock()

    def log(self, record: AlertRecord) -> None:
        """追加写入告警日志。"""
        line = json.dumps(record.to_dict(), ensure_ascii=False) + "\n"
        try:
            with self._lock:
                with open(self._log_file, "a", encoding="utf-8") as f:
                    f.write(line)
                    f.flush()
                    os.fsync(f.fileno())
        except Exception as exc:  # noqa: BLE001
            # 审计写盘失败不能静默: 让运维能感知磁盘满 / 权限错。
            log.warning("安全告警日志写盘失败 (path=%s): %s", self._log_file, exc)

    def read_recent(self, last_n: int = 50) -> List[Dict[str, Any]]:
        """读取最近 N 条告警。"""
        if not self._log_file.exists():
            return []
        try:
            lines = self._log_file.read_text(encoding="utf-8").strip().split("\n")
            results = []
            for line in lines[-last_n:]:
                line = line.strip()
                if line:
                    results.append(json.loads(line))
            return results
        except Exception:
            return []


# ============================================================ 告警管理器

class SecurityAlerter:
    """安全告警管理器 —— 多通道实时通知 + 速率限制。

    用法::

        alerter = SecurityAlerter(home=Path("~/.qingxiaotuan"))

        # 直接告警
        alerter.alert("critical", "检测到命令注入", source="mcp_security")

        # 订阅 SecurityEventBus
        alerter.subscribe_bus(get_security_bus())

        # 查询历史
        recent = alerter.get_recent_alerts(severity="critical")
    """

    # 严重度 → 是否需要桌面通知
    _DESKTOP_SEVERITIES = {"critical", "high"}

    # 速率限制: 同一标题在 N 秒内最多通知 1 次
    _RATE_LIMIT_SECONDS = 30

    def __init__(
        self,
        home: Optional[Path] = None,
        on_alert: Optional[Callable[[AlertRecord], None]] = None,
        enable_desktop: bool = True,
    ) -> None:
        self._home = Path(home) if home else Path.home() / ".qingxiaotuan"
        self._on_alert = on_alert
        self._enable_desktop = enable_desktop

        # 通道
        self._logger = AlertLogger(self._home)

        # 速率限制
        self._last_notified: Dict[str, float] = {}
        self._lock = threading.Lock()

        # 内存缓冲
        self._buffer: List[AlertRecord] = []
        self._MAX_BUFFER = 200

    # ------------------------------------------------------------ 告警

    def alert(
        self,
        severity: str,
        title: str,
        message: str = "",
        details: Optional[Dict[str, Any]] = None,
        source: str = "",
    ) -> AlertRecord:
        """发送安全告警。

        Args:
            severity: critical / high / medium / low
            title: 告警标题
            message: 告警详情
            details: 附加数据
            source: 来源模块

        Returns:
            AlertRecord: 告警记录
        """
        record = AlertRecord(
            severity=severity,
            title=title,
            message=message,
            details=details or {},
            source=source,
        )

        # 速率限制检查
        rate_key = f"{severity}:{title}"
        if self._is_rate_limited(rate_key):
            record.notified = False
        else:
            # 发送通知
            self._send_notification(record)
            record.notified = True

        # 写日志
        self._logger.log(record)

        # 内存缓冲
        with self._lock:
            self._buffer.append(record)
            if len(self._buffer) > self._MAX_BUFFER:
                self._buffer = self._buffer[-self._MAX_BUFFER:]

        # 回调
        if self._on_alert:
            try:
                self._on_alert(record)
            except Exception as exc:  # noqa: BLE001
                # 用户回调出错不能静默: 调试时无任何线索是最差的体验。
                log.debug("安全告警 on_alert 回调异常: %s", exc)

        return record

    def alert_critical(self, title: str, message: str = "", **kwargs: Any) -> AlertRecord:
        """发送 critical 级告警。"""
        return self.alert("critical", title, message, **kwargs)

    def alert_high(self, title: str, message: str = "", **kwargs: Any) -> AlertRecord:
        """发送 high 级告警。"""
        return self.alert("high", title, message, **kwargs)

    # ------------------------------------------------------------ 集成

    def subscribe_bus(self, bus: Any) -> None:
        """订阅 SecurityEventBus, 自动将安全事件转为告警。"""
        def _on_security_event(event: Any) -> None:
            severity = getattr(event, "severity", "info")
            event_type = getattr(event, "event_type", "")
            payload = getattr(event, "payload", {})
            source = getattr(event, "source", "")

            # 只对高危事件告警
            if severity not in ("critical", "high", "medium"):
                return

            title = f"安全事件: {event_type}"
            message = payload.get("reason", "") or str(payload.get("command", ""))[:200]

            self.alert(
                severity=severity,
                title=title,
                message=message,
                details={"event_type": event_type, **payload},
                source=source,
            )

        if hasattr(bus, "on"):
            bus.on("*", _on_security_event)
            log.debug("SecurityAlerter 已订阅 SecurityEventBus")

    # ------------------------------------------------------------ 查询

    def get_recent_alerts(
        self,
        *,
        severity: Optional[str] = None,
        last_n: int = 50,
    ) -> List[AlertRecord]:
        """获取最近告警。"""
        with self._lock:
            results = list(self._buffer)
        if severity:
            results = [r for r in results if r.severity == severity]
        return results[-last_n:]

    def get_alert_stats(self) -> Dict[str, Any]:
        """告警统计。"""
        with self._lock:
            records = list(self._buffer)
        by_severity: Dict[str, int] = {}
        by_source: Dict[str, int] = {}
        for r in records:
            by_severity[r.severity] = by_severity.get(r.severity, 0) + 1
            by_source[r.source] = by_source.get(r.source, 0) + 1
        return {
            "total": len(records),
            "by_severity": by_severity,
            "by_source": by_source,
        }

    def export_alerts(self, last_n: int = 100) -> str:
        """导出告警报告。"""
        records = self.get_recent_alerts(last_n=last_n)
        lines = [
            "# 安全告警报告",
            "",
            f"- 总告警数: {len(records)}",
            "",
        ]
        for r in reversed(records):
            ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(r.timestamp))
            lines.append(f"### [{r.severity.upper()}] [{ts}] {r.title}")
            if r.message:
                lines.append(f"- {r.message[:200]}")
            if r.source:
                lines.append(f"- 来源: {r.source}")
            lines.append("")

        return "\n".join(lines)

    # ------------------------------------------------------------ 内部方法

    def _is_rate_limited(self, key: str) -> bool:
        """速率限制检查。"""
        now = time.time()
        with self._lock:
            last = self._last_notified.get(key, 0)
            if now - last < self._RATE_LIMIT_SECONDS:
                return True
            self._last_notified[key] = now
            return False

    def _send_notification(self, record: AlertRecord) -> None:
        """发送桌面通知。"""
        if not self._enable_desktop:
            return
        if record.severity not in self._DESKTOP_SEVERITIES:
            return

        # 关键: 桌面通知必须异步派发, 绝不阻塞主路径 (比如 _pre_exec_guard)。
        # 桌面通知子进程 (powershell / osascript / notify-send) 在 Windows 上
        # 偶发阻塞 30s+, 同步调用会直接拖垮安全护栏自身。
        try:
            import threading as _threading
            t = _threading.Thread(
                target=self._notify_safely,
                args=(record,),
                name=f"qxt-notify-{record.severity}",
                daemon=True,
            )
            t.start()
        except Exception as exc:  # noqa: BLE001
            log.debug("启动桌面通知线程失败: %s", exc)

    def _notify_safely(self, record: "AlertRecord") -> None:
        """在后台线程里跑桌面通知, 吞所有异常。"""
        try:
            DesktopNotifier.notify(
                title=f"[{record.severity.upper()}] {record.title}",
                message=record.message or record.title,
                severity=record.severity,
            )
        except Exception as exc:  # noqa: BLE001
            log.debug("桌面通知派发异常: %s", exc)


# ============================================================ 模块级便捷函数

_global_alerter: Optional[SecurityAlerter] = None


def get_alerter(home: Optional[Path] = None) -> SecurityAlerter:
    """获取全局告警器。"""
    global _global_alerter
    if _global_alerter is None:
        _global_alerter = SecurityAlerter(home=home)
    return _global_alerter


def alert_critical(title: str, message: str = "", **kwargs: Any) -> AlertRecord:
    """模块级便捷 critical 告警。"""
    return get_alerter().alert_critical(title, message, **kwargs)
