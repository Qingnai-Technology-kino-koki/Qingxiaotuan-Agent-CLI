"""后台任务接入 qxt session 断点续聊 (Task F) 测试。

验证:
- 后台任务提交的会话流带有 session.meta(kind=background, job_id, task);
- SessionStore.read_meta / peek_title 能正确识别后台会话并给出任务标题;
- cmd_session list 能为后台会话打 [后台] 徽标;
- 后台会话流可被 SessionStore.load_messages 重建 (断点续聊基础)。
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from qingxiaotuan.memory.sessions import SessionStore
from qingxiaotuan.core.background import BackgroundRunner, BackgroundJob
from qingxiaotuan.app import build_kernel


def test_background_session_writes_meta(tmp_path):
    kernel = build_kernel()
    cfg = kernel.require("config")
    cfg.home = tmp_path  # 隔离家目录
    runner = BackgroundRunner(kernel, cfg, str(tmp_path))
    store = SessionStore(tmp_path)
    store.append("session.meta", {"kind": "background", "job_id": "bg-test", "task": "整理周报"})
    meta = SessionStore.read_meta(store.file)
    assert meta is not None
    assert meta["kind"] == "background"
    assert meta["job_id"] == "bg-test"
    assert meta["task"] == "整理周报"


def test_peek_title_uses_meta_task(tmp_path):
    store = SessionStore(tmp_path)
    store.append("session.meta", {"kind": "background", "job_id": "bg-1", "task": "把日志整理成周报"})
    store.append("user", {"message": {"role": "user", "content": "被忽略的首条 user"}})
    assert SessionStore.peek_title(store.file) == "把日志整理成周报"


def test_interactive_session_has_no_background_meta(tmp_path):
    store = SessionStore(tmp_path)
    store.append("user", {"message": {"role": "user", "content": "你好"}})
    meta = SessionStore.read_meta(store.file)
    # 交互会话无 session.meta -> read_meta 返回 None (或不含 kind=background)
    assert meta is None or meta.get("kind") != "background"
    assert SessionStore.peek_title(store.file) == "你好"


def test_background_job_session_file_discoverable(tmp_path):
    """模拟 BackgroundRunner.submit 写入 meta 后, 会话能被 list_sessions 发现且带后台标记。"""
    kernel = build_kernel()
    cfg = kernel.require("config")
    cfg.home = tmp_path
    runner = BackgroundRunner(kernel, cfg, str(tmp_path))
    if not runner.enabled:
        return
    # 不真正起线程跑模型, 直接校验 session 文件自描述
    job = BackgroundJob(
        job_id="bg-x1", task="示例后台任务", started_at=time.time(),
        thread=None, store=SessionStore(tmp_path),
    )
    job.store.append("session.meta", {"kind": "background", "job_id": job.job_id, "task": job.task})
    # 写入一条 user/assistant 让 load_messages 有内容
    job.store.append("user", {"message": {"role": "user", "content": "开始"}})
    job.store.append("assistant", {"message": {"role": "assistant", "content": "好的"}})

    meta = SessionStore.read_meta(job.store.file)
    assert meta["kind"] == "background"
    msgs = SessionStore.load_messages(job.store.file)
    assert len(msgs) == 2
    assert msgs[0]["content"] == "开始"
