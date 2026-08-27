"""JSONL IPC 客户端 - 纯 Python 引擎适配

通过统一 JSONL IPC 协议与 Python 引擎通信。
支持: 请求/响应/流式/首帧 ready。
"""
import json
import logging
import subprocess
import sys
import threading
import queue
import os
from typing import Any, Dict, Optional

log = logging.getLogger(__name__)


class IpcError(Exception):
    """IPC 通信错误"""
    pass


class IpcClient:
    """JSONL IPC 客户端, 与 Python 引擎对话"""

    def __init__(self, engine_name: str, engine_path: Optional[str] = None):
        self.engine_name = engine_name
        self.engine_path = engine_path
        self.proc: Optional[subprocess.Popen] = None
        self._write_lock = threading.Lock()
        self._read_thread = None
        self._responses: Dict[int, queue.Queue] = {}
        self._streams: Dict[int, queue.Queue] = {}
        self._request_stream: Dict[int, Any] = {}
        self._ready = threading.Event()
        self._closed = False
        self._seq = 0

    def _next_id(self) -> int:
        """协议要求 id 为数字, 用递增计数器。"""
        self._seq += 1
        return self._seq

    def start(self):
        """启动引擎进程"""
        if self.engine_path:
            cmd = [sys.executable, self.engine_path]
        else:
            from qingxiaotuan.ext.registry import ENGINE_MAP
            entry = ENGINE_MAP.get(self.engine_name)
            if entry is not None:
                module = entry[0]
            else:
                module = f"qingxiaotuan.ext.{self.engine_name}_engine"
            cmd = [sys.executable, "-m", module]

        self.proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )

        self._read_thread = threading.Thread(target=self._read_loop, daemon=True)
        self._read_thread.start()

        if not self._ready.wait(timeout=5.0):
            raise IpcError(f"Engine {self.engine_name} did not become ready")

        return self

    def _read_loop(self):
        """后台读取循环"""
        try:
            for line in self.proc.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    continue

                if msg.get("ready"):
                    self._ready.set()
                    continue

                msg_id = msg.get("id")
                if msg.get("stream"):
                    cb = self._request_stream.get(msg_id)
                    if cb is not None:
                        cb(msg.get("chunk"))
                    elif msg_id in self._streams:
                        self._streams[msg_id].put(msg)
                elif msg_id in self._responses:
                    self._responses[msg_id].put(msg)
        except Exception as exc:  # noqa: BLE001
            log.debug("引擎读取循环异常 (%s): %s", self.engine_name, exc)
        finally:
            # 管道已断 (引擎退出/崩溃): 让所有挂起的请求/流立即失败,
            # 避免 request() 永久阻塞在队列等待上。
            for q in list(self._responses.values()):
                q.put({"ok": False, "error": "engine closed"})
            for q in list(self._streams.values()):
                q.put({"ok": False, "error": "engine closed"})

    def request(self, method: str, params: Optional[Dict[str, Any]] = None, timeout: float = 30.0,
                on_stream: Any = None) -> Any:
        """发送请求并等待响应; on_stream 回调接收流式 chunk。"""
        req_id = self._next_id()
        if params is None:
            params = {}

        msg = {"id": req_id, "method": method, "params": params}
        if on_stream is not None:
            msg["stream"] = True
            self._request_stream[req_id] = on_stream
        resp_queue: queue.Queue = queue.Queue()
        self._responses[req_id] = resp_queue

        with self._write_lock:
            proc = self.proc
            if proc is None or proc.stdin is None:
                raise IpcError(f"Engine {self.engine_name} 未启动")
            proc.stdin.write(json.dumps(msg, ensure_ascii=False) + "\n")
            proc.stdin.flush()

        try:
            resp = resp_queue.get(timeout=timeout)
        except queue.Empty:
            self._responses.pop(req_id, None)
            self._request_stream.pop(req_id, None)
            raise IpcError(f"Request {method} timed out")
        finally:
            self._responses.pop(req_id, None)
            self._request_stream.pop(req_id, None)

        if resp.get("ok"):
            return resp.get("result")
        else:
            raise IpcError(resp.get("error", f"Engine error: {method}"))

    def stream(self, method: str, params: Optional[Dict[str, Any]] = None) -> queue.Queue:
        """发送流式请求"""
        req_id = self._next_id()
        if params is None:
            params = {}

        msg = {"id": req_id, "method": method, "params": params, "stream": True}
        stream_queue: queue.Queue = queue.Queue()
        self._streams[req_id] = stream_queue

        with self._write_lock:
            proc = self.proc
            if proc is None or proc.stdin is None:
                raise IpcError(f"Engine {self.engine_name} 未启动")
            proc.stdin.write(json.dumps(msg, ensure_ascii=False) + "\n")
            proc.stdin.flush()

        return stream_queue

    def close(self):
        """关闭引擎进程并回收读线程, 避免残留 _read_loop 线程与子进程。"""
        if self._closed:
            return
        self._closed = True
        proc, self.proc = self.proc, None
        if proc is None:
            return
        try:
            if proc.stdin:
                proc.stdin.close()
        except Exception as exc:
            log.debug("关闭引擎 stdin 失败: %s", exc)
        try:
            proc.terminate()
        except Exception as exc:
            log.debug("终止引擎进程失败: %s", exc)
        try:
            proc.wait(timeout=3)
        except Exception as exc:
            log.debug("等待引擎进程退出失败: %s", exc)
        if proc.poll() is None:
            # terminate 未生效 (引擎线程不响应), 升级为强制终止。
            try:
                proc.kill()
            except Exception as exc:
                log.debug("强杀引擎进程失败: %s", exc)
            try:
                proc.wait(timeout=3)
            except Exception as exc:
                log.debug("等待引擎进程强杀退出失败: %s", exc)
        if proc.poll() is None and os.name == "nt":
            # Windows 上 kill 也可能无效, 用 taskkill /T 连根拔起。
            try:
                subprocess.run(
                    ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    timeout=5, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            except Exception as exc:
                log.debug("taskkill 清理引擎进程失败: %s", exc)
        # 关闭 stdout 让 _read_loop 的 for 循环自然结束, 再回收线程
        for stream in (proc.stdout, proc.stderr):
            try:
                if stream:
                    stream.close()
            except Exception as exc:
                log.debug("关闭引擎输出流失败: %s", exc)
        thread, self._read_thread = self._read_thread, None
        if thread is not None and thread.is_alive():
            thread.join(timeout=3)

    def __enter__(self):
        return self.start()

    def __exit__(self, *args):
        self.close()


class ExternalEngineManager:
    """外部引擎管理器 - 供 CLI 和工具层使用"""

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.clients: Dict[str, IpcClient] = {}
        self.config = config or {}

    def _ensure_client(self, name: str) -> IpcClient:
        """获取或创建引擎客户端"""
        if name not in self.clients:
            client = IpcClient(name)
            client.start()
            self.clients[name] = client
        return self.clients[name]

    def call(self, engine: str, method: str, params: Optional[Dict[str, Any]] = None, timeout: float = 30.0) -> Any:
        """调用引擎方法"""
        client = self._ensure_client(engine)
        return client.request(method, params, timeout=timeout)

    def list_engines(self) -> list:
        """列出所有可用引擎"""
        from qingxiaotuan.ext.registry import ENGINE_NAMES
        return ENGINE_NAMES

    def healthcheck(self) -> dict:
        """健康检查"""
        from qingxiaotuan.ext.registry import engine_healthcheck
        return engine_healthcheck()

    def close_all(self):
        """关闭所有客户端"""
        for client in self.clients.values():
            client.close()
        self.clients.clear()


# 单例
_engine_manager: Optional[ExternalEngineManager] = None


def get_engine_manager() -> ExternalEngineManager:
    """获取引擎管理器单例"""
    global _engine_manager
    if _engine_manager is None:
        _engine_manager = ExternalEngineManager()
    return _engine_manager


def close_all_managers() -> None:
    """关闭所有外部引擎进程 (测试后清理, 防止跨测试 IPC 句柄泄漏)。

    供 tests/conftest.py 的 autouse fixture 调用; 管理器的单例在进程内常驻,
    测试间若不显式关闭, Windows 上子进程句柄会泄漏并拖慢后续用例。
    """
    if _engine_manager is not None:
        _engine_manager.close_all()
