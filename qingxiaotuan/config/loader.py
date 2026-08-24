"""配置加载与组合视图。

Config 类负责把 内置默认 -> 用户配置 -> Profile -> 命令行 patch 四层叠加成统一视图;
并附带 home_dir / load_dotenv / deep_merge / patch_replace / dump_yaml 等辅助函数。
"""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

import yaml

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


def _load_yaml(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data or {}


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
        with open(self.user_config_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(cfg, f, allow_unicode=True, sort_keys=False)
        # 同步内存视图: 用 patch_replace 整值替换目标键 (保留同级默认字段)。
        # 整值替换确保嵌套配置 (如 model.planner) 改写时不会与内存残留旧子字段深合并,
        # 同时又不丢同级的 model.provider 等默认项。
        self.data = patch_replace(self.data, {dotted: value})

    @staticmethod
    def _coerce(value: Any) -> Any:
        """尽力把用户/CLI 传入的字符串值转为 int/float/bool/JSON/原值。

        支持:
        - 布尔: true/false
        - 空值: null/none/~
        - 整数 / 浮点
        - JSON 字符串 (dict/list): 例如 '{"provider":"opencode-zen"}' 会解析为对象,
          使 `qxt config set model.worker '{"provider":"opencode-zen"}'` 可用
        - 其他: 原样字符串
        """
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
        # 尝试 JSON (支持嵌套 dict/list 配置, 如 model.worker)
        s = value.strip()
        if s and s[0] in ("{", "["):
            try:
                return json.loads(s)
            except (ValueError, json.JSONDecodeError):
                pass
        return value

    def api_key(self) -> Optional[str]:
        """按优先级解析密钥: 专用 env 名 -> 通用 QXT_API_KEY -> 用户配置中的引用。

        注意: 密钥永远从环境变量读取, 不写入 config.yaml 明文。
        """
        env_name = self.get("model.api_key_env", "DEEPSEEK_API_KEY")
        key = os.environ.get(env_name)
        if key:
            return key
        # 兼容历史/通用变量
        key = os.environ.get("QXT_API_KEY")
        if key:
            return key
        # 允许 config 中以 ${ENV_VAR} 形式引用 (仅当配置了 api_key_ref)
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

    # ------------------------------------------------------------ 运行模式

    @property
    def mode(self) -> str:
        """当前运行模式: standard 或 yolo。"""
        return self.get("mode.default", "standard")

    @mode.setter
    def mode(self, value: str) -> None:
        if value not in ("standard", "yolo"):
            raise ValueError(f"未知运行模式: {value} (仅支持 standard / yolo)")
        self.set_user("mode.default", value)

    def is_yolo(self) -> bool:
        return self.mode == "yolo"

    def tool_auto_approve(self, tool_name: str) -> bool:
        """判断某危险工具是否应自动批准 (无需询问用户)。

        - standard 模式: 永远不自动批准, 交给 confirm 回调处理。
        - yolo 模式: 默认全部自动批准, 除非该工具在 mode.yolo_require_confirm 红名单中。
        """
        if not self.is_yolo():
            return False
        redlist = self.get("mode.yolo_require_confirm", []) or []
        return tool_name not in redlist

    # ------------------------------------------------------------ 初始化

    def ensure_home(self) -> None:
        """创建 ~/.qingxiaotuan 目录骨架 (Hermes 风格)。"""
        for sub in ["memories", "skills", "sessions", "logs", "cron", "profiles", "history"]:
            (self.home / sub).mkdir(parents=True, exist_ok=True)
        # .env 默认不存在则创建一个空文件并锁权限 (仅本人可读写)
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


def dump_yaml(data: Dict[str, Any]) -> str:
    return yaml.safe_dump(data, allow_unicode=True, sort_keys=False)


def _chmod_600(path: Path) -> None:
    """在 POSIX 上把文件权限设为 600 (仅属主可读写)。Windows 忽略。"""
    if os.name != "posix":
        return
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
