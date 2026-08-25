"""M1: detached worker 集成测试
验证独立 worker 进程的生命周期、跨命令查询、取消等核心能力。
"""
import os
import sys
import time
import tempfile
import json
from pathlib import Path

import pytest

from qingxiaotuan.core.background_store import BackgroundStore, kill_process_tree


def test_background_store_create_write_read(tmp_path):
    """测试创建、写入、读取 manifest"""
    store = BackgroundStore(tmp_path)
    data = store.create("bg-test-001", "测试任务", str(tmp_path), "default")
    assert data["job_id"] == "bg-test-001"
    assert data["status"] == "queued"

    # 更新状态
    updated = store.update("bg-test-001", status="running", pid=12345)
    assert updated["status"] == "running"
    assert updated["pid"] == 12345

    # 重新读取
    stored = store.get("bg-test-001")
    assert stored["job_id"] == "bg-test-001"
    assert stored["status"] == "running"


def test_background_store_list_sorted(tmp_path):
    """测试列表排序 (按 started_at 倒序)"""
    store = BackgroundStore(tmp_path)
    store.create("bg-test-001", "任务1", str(tmp_path))
    time.sleep(0.01)
    store.create("bg-test-002", "任务2", str(tmp_path))

    jobs = store.list()
    assert len(jobs) == 2
    assert jobs[0]["job_id"] == "bg-test-002"  # 最新的在前


def test_background_store_reconcile_stale(tmp_path):
    """测试心跳超时处理"""
    store = BackgroundStore(tmp_path)
    data = store.create("bg-test-stale", "超时任务", str(tmp_path))
    # 模拟 worker 已启动但心跳超时
    store.update("bg-test-stale", status="running", pid=None, heartbeat=time.time() - 200)

    changed = store.reconcile_stale(timeout=90.0)
    assert changed >= 1
    stored = store.get("bg-test-stale")
    assert stored["status"] == "failed"


def test_background_store_recoverable(tmp_path):
    """测试 queued 且无进程的可恢复任务"""
    store = BackgroundStore(tmp_path)
    store.create("bg-test-recover", "可恢复任务", str(tmp_path))
    # 无 pid 的 queued 任务应该可恢复
    recoverable = store.recoverable(timeout=30.0)
    assert "bg-test-recover" in recoverable


def test_kill_process_tree_windows(tmp_path):
    """Windows 进程树终止测试 (模拟)"""
    if os.name != "nt":
        pytest.skip("Windows only")
    # 用不存在的 pid 测试: 应该返回 True (已不存在视为终止)
    result = kill_process_tree(999999)
    assert result is True


def test_background_store_atomic_write(tmp_path):
    """测试原子写入: 不会留下半写入文件"""
    store = BackgroundStore(tmp_path)
    store.create("bg-test-atomic", "原子任务", str(tmp_path))
    path = store.path("bg-test-atomic")
    content = path.read_text(encoding="utf-8")
    data = json.loads(content)  # 应该是完整合法的 JSON
    assert data["job_id"] == "bg-test-atomic"

    # 确认没有临时文件残留
    temp_files = list(path.parent.glob("*.tmp"))
    assert len(temp_files) == 0
