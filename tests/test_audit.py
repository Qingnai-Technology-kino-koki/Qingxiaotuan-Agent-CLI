"""审计日志后端测试。"""

from __future__ import annotations

import json
from pathlib import Path

from qingxiaotuan.audit.store import (
    AuditStore, AuditQuery, redact, _redact_text,
)
from qingxiaotuan.audit.plugin import AuditPlugin
from qingxiaotuan.core.kernel import Kernel


# ---------------------------------------------------------------- 脱敏

def test_redact_masks_api_key():
    out = redact({"api_key": "sk-1234567890abcdef", "model": "deepseek"})
    assert out["api_key"] == "***"
    assert out["model"] == "deepseek"


def test_redact_masks_nested_and_lists():
    out = redact({
        "auth": {"token": "abc123secret"},
        "items": [{"password": "hunter2"}, {"name": "ok"}],
    })
    assert out["auth"]["token"] == "***"
    assert out["items"][0]["password"] == "***"
    assert out["items"][1]["name"] == "ok"


def test_redact_text_catches_bearer_and_sk():
    txt = "call with Bearer eyJhbGciOiJIUzI1NiIs and sk-ABCdefGHIjklMNO"
    red = _redact_text(txt)
    assert "eyJhbGci" not in red
    assert "sk-ABCdefGHI" not in red
    assert "***" in red


# ---------------------------------------------------------------- 落盘

def test_log_persists_and_excludes_raw(tmp_path):
    store = AuditStore(home=tmp_path, enabled=True, persist=True)
    store.log("tool.executed", {"name": "run_shell", "api_key": "sk-secret", "status": "ok"})
    store.close()
    log_file = tmp_path / "audit" / "audit.log"
    assert log_file.exists()
    line = log_file.read_text(encoding="utf-8").strip().splitlines()[-1]
    rec = json.loads(line)
    # 落盘内容必须已脱敏, 明文密钥绝不出现
    assert "sk-secret" not in line
    assert rec["payload"]["api_key"] == "***"
    assert rec["payload"]["name"] == "run_shell"
    assert rec["type"] == "tool.executed"


def test_raw_not_leaked_in_memory(tmp_path):
    store = AuditStore(home=tmp_path, enabled=True, persist=True)
    store.log("model.request", {"model": "gpt-4o", "api_key": "sk-raw"})
    ev = store.recent(1)[0]
    # 内存 raw 保留原文 (供调试), 但 to_line 落盘只用 payload (脱敏)
    assert ev.raw.get("api_key") == "sk-raw"
    assert "sk-raw" not in ev.to_line()


# ---------------------------------------------------------------- 查询

def test_query_filters_by_type(tmp_path):
    store = AuditStore(home=tmp_path, enabled=True, persist=True)
    store.log("tool.executed", {"name": "a"})
    store.log("model.response", {"model": "x"})
    store.log("tool.executed", {"name": "b"})
    by_tool = store.query(AuditQuery(types=("tool.executed",)))
    assert len(by_tool) == 2
    assert all(e.type == "tool.executed" for e in by_tool)


def test_query_filters_by_keyword(tmp_path):
    store = AuditStore(home=tmp_path, enabled=True, persist=True)
    store.log("task.started", {"job_id": "j-1", "task": "build"})
    store.log("task.started", {"job_id": "j-2", "task": "deploy"})
    hits = store.query(AuditQuery(keyword="deploy"))
    assert len(hits) == 1
    assert hits[0].payload["job_id"] == "j-2"


# ---------------------------------------------------------------- 插件接入

def test_audit_plugin_subscribes_and_logs(tmp_path):
    kernel = Kernel()
    # 提供一个临时 home 的 config 桩
    class _Cfg:
        home = tmp_path / "home"
        mode = "standard"
        def get(self, k, d=None):
            if k == "audit.enabled":
                return True
            if k == "audit.persist":
                return True
            return d
    kernel.provide("config", _Cfg())
    plugin = AuditPlugin()
    plugin.activate(kernel)
    # 通配订阅: 任意 emit 都应被审计捕获
    kernel.emit("tool.executed", {"name": "run_shell", "status": "ok", "token": "sk-x"})
    events = plugin.store.recent()
    assert any(e.type == "tool.executed" for e in events)
    # 敏感字段已脱敏
    ev = next(e for e in events if e.type == "tool.executed")
    assert ev.payload.get("token") == "***"
    # 跳过类型不被记录
    assert not any(e.type == "turn.step" for e in plugin.store.recent(50))
