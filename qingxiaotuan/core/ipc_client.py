"""与外部引擎 (C 编译程序 / TypeScript 模块) 的 JSONL IPC 客户端。

统一协议 (与 ext/c/common/ipc.h 和 ext/ts/src/protocol.ts 完全对应):
    请求:  { id:int, method:str, params?:dict }
    响应:  { id:int, ok:bool, result?:any, error?:str }
    流块:  { id:int, stream:true, chunk:any }
    首帧:  { ready:true }

本模块与语言无关: 无论是 gcc 编译出的 qxt_*.exe, 还是用 node 跑的 TS 模块,
只要遵守上面的行协议, 都能用同一个客户端驱动。

设计要点:
    * 进程长驻, 复用连接 (index 这类有状态引擎必须如此)。
    * 后台线程逐行读 stdout, 按 id 把响应投递给 Future。
    * 每个请求带超时, 进程意外退出会立即让所有挂起请求失败。
"""

from __future__ import annotations

import json
import logging
import subprocess
import threading
import weakref
from concurrent.futures import Future
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("qingxiaotuan.ipc")


class IpcError(RuntimeError):
    """引擎返回 ok=false 或进程异常。"""


class IpcProcess:
    """管理一个长驻的外部引擎子进程。"""

    def __init__(
        self,
        cmd: List[str],
        *,
        env: Optional[Dict[str, str]] = None,
        cwd: Optional[str] = None,
        startup_timeout: float = 10.0,
        quiet: bool = False,
    ) -> None:
        self._cmd = cmd
        self._lock = threading.Lock()
        self._write_lock = threading.Lock()
        self._seq = 0
        self._pending: Dict[int, Future] = {}
        self._ready = threading.Event()
        self._stream_cb: Optional[Callable[[int, Any], None]] = None
        self._request_stream: Dict[int, Callable[[Any], None]] = {}
        self._closed = False
        self._quiet = quiet
        self._stderr_buf: List[str] = []

        try:
            self._proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                cwd=cwd,
                text=True,
                bufsize=1,
                encoding="utf-8",
                errors="replace",
            )
        except FileNotFoundError as exc:
            raise IpcError(f"找不到引擎可执行文件: {cmd[0]} ({exc})") from exc
        except OSError as exc:
            raise IpcError(f"无法启动引擎 {cmd[0]}: {exc}") from exc

        self._reader = threading.Thread(target=self._read_loop, name="ipc-reader", daemon=True)
        self._reader.start()
        threading.Thread(target=self._drain_stderr, name="ipc-stderr", daemon=True).start()
        if not self._ready.wait(timeout=startup_timeout):
            tail = "".join(self._stderr_buf[-8:]).strip()
            self.close()
            raise IpcError(
                f"引擎未在 {startup_timeout}s 内就绪: {' '.join(cmd)}"
                + (f"\n引擎 stderr:\n{tail}" if tail else "")
            )

    def _drain_stderr(self) -> None:
        """消费 stderr, 既避免管道写满死锁, 也保留最近若干行用于启动失败诊断。"""
        if self._proc.stderr is None:
            return
        try:
            for line in self._proc.stderr:
                line = line.rstrip("\n")
                if not line:
                    continue
                if len(self._stderr_buf) >= 64:
                    self._stderr_buf.pop(0)
                self._stderr_buf.append(line)
                logger.debug("[%s] %s", self._cmd[0], line)
        except (OSError, ValueError):
            pass

    # -------------------------------------------------------------- 通信

    def _read_loop(self) -> None:
        assert self._proc.stdout is not None
        try:
            for raw in self._proc.stdout:
                line = raw.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if msg.get("ready") is True:
                    self._ready.set()
                    continue
                if msg.get("stream") is True:
                    cid = msg.get("id", 0)
                    callback = self._request_stream.get(cid)
                    if callback is not None:
                        try:
                            callback(msg.get("chunk"))
                        except Exception:  # noqa: BLE001
                            pass
                    if self._stream_cb is not None:
                        try:
                            self._stream_cb(cid, msg.get("chunk"))
                        except Exception:  # noqa: BLE001
                            pass
                    continue
                rid = msg.get("id")
                fut = self._pending.pop(rid, None) if rid is not None else None
                if rid is not None:
                    self._request_stream.pop(rid, None)
                if fut is None:
                    continue
                if msg.get("ok") is True:
                    fut.set_result(msg.get("result"))
                else:
                    fut.set_exception(IpcError(msg.get("error", "unknown error")))
        except Exception as exc:  # noqa: BLE001
            logger.debug("ipc reader stopped: %s", exc)
        finally:
            # 进程结束: 让所有挂起请求失败
            with self._lock:
                pend = list(self._pending.values())
                self._pending.clear()
            for fut in pend:
                if not fut.done():
                    fut.set_exception(IpcError("引擎进程已退出"))

    def is_alive(self) -> bool:
        """子进程是否仍在运行 (未退出)。"""
        return self._proc.poll() is None

    def request(self, method: str, params: Optional[dict] = None, *, timeout: float = 60.0,
                on_stream: Optional[Callable[[Any], None]] = None) -> Any:
        """发送一个请求并等待响应。"""
        if self._closed:
            raise IpcError("引擎已关闭")
        if not self.is_alive():
            raise IpcError("引擎进程已退出")
        with self._lock:
            self._seq += 1
            rid = self._seq
            fut: Future = Future()
            self._pending[rid] = fut
            if on_stream is not None:
                self._request_stream[rid] = on_stream
        req = {"id": rid, "method": method, "params": params or {}}
        try:
            stdin = self._proc.stdin
            if stdin is None:
                raise IpcError("引擎 stdin 不可用")
            with self._write_lock:
                stdin.write(json.dumps(req, ensure_ascii=False) + "\n")
                stdin.flush()
        except (BrokenPipeError, ValueError, OSError) as exc:
            # Windows 上进程已退出时写 stdin 会抛 OSError[Errno 22] (无效参数)
            with self._lock:
                self._pending.pop(rid, None)
                self._request_stream.pop(rid, None)
            raise IpcError(f"写入引擎失败 (进程可能已退出): {exc}") from exc
        try:
            return fut.result(timeout=timeout)
        except TimeoutError as exc:
            with self._lock:
                self._pending.pop(rid, None)
                self._request_stream.pop(rid, None)
            raise IpcError(f"引擎请求超时 ({timeout}s): {method}") from exc

    def set_stream_callback(self, cb: Optional[Callable[[int, Any], None]]) -> None:
        self._stream_cb = cb

    def ping(self) -> bool:
        try:
            self.request("list", timeout=5.0)
            return True
        except Exception:  # noqa: BLE001
            return False

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        # 1) 尝试优雅停机: 发 _quit
        try:
            stdin = self._proc.stdin
            if stdin is not None and self.is_alive():
                with self._write_lock:
                    try:
                        stdin.write(json.dumps({"id": 0, "method": "_quit"}) + "\n")
                        stdin.flush()
                    except (BrokenPipeError, ValueError, OSError):
                        pass
        except Exception:  # noqa: BLE001
            pass
        # 2) 终止进程
        try:
            if self.is_alive():
                self._proc.terminate()
        except Exception:  # noqa: BLE001
            pass
        try:
            self._proc.wait(timeout=3)
        except Exception:  # noqa: BLE001
            try:
                self._proc.kill()
            except Exception:  # noqa: BLE001
                pass
        # 3) 显式关闭管道, 避免解释器退出时 GC 触发 unraisable OSError
        for pipe in (self._proc.stdin, self._proc.stdout, self._proc.stderr):
            try:
                if pipe is not None:
                    pipe.close()
            except Exception:  # noqa: BLE001
                pass

    @property
    def pid(self) -> Optional[int]:
        return self._proc.pid if self._proc else None


