"""配置加载与组合视图。

Config 类负责把 内置默认 -> 用户配置 -> Profile -> 命令行 patch 四层叠加成统一视图;
并附带 home_dir / load_dotenv / deep_merge / patch_replace / dump_yaml 等辅助函数。
"""

from __future__ import annotations

import copy
import json
import logging
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional

log = logging.getLogger(__name__)

# 优先尝试导入 PyYAML; 若不可用 (隔离环境/未安装), 降级为内置极简解析器
_yaml: Any = None
try:
    import yaml as _yaml_mod
    _yaml = _yaml_mod
except ImportError:
    _yaml = None

from .defaults import DEFAULT_CONFIG, PRESET_PROFILES, load_builtin_soul


def home_dir() -> Path:
    override = os.environ.get("QXT_HOME")
    if override:
        return Path(override)
    return Path.home() / ".qingxiaotuan"


def load_dotenv(home: Optional[Path] = None) -> None:
    """把 ~/.qingxiaotuan/.env 中的键值加载进进程环境变量 (不覆盖已有变量)。

    密钥集中管理, 不进版本库。.env 权限应为 600。
    """
    env_path = (home or home_dir()) / ".env"
    if not env_path.exists():
        return
    try:
        text = env_path.read_text(encoding="utf-8")
    except OSError:
        return
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = val


