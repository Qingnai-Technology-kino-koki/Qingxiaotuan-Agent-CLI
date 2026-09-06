"""离线模式支持: Ollama 本地模型集成与离线状态管理。

核心价值:
- 完全离线运行，数据不出境
- 零网络延迟
- 企业合规友好

功能:
- Ollama 连接检测与模型管理
- 离线模式状态显示
- 模型下载与切换
"""

from __future__ import annotations

import json
import subprocess
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional


@dataclass
class OllamaModel:
    """Ollama 模型信息。"""
    name: str
    size: int  # bytes
    modified_at: str
    digest: str
    
    @property
    def size_human(self) -> str:
        """人类可读的大小。"""
        if self.size >= 1024 ** 3:
            return f"{self.size / 1024 ** 3:.1f}GB"
        elif self.size >= 1024 ** 2:
            return f"{self.size / 1024 ** 2:.1f}MB"
        return f"{self.size / 1024:.1f}KB"


@dataclass
class OllamaStatus:
    """Ollama 服务状态。"""
    available: bool
    version: Optional[str] = None
    models: Optional[List[OllamaModel]] = None
    error: Optional[str] = None
    
    def __post_init__(self):
        if self.models is None:
            self.models = []


class OllamaManager:
    """Ollama 管理器: 管理本地 Ollama 服务。"""
    
    def __init__(self, base_url: str = "http://localhost:11434") -> None:
        self.base_url = base_url
        self._status: Optional[OllamaStatus] = None
    
    def check_status(self) -> OllamaStatus:
        """检查 Ollama 服务状态。"""
        try:
            # 检查 Ollama 是否在运行
            result = subprocess.run(
                ["ollama", "list"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            
            if result.returncode != 0:
                self._status = OllamaStatus(
                    available=False,
                    error=f"Ollama 未运行或未安装: {result.stderr.strip()}"
                )
                return self._status
            
            # 解析已安装的模型
            models = []
            for line in result.stdout.strip().split("\n")[1:]:  # 跳过标题行
                if not line.strip():
                    continue
                parts = line.split()
                if len(parts) >= 3:
                    name = parts[0]
                    # 解析大小
                    size_str = parts[2] if len(parts) > 2 else "0B"
                    size = self._parse_size(size_str)
                    models.append(OllamaModel(
                        name=name,
                        size=size,
                        modified_at=parts[1] if len(parts) > 1 else "",
                        digest=parts[3] if len(parts) > 3 else "",
                    ))
            
            self._status = OllamaStatus(
                available=True,
                models=models,
            )
            return self._status
            
        except FileNotFoundError:
            self._status = OllamaStatus(
                available=False,
                error="Ollama 未安装。请访问 https://ollama.com 安装。"
            )
            return self._status
        except subprocess.TimeoutExpired:
            self._status = OllamaStatus(
                available=False,
                error="Ollama 响应超时"
            )
            return self._status
        except Exception as exc:
            self._status = OllamaStatus(
                available=False,
                error=f"检查 Ollama 状态失败: {exc}"
            )
            return self._status
    
    def _parse_size(self, size_str: str) -> int:
        """解析大小字符串 (如 '4.7GB') 为字节数。"""
        size_str = size_str.strip().upper()
        multipliers = {
            "B": 1,
            "KB": 1024,
            "MB": 1024 ** 2,
            "GB": 1024 ** 3,
            "TB": 1024 ** 4,
        }
        
        for suffix, mult in multipliers.items():
            if size_str.endswith(suffix):
                try:
                    return int(float(size_str[:-len(suffix)]) * mult)
                except ValueError:
                    return 0
        return 0
    
    def pull_model(self, model_name: str, callback: Optional[Callable[[str], Any]] = None) -> bool:
        """下载模型。"""
        try:
            proc = subprocess.Popen(
                ["ollama", "pull", model_name],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )

            # 实时读取输出
            stdout = proc.stdout
            if stdout is not None:
                for line in stdout:
                    if callback:
                        callback(line.strip())
            
            proc.wait(timeout=300)  # 5分钟超时
            return proc.returncode == 0
            
        except Exception:
            return False
    
    def list_available_models(self) -> List[str]:
        """列出 Ollama 官方可用的模型。"""
        # 常用模型列表
        return [
            "qwen2.5:7b",
            "qwen2.5:14b",
            "qwen2.5:32b",
            "llama3.1:8b",
            "llama3.1:70b",
            "deepseek-v3",
            "gemma2:9b",
            "mistral:7b",
            "codellama:7b",
        ]


# 全局实例
_ollama_manager: Optional[OllamaManager] = None


def get_ollama_manager() -> OllamaManager:
    """获取 Ollama 管理器单例。"""
    global _ollama_manager
    if _ollama_manager is None:
        _ollama_manager = OllamaManager()
    return _ollama_manager


def detect_llamacpp(base_url: str = "http://localhost:8080/v1", timeout: float = 2.0) -> Dict[str, Any]:
    """探测本地 llama.cpp 服务器 (OpenAI 兼容 /v1/models)。

    仅用标准库 urllib, 不引入额外依赖; 探测失败 (未启动/未安装) 时返回 available=False。
    """
    import urllib.error
    import urllib.request

    try:
        req = urllib.request.Request(base_url.rstrip("/") + "/models")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        models = [m.get("id") for m in data.get("data", []) if isinstance(m, dict) and m.get("id")]
        return {"available": True, "models": models, "error": None}
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "models": [], "error": str(exc)}


def detect_local_models() -> Dict[str, Any]:
    """聚合探测本机已安装的本地模型 (Ollama + llama.cpp)。

    供 `qxt models local` 调用, 返回统一结构:
        {
          "ollama":   {"available": bool, "models": [name...], "error": str|None},
          "llamacpp": {"available": bool, "models": [name...], "error": str|None},
        }
    """
    manager = get_ollama_manager()
    status = manager.check_status()
    llama = detect_llamacpp()

    return {
        "ollama": {
            "available": status.available,
            "models": [m.name for m in (status.models or [])],
            "error": status.error,
        },
        "llamacpp": {
            "available": llama["available"],
            "models": llama["models"] or [],
            "error": llama["error"],
        },
    }


def check_offline_status() -> Dict[str, Any]:
    """检查离线模式状态 (Ollama + llama.cpp)。"""
    manager = get_ollama_manager()
    status = manager.check_status()
    llama = detect_llamacpp()

    return {
        "ollama_available": status.available,
        "ollama_error": status.error,
        "installed_models": [
            {
                "name": m.name,
                "size": m.size_human,
            }
            for m in (status.models or [])
        ],
        "llamacpp_available": llama["available"],
        "llamacpp_models": llama["models"],
        "llamacpp_error": llama["error"],
        "recommended_models": [
            "qwen2.5:7b",  # 轻量级，适合一般任务
            "deepseek-v3",  # 代码能力强
            "llama3.1:70b",  # 强大但需要更多资源
        ],
    }


def format_offline_status() -> str:
    """格式化离线状态为可读字符串。"""
    status = check_offline_status()
    
    lines = []
    lines.append("=== 离线模式状态 ===")
    lines.append("")
    
    if status["ollama_available"]:
        lines.append("✓ Ollama 服务可用")
        lines.append("")
        
        models = status["installed_models"]
        if models:
            lines.append(f"已安装模型 ({len(models)} 个):")
            for m in models:
                lines.append(f"  • {m['name']} ({m['size']})")
        else:
            lines.append("未安装任何模型")
            lines.append("运行 'ollama pull <model>' 下载模型")
    else:
        lines.append("✗ Ollama 服务不可用")
        if status["ollama_error"]:
            lines.append(f"  错误: {status['ollama_error']}")
        lines.append("")
        lines.append("安装 Ollama:")
        lines.append("  访问 https://ollama.com 下载安装")

    lines.append("")
    if status.get("llamacpp_available"):
        llama_models = status.get("llamacpp_models") or []
        lines.append("✓ llama.cpp 服务可用 (:8080/v1)")
        if llama_models:
            lines.append(f"已加载模型 ({len(llama_models)} 个):")
            for m in llama_models:
                lines.append(f"  • {m}")
        else:
            lines.append("  尚未加载模型 (用 llama.cpp 的 --model 启动并加载)")
    else:
        lines.append("• llama.cpp 服务未检测到 (:8080/v1)")
        if status.get("llamacpp_error"):
            lines.append(f"  错误: {status['llamacpp_error']}")
    
    lines.append("")
    lines.append("推荐模型:")
    for m in status["recommended_models"]:
        lines.append(f"  • {m}")
    
    return "\n".join(lines)
