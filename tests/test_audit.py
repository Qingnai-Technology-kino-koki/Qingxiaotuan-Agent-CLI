"""审计日志后端测试。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

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


# ---------------------------------------------------------------- 去重 / limit / stats

def test_query_dedupes_buffer_and_disk(tmp_path):
    """回归: 缓冲与落盘合并时同一 seq 只计一次, 不翻倍。"""
    store = AuditStore(home=tmp_path, enabled=True, persist=True)
    for i in range(3):
        store.log("tool.executed", {"n": i})
    # 同一实例: 缓冲 3 条 + 磁盘 3 条, 必须恰好 3 条
    assert len(store.query()) == 3
    store.close()
    # 新实例 (空缓冲) 从磁盘读也恰好各一次
    fresh = AuditStore(home=tmp_path, enabled=True, persist=True)
    again = fresh.query()
    assert len(again) == 3
    assert [e.seq for e in again] == sorted(e.seq for e in again)
    assert len({e.seq for e in again}) == 3


def test_query_limit_returns_earliest(tmp_path):
    store = AuditStore(home=tmp_path, enabled=True, persist=True)
    for i in range(3):
        store.log("tool.executed", {"n": i})
    hits = store.query(AuditQuery(limit=2))
    assert len(hits) == 2
    # limit 取最早的前 N 条
    assert [e.payload["n"] for e in hits] == [0, 1]


def test_stats_counts_disk_after_clear_buffer(tmp_path):
    """stats 必须算上落盘记录: 清掉内存缓冲后 total 不应归零。"""
    store = AuditStore(home=tmp_path, enabled=True, persist=True)
    store.log("model.request", {"model": "a"})
    store.log("tool.executed", {"name": "b"})
    store.clear_buffer()
    st = store.stats()
    assert st["buffered"] == 0
    assert st["total"] == 2
    assert st["by_type"] == {"model.request": 1, "tool.executed": 1}
    assert st["oldest_iso"] and st["newest_iso"]
    assert st["oldest_iso"] <= st["newest_iso"]


# ---------------------------------------------------------------- 导出

def test_export_three_formats_redacted(tmp_path):
    store = AuditStore(home=tmp_path, enabled=True, persist=True)
    store.log("tool.executed", {"name": "run_shell", "api_key": "sk-export-secret"})
    q = AuditQuery()
    jl = tmp_path / "out.jsonl"
    js = tmp_path / "out.json"
    cs = tmp_path / "out.csv"
    r1 = store.export(jl, fmt="jsonl", query=q)
    r2 = store.export(js, fmt="json", query=q)
    r3 = store.export(cs, fmt="csv", query=q)
    assert (r1["count"], r1["fmt"]) == (1, "jsonl")
    assert (r2["count"], r2["fmt"]) == (1, "json")
    assert (r3["count"], r3["fmt"]) == (1, "csv")

    lines = jl.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == r1["count"]
    arr = json.loads(js.read_text(encoding="utf-8"))
    assert isinstance(arr, list) and len(arr) == 1
    assert arr[0]["payload"]["api_key"] == "***"
    header = cs.read_text(encoding="utf-8").splitlines()[0]
    assert header == "seq,ts,iso,type,payload_json"
    # 三种格式全程无明文密钥
    for f in (jl, js, cs):
        content = f.read_text(encoding="utf-8")
        assert "sk-export-secret" not in content


def test_export_unknown_fmt_raises(tmp_path):
    store = AuditStore(home=tmp_path, enabled=True, persist=True)
    with pytest.raises(ValueError):
        store.export(tmp_path / "out.xml", fmt="xml")
