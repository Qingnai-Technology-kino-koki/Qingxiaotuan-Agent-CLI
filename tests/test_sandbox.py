"""进程级沙箱隔离测试 (真实子进程, 无需 API Key, 用 fake worker)。

验证:
- prepare_sandbox: 复制工作区到沙箱, 子进程在沙箱里的写操作不回写源目录;
- run_in_sandbox: 经真实子进程 + JSON 协议往返, 拿到 SubResult, 沙箱被清理;
- 超时: fake worker 故意 sleep 超过 timeout, 主进程返回超时失败而非卡死;
- SubAgentPool(isolation="process") 复用沙箱路径 (用 fake worker 注入)。
"""

import json
import sys
import textwrap
from pathlib import Path

import pytest

from qingxiaotuan.core.sandbox import prepare_sandbox, run_in_sandbox
from qingxiaotuan.core.subagents import SubAgentPool, SubTask, make_tasks


def _write_fake_worker(tmp_path: Path, sleep: float = 0.0, fail: bool = False) -> str:
    """写一个 fake sandbox worker 脚本: 不调模型, 直接写约定 JSON, 并在沙箱留痕。"""
    script = textwrap.dedent(f"""
        import json, sys, os
        req = json.loads(open(sys.argv[1], encoding='utf-8').read())
        sandbox = req['workspace']
        # 在沙箱里写一个标记文件, 证明 worker 确实跑在沙箱内
        with open(os.path.join(sandbox, '.sandbox_ran'), 'w', encoding='utf-8') as f:
            f.write('ran')
        if {sleep}:
            import time; time.sleep({sleep})
        result = {{
            "ok": {not fail},
            "output": "fake-output:" + req['task'][:20],
            "turns": 1,
            "error": "fake-fail" if {fail} else None,
            "tool_calls": []
        }}
        open(sys.argv[2], 'w', encoding='utf-8').write(json.dumps(result, ensure_ascii=False))
        sys.exit(0)
    """)
    path = tmp_path / "fake_worker.py"
    path.write_text(script, encoding="utf-8")
    return str(path)


def test_prepare_sandbox_isolates_writes(tmp_path):
    """沙箱是副本: 子进程在沙箱写文件, 源工作区不受影响。"""
    # 源工作区放一个文件
    (tmp_path / "src.txt").write_text("source", encoding="utf-8")
    sandbox = prepare_sandbox(str(tmp_path), "T1")
    # 模拟子进程在沙箱里新增/改写
    (sandbox / "src.txt").write_text("tampered", encoding="utf-8")
    (sandbox / "new.txt").write_text("new", encoding="utf-8")
    # 源工作区必须原封不动
    assert (tmp_path / "src.txt").read_text(encoding="utf-8") == "source"
    assert not (tmp_path / "new.txt").exists()
    # 沙箱里确有改动
    assert (sandbox / "new.txt").read_text(encoding="utf-8") == "new"


def test_run_in_sandbox_roundtrip(tmp_path):
    """真实子进程 + JSON 协议: 拿到 SubResult, 沙箱被清理。"""
    worker = _write_fake_worker(tmp_path)
    task = SubTask(task_id="T1", prompt="调研一下天气")
    result = run_in_sandbox(task, workspace=str(tmp_path), worker_module=worker, timeout=30)
    assert result.ok
    assert result.output.startswith("fake-output:")
    assert result.turns == 1
    # 沙箱应被清理 (目录不存在)
    assert not any(p.name.startswith("qxt-sandbox-") for p in Path(tmp_path).iterdir())


def test_run_in_sandbox_failure_propagates(tmp_path):
    """worker 返回 ok=False, 主进程拿到失败结果。"""
    worker = _write_fake_worker(tmp_path, fail=True)
    task = SubTask(task_id="T1", prompt="会失败的任务")
    result = run_in_sandbox(task, workspace=str(tmp_path), worker_module=worker, timeout=30)
    assert not result.ok
    assert result.error == "fake-fail"


def test_run_in_sandbox_timeout(tmp_path):
    """worker 故意睡超 timeout: 主进程返回超时失败, 不卡死。"""
    worker = _write_fake_worker(tmp_path, sleep=5)
    task = SubTask(task_id="T1", prompt="慢任务")
    import time
    start = time.time()
    result = run_in_sandbox(task, workspace=str(tmp_path), worker_module=worker, timeout=1.0)
    elapsed = time.time() - start
    # 不应等满 5s (留给 Windows 下 kill+清理一些开销, 放宽到 8s)
    assert elapsed < 8.0, "不应等满 5s 的子进程睡眠"
    assert not result.ok
    assert "超时" in (result.error or "")


def test_pool_process_isolation_uses_sandbox(tmp_path):
    """SubAgentPool(isolation=process) 经 fake worker 跑通, 结果汇总正确。"""
    worker = _write_fake_worker(tmp_path)
    kernel = __import__("qingxiaotuan.app", fromlist=["build_kernel"]).build_kernel()
    config = kernel.require("config")
    pool = SubAgentPool(
        kernel=kernel, config=config, workspace=str(tmp_path),
        isolation="process", worker_module=worker, default_timeout=30,
    )
    results = pool.dispatch(make_tasks(["任务A", "任务B"]), stream=False)
    assert len(results) == 2
    assert all(r.ok for r in results)
    assert results[0].task_id == "T1" and results[1].task_id == "T2"