# 进程级弱引用注册表: 跟踪所有活着的 ExternalEngineManager, 便于测试后统一清理
# 长驻 IPC 进程, 避免跨测试进程句柄泄漏 (Windows 上尤为敏感)。
_ALL_MANAGERS: "weakref.WeakSet" = weakref.WeakSet()


def close_all_managers() -> None:
    """关闭所有活着的 ExternalEngineManager 的引擎进程 (测试隔离用)。"""
    for mgr in list(_ALL_MANAGERS):
        try:
            mgr.close_all()
        except Exception:  # noqa: BLE001
            pass


class ExternalEngineManager:
    """发现并懒加载 C / TS 外部引擎, 每个引擎一个持久 IpcProcess。"""

    def __init__(self, config: Optional[dict] = None, *, quiet: bool = False) -> None:
        self._cfg = config or {}
        self._quiet = quiet
        self._handles: Dict[str, IpcProcess] = {}
        self._lock = threading.Lock()
        self._node_exe = self._cfg.get("ext.node_exe") or self._detect_node()
        self._c_bin_dir = self._cfg.get("ext.c_bin_dir")
        self._ts_src_dir = self._cfg.get("ext.ts_src_dir")
        self._ts_dist_dir = self._cfg.get("ext.ts_dist_dir")
        self._repo_root = self._cfg.get("ext.repo_root") or self._detect_repo_root()
        _ALL_MANAGERS.add(self)

    # ---------------------------------------------------------- 路径探测

    @staticmethod
    def _detect_repo_root() -> str:
        # core/ipc_client.py -> parents[2] = 仓库根
        from pathlib import Path

        p = Path(__file__).resolve()
        for cand in (p.parents[2], p.parents[1]):
            if (cand / "ext").exists():
                return str(cand)
        # 退回: 找当前工作区里的 ext
        return str(Path.cwd())

    def _detect_node(self) -> Optional[str]:
        import shutil

        for cand in (self._cfg.get("ext.node_exe"),):
            if cand and shutil.which(cand):
                return cand
        found = shutil.which("node") or shutil.which("node.exe")
        if found:
            return found
        # 常见受管路径
        import os

        for base in (os.environ.get("LOCALAPPDATA", ""), os.environ.get("HOME", "")):
            if not base:
                continue
            for pat in ("*/node/versions/*/node.exe", "*/nodejs/node.exe"):
                import glob

                hits = glob.glob(os.path.join(base, pat))
                if hits:
                    return hits[0]
        return None

    def _c_binary(self, name: str) -> Optional[str]:
        from pathlib import Path

        root = Path(self._c_bin_dir) if self._c_bin_dir else Path(self._repo_root) / "ext" / "dist" / "bin"
        for ext in ("", ".exe"):
            cand = root / f"qxt_{name}{ext}"
            if cand.exists():
                return str(cand)
        return None

    def _ts_main(self, name: str) -> Optional[List[str]]:
        """返回启动 TS 引擎的命令 (优先用编译产物 dist, 否则 node --experimental-strip-types)。"""
        if not self._node_exe:
            return None
        from pathlib import Path

        root = Path(self._ts_src_dir) if self._ts_src_dir else Path(self._repo_root) / "ext" / "ts"
        dist_main = (Path(self._ts_dist_dir) if self._ts_dist_dir else root / "dist") / name / "main.js"
        src_main = root / "src" / name / "main.ts"
        if dist_main.exists():
            return [self._node_exe, str(dist_main)]
        if src_main.exists():
            return [self._node_exe, "--experimental-strip-types", str(src_main)]
        return None

    def command_for(self, engine: str) -> Optional[List[str]]:
        """给定引擎名, 返回启动命令; 若引擎不可用返回 None。"""
        if engine.startswith("c:"):
            binpath = self._c_binary(engine[2:])
            return [binpath] if binpath else None
        if engine.startswith("ts:"):
            return self._ts_main(engine[3:])
        # 先试 C, 再试 TS
        c = self._c_binary(engine)
        if c:
            return [c]
        return self._ts_main(engine)

    def available(self) -> List[str]:
        """列出当前环境真正可用的引擎名。"""
        names = [
            "diff", "patch", "merge3", "crypto", "index", "ansi",
            "sandbox", "watch", "safety", "json",  # C 引擎 (qxt_*)
            "rules", "plugin-host", "mcp-client", "skill-market", "dashboard", "agent-sdk",
            "search", "notify",            # TS 模块 (检索 / 通知)
        ]
        out = []
        for n in names:
            if self.command_for(n):
                out.append(n)
        return out

    # ---------------------------------------------------------- 句柄管理

    def handle(self, engine: str, *, timeout: float = 60.0) -> IpcProcess:
        with self._lock:
            h = self._handles.get(engine)
            if h is not None and not h._closed and h.is_alive():
                return h
        cmd = self.command_for(engine)
        if not cmd:
            raise IpcError(f"引擎不可用 (未编译或未安装 node): {engine}")
        h = IpcProcess(cmd, quiet=self._quiet)
        with self._lock:
            # 二次检查: 避免并发创建两个句柄
            old = self._handles.get(engine)
            if old is not None and not old._closed and old.is_alive():
                h.close()
                return old
            self._handles[engine] = h
        return h

    def call(self, engine: str, method: str, params: Optional[dict] = None, *, timeout: float = 60.0) -> Any:
        try:
            return self.handle(engine, timeout=timeout).request(method, params, timeout=timeout)
        except IpcError:
            # 进程意外退出的偶发竞态: 关闭残留句柄, 重建进程重试一次 (自愈)
            with self._lock:
                self._handles.pop(engine, None)
            try:
                return self.handle(engine, timeout=timeout).request(method, params, timeout=timeout)
            except IpcError:
                raise

    def close_all(self) -> None:
        with self._lock:
            handles = list(self._handles.values())
            self._handles.clear()
        for h in handles:
            try:
                h.close()
            except Exception:  # noqa: BLE001
                pass