def deep_merge(base: Dict[str, Any], overlay: Dict[str, Any]) -> Dict[str, Any]:
    """深合并: overlay 中的 dict 递归合并, 其他类型直接覆盖。"""
    out = copy.deepcopy(base)
    for key, value in overlay.items():
        if key in out and isinstance(out[key], dict) and isinstance(value, dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def patch_replace(base: Dict[str, Any], patch: Dict[str, Any], _prefix: str = "") -> Dict[str, Any]:
    """patch 语义: 以点分隔路径定位, 整值替换 (与 dsh --patch 一致)。

    patch 文件格式示例:
        model.model: deepseek-reasoner
        agent.max_iterations: 50
    """
    out = copy.deepcopy(base)
    for dotted_key, value in patch.items():
        keys = str(dotted_key).split(".")
        node = out
        for k in keys[:-1]:
            node = node.setdefault(k, {})
            if not isinstance(node, dict):
                raise ValueError(f"patch 路径冲突: {dotted_key}")
        node[keys[-1]] = value
    return out


def _chmod_600(path: Path) -> None:
    """把文件权限设为 600 (仅所有者可读写), 用于保存密钥相关文件。"""
    try:
        if os.name == "nt":
            # Windows 上 chmod 效果有限, 用 attrib 隐藏保护
            import subprocess
            subprocess.run(["attrib", "+H", str(path)], capture_output=True, timeout=5)
        else:
            os.chmod(path, 0o600)
    except Exception as exc:
        log.debug("设置配置文件权限失败 (%s): %s", path, exc)


# ---- 极简 YAML 子集解析器 (PyYAML 不可用时的降级) ----
def _mini_yaml_load(text: str) -> Dict[str, Any]:
    """极简 YAML 解析: 仅支持 键: 值 的简单扁平结构 (嵌套忽略, 用于降级)。"""
    result: Dict[str, Any] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, _, val = line.partition(":")
        key = key.strip()
        val = val.strip()
        if val.startswith("[") and val.endswith("]"):
            inner = val[1:-1].strip()
            result[key] = [x.strip().strip('"').strip("'") for x in inner.split(",") if x.strip()]
        elif val.lower() in ("true", "false"):
            result[key] = val.lower() == "true"
        elif val.lower() in ("null", "none", "~"):
            result[key] = None
        else:
            try:
                result[key] = int(val)
            except ValueError:
                try:
                    result[key] = float(val)
                except ValueError:
                    result[key] = val.strip('"').strip("'")
    return result


def _load_yaml(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
        if _yaml is not None:
            data = _yaml.safe_load(text)
        else:
            data = _mini_yaml_load(text)
        return data or {}
    except Exception:
        return {}


def dump_yaml(data: Dict[str, Any], path: Path) -> None:
    """写入 YAML 配置 (临时文件 + 原子替换, 崩溃不留下半截配置)。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd, temp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                if _yaml is not None:
                    _yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)
                else:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temp_name, path)
        finally:
            try:
                Path(temp_name).unlink()
            except FileNotFoundError:
                pass
    except Exception as exc:
        log.warning("配置写入失败 (%s): %s", path, exc)


def to_yaml_str(data: Dict[str, Any]) -> str:
    """把配置序列化为 YAML 字符串 (用于 qxt config dump 打印)。"""
    try:
        if _yaml is not None:
            return str(_yaml.safe_dump(data, allow_unicode=True, sort_keys=False))
        return json.dumps(data, ensure_ascii=False, indent=2)
    except Exception:
        return json.dumps(data, ensure_ascii=False, indent=2)


class Config:
    """组合后的配置视图。"""

    def __init__(self, profile: str = "default", patch_file: Optional[str] = None):
        self.profile = profile
        self.home = home_dir()
        self.user_config_path = self.home / "config.yaml"
        self.profile_dir = self.home / "profiles" / profile

        merged = copy.deepcopy(DEFAULT_CONFIG)
        # 预设 profile 作为基线 (用户可在 profiles/<name>/config.yaml 再覆盖)
        if profile in PRESET_PROFILES:
            merged = deep_merge(merged, PRESET_PROFILES[profile])
        merged = deep_merge(merged, _load_yaml(self.user_config_path))
        merged = deep_merge(merged, _load_yaml(self.profile_dir / "config.yaml"))
        if patch_file:
            merged = patch_replace(merged, _load_yaml(Path(patch_file)))
        self.data = merged

    # ------------------------------------------------------------ 访问

    def get(self, dotted: str, default: Any = None) -> Any:
        node: Any = self.data
        for key in dotted.split("."):
            if not isinstance(node, dict) or key not in node:
                return default
            node = node[key]
        return node

    def set_user(self, dotted: str, value: Any) -> None:
        """写入用户层配置文件 (自动把字符串值按目标类型转换)。"""
        value = self._coerce(value)
        cfg = _load_yaml(self.user_config_path)
        keys = dotted.split(".")
        node = cfg
        for k in keys[:-1]:
            node = node.setdefault(k, {})
        node[keys[-1]] = value
        self.home.mkdir(parents=True, exist_ok=True)
        dump_yaml(cfg, self.user_config_path)
        # 同步内存视图: 用 patch_replace 整值替换目标键 (保留同级默认字段)。
        self.data = patch_replace(self.data, {dotted: value})

    @staticmethod
    def _coerce(value: Any) -> Any:
        """尽力把用户/CLI 传入的字符串值转为 int/float/bool/JSON/原值。"""
        if not isinstance(value, str):
            return value
        low = value.strip().lower()
        if low in ("true", "false"):
            return low == "true"
        if low in ("null", "none", "~"):
            return None
        try:
            return int(value)
        except ValueError:
            pass
        try:
            return float(value)
        except ValueError:
            pass
        s = value.strip()
        if s and s[0] in ("{", "["):
            try:
                return json.loads(s)
            except (ValueError, json.JSONDecodeError):
                pass
        return value

    def api_key(self) -> Optional[str]:
        """按优先级解析密钥: 专用 env 名 -> 通用 QXT_API_KEY -> 用户配置中的引用。"""
        env_name = self.get("model.api_key_env", "DEEPSEEK_API_KEY")
        key = os.environ.get(env_name)
        if key:
            return key
        key = os.environ.get("QXT_API_KEY")
        if key:
            return key
        ref = self.get("model.api_key_ref")
        if ref:
            return os.environ.get(ref)
        return None

    def require_api_key(self) -> str:
        key = self.api_key()
        if not key:
            env_name = self.get("model.api_key_env", "DEEPSEEK_API_KEY")
            raise RuntimeError(
                f"未配置 API Key。请设置环境变量 {env_name} (推荐写入 ~/.qingxiaotuan/.env), "
                f"或运行 `qxt setup`。"
            )
        return key

    @property
    def mode(self) -> str:
        return str(self.get("mode.default", "standard"))

    @mode.setter
    def mode(self, value: str) -> None:
        if value not in ("standard", "yolo"):
            raise ValueError(f"未知运行模式: {value} (仅支持 standard / yolo)")
        self.set_user("mode.default", value)

    def is_yolo(self) -> bool:
        return self.mode == "yolo"

    def tool_auto_approve(self, tool_name: str) -> bool:
        if not self.is_yolo():
            return False
        redlist = self.get("mode.yolo_require_confirm", []) or []
        return tool_name not in redlist

    def ensure_home(self) -> None:
        """创建 ~/.qingxiaotuan 目录骨架 (Hermes 风格)。"""
        for sub in ["memories", "skills", "sessions", "logs", "cron", "profiles", "history"]:
            (self.home / sub).mkdir(parents=True, exist_ok=True)
        env_file = self.home / ".env"
        if not env_file.exists():
            env_file.write_text("# 密钥集中在此, 不要提交到版本库\n", encoding="utf-8")
        _chmod_600(env_file)
        soul = self.home / "SOUL.md"
        if not soul.exists():
            soul.write_text(load_builtin_soul(), encoding="utf-8")
        memory = self.home / "memories" / "MEMORY.md"
        if not memory.exists():
            memory.write_text("# MEMORY.md - 长期事实记忆\n\n(Agent 会把跨会话需要记住的事实写在这里)\n", encoding="utf-8")
        user = self.home / "memories" / "USER.md"
        if not user.exists():
            user.write_text("# USER.md - 关于用户\n\n- Name:\n- City:\n- Notes:\n", encoding="utf-8")

    def raw(self) -> dict:
        return self.data
