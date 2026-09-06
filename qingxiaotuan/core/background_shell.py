"""后台 Shell (`! command`) —— 对标 Claude Code 的异步 Bash。

在 ``run_shell`` 命令前加 ``!`` 前缀即可**异步**启动该命令: 工具立即返回一个
job id 而不阻塞当前回合; 进程在守护线程里持续跑, 输出被流式收集; 之后可用
配套工具 (status / logs / wait / cancel) 查询进度、取回输出或终止它。

典型场景:
- ``! npm install``          —— 长安装命令不卡在 Agent 回合里;
- ``! pytest -q``            —— 测试跑几十分钟, Agent 先去做别的事;
- ``! bash train.sh``        —— 训练脚本后台推进, 定期 ``status`` 看 epoch。

与 core/background.py (青小团的"手") 的区别:
- background.py 是**另一个 Agent** 在后台会话里自主干活;
- background_shell 是**一条 OS shell 命令**在后台跑, 不产生新 Agent。

设计约束
--------
- 纯线程 + 自包含持久目录, 不依赖 Agent/内核服务, 便于工具层与 CLI 复用;
- job 状态与输出写入 home/background_shell/<job_id>.{json,log}, 进程崩溃也能读到;
- 输出按行累积, 超过上限做远端截断 (只保留末尾 live 窗口), 防内存/CU 膨胀。
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

LIVE_MAX_BYTES = 1_000_000   # 内存中每个 job 最多保留 1MB 输出按行窗口
LOG_KEEP_BYTES = 2_000_000   # 落盘日志保留上限


def _default_home() -> Path:
    from ..config import home_dir  # 惰性, 避免循环导入
    return Path(home_dir())


class ShellJob:
    """一个后台 shell 命令 job (进程 + 环形输出缓冲)。"""

    def __init__(self, job_id: str, command: str, cwd: str, home: Path,
                 started_at: float) -> None:
        self.job_id = job_id
        self.command = command
        self.cwd = cwd
        self.home = home
        self.dir = home / "background_shell"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.log_path = self.dir / f"{job_id}.log"
        self.meta_path = self.dir / f"{job_id}.json"
        self.started_at = started_at
        self.status = "queued"          # queued | running | done | cancelled | failed
        self.returncode: Optional[int] = None
        self.error: Optional[str] = None
        self.pid: Optional[int] = None
        self.ended_at: Optional[float] = None
        self._lines: List[str] = []
        self._bytes = 0
        self._lock = threading.Lock()
        self._proc: Optional[subprocess.Popen] = None
        self._write_meta()

    # ---------------------------------------------------------- 元数据持久化
    def _write_meta(self) -> None:
        obj = {
            "job_id": self.job_id,
            "command": self.command,
            "cwd": self.cwd,
            "started_at": self.started_at,
            "status": self.status,
            "pid": self.pid,
            "returncode": self.returncode,
            "error": self.error,
            "ended_at": self.ended_at,
        }
        try:
            tmp = self.meta_path.with_suffix(".tmp.json")
            tmp.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
            tmp.replace(self.meta_path)
        except OSError as exc:
            log.debug("写入后台 shell 元数据失败: %s", exc)

    # ---------------------------------------------------------- 输出收集
    def _append(self, chunk: str) -> None:
        with self._lock:
            for ln in chunk.splitlines():
                self._lines.append(ln)
                self._bytes += len(ln) + 1
            # 超过内存上限: 丢弃最老的一半, 保留最新 live 窗口
            while self._bytes > LIVE_MAX_BYTES and len(self._lines) > 1000:
                old = self._lines.pop(0)
                self._bytes -= (len(old) + 1)
            # 落盘日志: 追加写 + 超限截断
            try:
                size = self.log_path.stat().st_size if self.log_path.exists() else 0
                if size > LOG_KEEP_BYTES:
                    self.log_path.write_text("...[log 截断]...\n", encoding="utf-8")
                with self.log_path.open("a", encoding="utf-8") as f:
                    f.write(chunk)
                    if not chunk.endswith("\n"):
                        f.write("\n")
            except OSError as exc:
                log.debug("写后台 shell 日志失败: %s", exc)

    # ---------------------------------------------------------- 进程控制
    def register_proc(self, proc: subprocess.Popen) -> None:
        self._proc = proc
        self.pid = proc.pid
        self.status = "running"
        self._write_meta()

    def _finalize(self, status: str, returncode: Optional[int], error: Optional[str]) -> None:
        self.status = status
        self.returncode = returncode
        self.error = error
        self.ended_at = time.time()
        self._write_meta()

    def _kill_tree(self) -> bool:
        """终止进程树 (Windows taskkill /T, POSIX killpg) 但不改状态 — 供超时/取消复用。"""
        proc = self._proc
        if proc is None or proc.poll() is not None:
            return False
        try:
            if sys.platform == "win32":
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                    capture_output=True, timeout=5,
                )
            else:
                import signal
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                except (ProcessLookupError, PermissionError):
                    pass
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    pass
        except Exception as exc:  # noqa: BLE001
            log.debug("取消后台 shell 异常: %s", exc)
            try:
                proc.kill()
            except Exception:  # noqa: BLE001
                return False
        return True

    def cancel(self) -> bool:
        """终止进程树并标记 cancelled。"""
        if not self._kill_tree():
            return False
        proc = self._proc
        self._finalize("cancelled", proc.returncode if proc is not None else None,
                       "已取消")
        return True

    # ---------------------------------------------------------- 查询
    @property
    def live_lines(self) -> List[str]:
        with self._lock:
            return list(self._lines)

    def tail(self, n: int = 20) -> str:
        lines = self.live_lines
        return "\n".join(lines[-n:]) if lines else ""

    def lines_since(self, offset: int = 0, limit: int = 100) -> tuple:
        """返回 ``(新增文本, 新游标)`` —— 增量跟随输出。

        ``offset`` 是上次读取的总行数游标; 只返回偏移之后的 ``limit`` 行, 供长跑
        任务只读"新产出"而不重复刷全量。新游标 = 当前总行数。
        """
        lines = self.live_lines
        total = len(lines)
        window = lines[offset:offset + limit]
        return "\n".join(window), total

    def to_dict(self) -> Dict[str, Any]:
        return {
            "job_id": self.job_id,
            "command": self.command,
            "status": self.status,
            "pid": self.pid,
            "returncode": self.returncode,
            "started_at": self.started_at,
            "elapsed": round((self.ended_at or time.time()) - self.started_at, 1),
            "error": self.error,
            "has_output": bool(self._lines),
        }

    def preview(self, head: int = 3, tailn: int = 5) -> str:
        """输出预览: 前几行 + 末几行, 便于一眼看状态而不全量拉取。"""
        lines = self.live_lines
        if not lines:
            return "(暂无输出)"
        head_lines = lines[:head]
        tail_lines = lines[-tailn:] if len(lines) > head + tailn else []
        parts = list(head_lines)
        if tail_lines:
            parts.append(f"... (中间 {len(lines) - len(head_lines) - len(tail_lines)} 行) ...")
            parts.extend(tail_lines)
        return "\n".join(parts)


class BackgroundShellManager:
    """管理一组后台 shell 命令 job, 支持跨进程读已完成任务的状态/输出。"""

    def __init__(self, home: Optional[Path] = None) -> None:
        self.home = Path(home) if home is not None else _default_home()
        self.dir = self.home / "background_shell"
        self.dir.mkdir(parents=True, exist_ok=True)
        self._jobs: Dict[str, ShellJob] = {}
        self._lock = threading.Lock()

    # ---------------------------------------------------------- 启动
    def _running_count(self) -> int:
        with self._lock:
            return sum(1 for j in self._jobs.values() if j.status in ("queued", "running"))

    def max_running(self) -> int:
        """并发上限 (环境变量 QXT_BG_SHELL_MAX, 默认 0=不限)。"""
        try:
            v = int(os.environ.get("QXT_BG_SHELL_MAX", "0") or "0")
            return max(0, v)
        except ValueError:
            return 0

    def start(self, command: str, cwd: Optional[str] = None,
              timeout: Optional[float] = None) -> ShellJob:
        """异步启动一条 shell 命令, 立即返回 job (不阻塞)。

        ``timeout`` 为可选软超时 (达到后自动 kill 进程树, 防失控)。
        超过并发上限 (见 max_running) 时抛 RuntimeError, 防止后台进程失控。
        """
        cap = self.max_running()
        if cap and self._running_count() >= cap:
            raise RuntimeError(
                f"后台 shell 并发已达上限 {cap} (可用 qxt bg shell list / cancel 清理, "
                f"或用 QXT_BG_SHELL_MAX 提高上限)")

        job = ShellJob("shl-" + uuid.uuid4().hex[:8], command,
                       cwd or os.getcwd(), self.home, time.time())
        with self._lock:
            self._jobs[job.job_id] = job

        def _read_stream(stream, is_err: bool) -> None:
            for line in iter(stream.readline, ""):
                if not line:
                    break
                job._append(line)
            stream.close()

        def _run() -> None:
            try:
                proc = subprocess.Popen(
                    command, shell=True, cwd=job.cwd or None,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    text=True, errors="replace",
                    start_new_session=os.name != "nt",
                )
                job.register_proc(proc)
                # 读线程: 关闭句柄避免管道缓冲阻塞子进程
                t_out = threading.Thread(target=_read_stream, args=(proc.stdout, False),
                                         daemon=True, name=f"shl-out-{job.job_id}")
                t_err = threading.Thread(target=_read_stream, args=(proc.stderr, True),
                                         daemon=True, name=f"shl-err-{job.job_id}")
                t_out.start(); t_err.start()
                try:
                    rc = proc.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    # 单一确定性状态: kP掉进程树后直接落 failed (不再先 cancelled 再覆盖,
                    # 避免 wait 轮询读到中间态)。
                    job._kill_tree()
                    job._finalize("failed", None, f"超过 {timeout}s 超时, 已终止进程树")
                    return
                t_out.join(timeout=2); t_err.join(timeout=2)
                job._finalize("done" if rc == 0 else "failed", rc, None)
            except Exception as exc:  # noqa: BLE001
                job._finalize("failed", None, f"{type(exc).__name__}: {exc}")

        threading.Thread(target=_run, name=f"shl-{job.job_id}", daemon=True).start()
        return job

    # ---------------------------------------------------------- 查询
    def _rehydrate(self, job_id: str) -> Optional[ShellJob]:
        """从磁盘重建一个已完成 job (跨进程读)。"""
        meta = self.dir / f"{job_id}.json"
        if not meta.exists():
            return None
        try:
            obj = json.loads(meta.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return None
        job = ShellJob(obj.get("job_id", job_id), obj.get("command", ""),
                       obj.get("cwd", ""), self.home, obj.get("started_at", 0.0))
        job.status = obj.get("status", "unknown")
        job.returncode = obj.get("returncode")
        job.error = obj.get("error")
        job.pid = obj.get("pid")
        job.ended_at = obj.get("ended_at")
        # 读回日志做 tail 重建
        try:
            for ln in job.log_path.read_text(encoding="utf-8", errors="replace").splitlines():
                job._lines.append(ln)
        except OSError:
            pass
        return job

    def get(self, job_id: str, *, live_only: bool = False) -> Optional[ShellJob]:
        with self._lock:
            job = self._jobs.get(job_id)
        if job is not None:
            return job
        if live_only:
            return None
        return self._rehydrate(job_id)

    def list(self, *, limit: int = 20) -> List[ShellJob]:
        jobs: List[ShellJob] = []
        with self._lock:
            for job in list(self._jobs.values()):
                jobs.append(job)
        # 补充磁盘上已结束、本进程未见过的 job (按文件 mtime 排序)
        seen = {j.job_id for j in jobs}
        disk = []
        for p in sorted(self.dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
            if p.stem in seen:
                continue
            job = self._rehydrate(p.stem)
            if job is not None:
                disk.append(job)
                seen.add(job.job_id)
        jobs.extend(disk)
        jobs.sort(key=lambda j: j.started_at, reverse=True)
        return jobs[:limit]

    def status_dict(self, job_id: Optional[str] = None,
                    *, tail: int = 0) -> Dict[str, Any]:
        """聚合状态; 不指定 job_id 时返回全部任务的概览。"""
        if job_id:
            job = self.get(job_id)
            if job is None:
                return {"ok": False, "error": f"后台任务 {job_id} 未找到",
                        "available": [j.job_id for j in self.list(limit=5)]}
            out = job.to_dict()
            out["preview"] = job.preview()
            if tail > 0:
                out["tail"] = job.tail(tail)
            out["ok"] = True
            return out
        jobs = self.list()
        return {
            "ok": True,
            "total": len(jobs),
            "by_status": _count_status(jobs),
            "running": self._running_count(),
            "max_running": self.max_running(),
            "jobs": [j.to_dict() for j in jobs],
        }

    def tail(self, job_id: str, n: int = 20) -> str:
        job = self.get(job_id)
        return job.tail(n) if job else f"[错误] 后台任务 {job_id} 未找到"

    def tail_since(self, job_id: str, offset: int = 0, limit: int = 100) -> Dict[str, Any]:
        """增量读一段后台任务的新输出。返回 dict: text / next_offset / ok。"""
        job = self.get(job_id)
        if job is None:
            return {"ok": False, "error": f"后台任务 {job_id} 未找到"}
        text, total = job.lines_since(max(0, offset), max(1, limit))
        return {"ok": True, "job_id": job_id, "status": job.status,
                "text": text, "next_offset": total}

    # ---------------------------------------------------------- 阻塞/取消
    def wait(self, job_id: str, timeout: Optional[float] = None) -> ShellJob:
        """阻塞等待后台命令结束 (可选超时)。"""
        job = self.get(job_id)
        if job is None:
            raise KeyError(job_id)
        deadline = None if timeout is None else time.time() + timeout
        while job.status in ("queued", "running"):
            if deadline is not None and time.time() >= deadline:
                break
            time.sleep(0.2)
            job = self.get(job_id) or job
        return job

    def cancel(self, job_id: str) -> bool:
        job = self.get(job_id)
        if job is None:
            return False
        # 进程可能尚未注册 (start 后 run 线程还没 Popen 完成): 短暂等待 pid 就绪
        deadline = time.time() + 1.5
        while job._proc is None and job.status == "queued" and time.time() < deadline:
            time.sleep(0.05)
        return job.cancel()

    def prune_finished(self) -> int:
        """清理本进程内存中已结束的 job (保留磁盘日志)。"""
        removed = 0
        with self._lock:
            for jid in [j.job_id for j in self._jobs.values() if j.status not in
                        ("queued", "running")]:
                self._jobs.pop(jid, None)
                removed += 1
        return removed

    def prune_disk(self, *, keep_running: bool = True) -> int:
        """清理磁盘上已结束 job 的元数据与日志文件 (释放占位)。

        保留 running/queued 的 job (跨进程重启后仍需读其状态)。返回移除文件数。
        """
        removed = 0
        for meta in list(self.dir.glob("*.json")):
            jid = meta.stem
            job = self.get(jid, live_only=True)
            if job is not None and job.status in ("queued", "running"):
                continue
            # 落盘再读一次以判别 status (live 不在内存时)
            try:
                obj = json.loads(meta.read_text(encoding="utf-8"))
                if keep_running and obj.get("status") in ("queued", "running"):
                    continue
            except Exception:  # noqa: BLE001
                continue
            try:
                meta.unlink()
            except OSError:
                continue
            removed += 1
            logf = self.dir / f"{jid}.log"
            try:
                logf.unlink()
            except OSError:
                pass
        return removed


def _count_status(jobs: List[ShellJob]) -> Dict[str, int]:
    by: Dict[str, int] = {}
    for j in jobs:
        by[j.status] = by.get(j.status, 0) + 1
    return by


# ================================================================ 工具层入口

_singleton: Optional[BackgroundShellManager] = None
_singleton_lock = threading.Lock()


def get_manager(home: Optional[Path] = None) -> BackgroundShellManager:
    global _singleton
    with _singleton_lock:
        if _singleton is None:
            _singleton = BackgroundShellManager(home)
    return _singleton


def is_bg_command(command: str) -> bool:
    """判断命令是否为后台形式 (以 ``!`` 开头, 允许前置空白)。"""
    return bool(command) and command.lstrip().startswith("!")


def strip_bg_prefix(command: str) -> str:
    """去掉 ``!`` 前缀, 返回真正要执行的命令后缀 (可选前后空白)。"""
    body = command.lstrip()
    if body.startswith("!"):
        body = body[1:]
    return body


def run_in_background(command: str, cwd: Optional[str] = None,
                      timeout: Optional[float] = None) -> ShellJob:
    """工具层便捷入口: 解析 ``! cmd`` 并异步执行, 返回 job。"""
    real = strip_bg_prefix(command).strip()
    if not real:
        raise ValueError("后台命令为空")
    return get_manager().start(real, cwd=cwd, timeout=timeout)