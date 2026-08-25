"""JSONL IPC 客户端 - 纯 Python 引擎适配

通过统一 JSONL IPC 协议与 Python 引擎通信。
支持: 请求/响应/流式/首帧 ready。
"""
import json
import subprocess
import sys
import threading
import queue
import os


class IpcError(Exception):
    """IPC 通信错误"""
    pass


class IpcClient:
    """JSONL IPC 客户端, 与 Python 引擎对话"""

    def __init__(self, engine_name: str, engine_path: str = None):
        self.engine_name = engine_name
        self.engine_path = engine_path
        self.proc = None
        self._write_lock = threading.Lock()
        self._read_thread = None
        self._responses = {}
        self._streams = {}
        self._request_stream = {}
        self._ready = threading.Event()
        self._closed = False
        self._seq = 0

    def _next_id(self) -> int:
        """协议要求 id 为数字 (与 ext/c 和 ext/ts 一致), 用递增计数器。"""
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
        except Exception:
            pass

    def request(self, method: str, params: dict = None, timeout: float = 30.0,
                on_stream=None):
        """发送请求并等待响应; on_stream 回调接收流式 chunk。"""
        req_id = self._next_id()
        if params is None:
            params = {}

        msg = {"id": req_id, "method": method, "params": params}
        if on_stream is not None:
            msg["stream"] = True
            self._request_stream[req_id] = on_stream
        resp_queue = queue.Queue()
        self._responses[req_id] = resp_queue

        with self._write_lock:
            self.proc.stdin.write(json.dumps(msg, ensure_ascii=False) + "\n")
            self.proc.stdin.flush()

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

    def stream(self, method: str, params: dict = None):
        """发送流式请求"""
        req_id = self._next_id()
        if params is None:
            params = {}

        msg = {"id": req_id, "method": method, "params": params, "stream": True}
        stream_queue = queue.Queue()
        self._streams[req_id] = stream_queue

        with self._write_lock:
            self.proc.stdin.write(json.dumps(msg, ensure_ascii=False) + "\n")
            self.proc.stdin.flush()

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
        except Exception:
            pass
        try:
            proc.terminate()
        except Exception:
            pass
        try:
            proc.wait(timeout=3)
        except Exception:
            pass
        # 关闭 stdout 让 _read_loop 的 for 循环自然结束, 再回收线程
        for stream in (proc.stdout, proc.stderr):
            try:
                if stream:
                    stream.close()
            except Exception:
                pass
        thread, self._read_thread = self._read_thread, None
        if thread is not None and thread.is_alive():
            thread.join(timeout=3)

    def __enter__(self):
        return self.start()

    def __exit__(self, *args):
        self.close()


class ExternalEngineManager:
    """外部引擎管理器 - 供 CLI 和工具层使用"""

    def __init__(self, config: dict = None):
        self.clients = {}
        self.config = config or {}

    def _ensure_client(self, name: str) -> IpcClient:
        """获取或创建引擎客户端"""
        if name not in self.clients:
            client = IpcClient(name)
            client.start()
            self.clients[name] = client
        return self.clients[name]

    def call(self, engine: str, method: str, params: dict = None, timeout: float = 30.0):
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
_engine_manager = None


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
