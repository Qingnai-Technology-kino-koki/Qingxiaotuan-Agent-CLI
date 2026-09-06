"""后台 detached worker 启动校验 + submit_background 线程回退策略测试。

回归点:
- submit_detached 必须校验 worker 进程真的活了起来; 若进程瞬间退出 (import 失败等),
  manifest 应被标记为 failed 并抛错, 而非停在 running 让任务永远跑不完。
- submit_background 不能静默把所有 detached 失败吞掉、降级为会随 CLI 退出被杀的
  进程内 daemon 线程; 仅当显式允许 background.allow_thread_fallback 时才回退。
"""

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from qingxiaotuan.core.background import BackgroundRunner, submit_background
from qingxiaotuan.app import build_kernel


class _FakeProc:
    def __init__(self, pid):
        self.pid = pid
        self.returncode = None


def _disable_background_if_needed(cfg):
    # 确保测试在不依赖真实后台开关的情况下也能跑
    cfg.data.setdefault("background", {})
    cfg.data["background"]["enabled"] = True


def test_submit_detached_marks_failed_when_worker_dies(tmp_path, monkeypatch):
    kernel = build_kernel()
    cfg = kernel.require("config")
    cfg.home = tmp_path
    _disable_background_if_needed(cfg)
    runner = BackgroundRunner(kernel, cfg, str(tmp_path))

    # 模拟 Popen: 进程对象已创建, 但随后立即死亡 (如 background_worker import 失败)
    monkeypatch.setattr("qingxiaotuan.core.background.subprocess.Popen",
                        lambda *a, **k: _FakeProc(12345))
    monkeypatch.setattr("qingxiaotuan.core.background._is_process_alive",
                        lambda pid: False)

    with pytest.raises(RuntimeError):
        runner.submit_detached("做点事")

    failed = [d for d in runner._store.list() if d.get("status") == "failed"]
    assert failed, "应有至少一条 failed 记录 (不应停在 running)"
    assert "worker" in (failed[-1].get("error") or "")


def test_submit_detached_ok_when_worker_alive(tmp_path, monkeypatch):
    kernel = build_kernel()
    cfg = kernel.require("config")
    cfg.home = tmp_path
    _disable_background_if_needed(cfg)
    runner = BackgroundRunner(kernel, cfg, str(tmp_path))

    monkeypatch.setattr("qingxiaotuan.core.background.subprocess.Popen",
                        lambda *a, **k: _FakeProc(54321))
    monkeypatch.setattr("qingxiaotuan.core.background._is_process_alive",
                        lambda pid: True)

    data = runner.submit_detached("做点事")
    assert data["status"] == "running"
    assert data["pid"] == 54321


def _raise_detached(self, task, *, yolo=False):
    raise RuntimeError("detached unavailable")


def test_submit_background_reraises_when_detached_unavailable(tmp_path, monkeypatch):
    kernel = build_kernel()
    cfg = kernel.require("config")
    cfg.home = tmp_path
    _disable_background_if_needed(cfg)
    monkeypatch.setattr(BackgroundRunner, "submit_detached", _raise_detached)

    with pytest.raises(RuntimeError):
        submit_background(kernel, object(), "做点事", str(tmp_path))


def test_submit_background_thread_fallback_only_when_allowed(tmp_path, monkeypatch):
    kernel = build_kernel()
    cfg = kernel.require("config")
    cfg.home = tmp_path
    _disable_background_if_needed(cfg)
    # 显式允许线程回退 (测试/嵌入环境)
    cfg.data.setdefault("background", {})["allow_thread_fallback"] = True

    calls = {}

    def fake_submit(self, task, **kw):
        calls["called"] = True
        return SimpleNamespace(job_id="bg-thread")

    monkeypatch.setattr(BackgroundRunner, "submit_detached", _raise_detached)
    monkeypatch.setattr(BackgroundRunner, "submit", fake_submit)

    jid = submit_background(kernel, object(), "做点事", str(tmp_path))
    assert jid == "bg-thread"
    assert calls.get("called") is True
