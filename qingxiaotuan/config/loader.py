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
def _parse_yaml_value(val: str) -> Any:
    """解析单个 YAML 值: 数字/布尔/null/列表/字符串。"""
    val = val.strip()
    if not val:
        return ""
    if val.startswith("[") and val.endswith("]"):
        inner = val[1:-1].strip()
        if not inner:
            return []
        return [_parse_yaml_value(x) for x in inner.split(",") if x.strip()]
    if val.lower() in ("true", "false"):
        return val.lower() == "true"
    if val.lower() in ("null", "none", "~"):
        return None
    try:
        return int(val)
    except ValueError:
        pass
    try:
        return float(val)
    except ValueError:
        pass
    return val.strip('"').strip("'")


def _mini_yaml_load(text: str) -> Dict[str, Any]:
    """极简 YAML 解析: 支持缩进嵌套 + 键值列表 (PyYAML 不可用时的降级)。"""
    lines = text.splitlines()
    root: Dict[str, Any] = {}
    # 用栈维护嵌套: [(indent_level, dict_or_list_ref, key_or_None)]
    # key_or_None: 如果当前节点是 dict 中某个 key 的值, 记录 key 名
    stack: list[tuple[int, Any, Optional[str]]] = [(-1, root, None)]

    for line in lines:
        stripped = line.lstrip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(line) - len(stripped)
        if " #" in stripped:
            stripped = stripped[:stripped.index(" #")].rstrip()

        # ---- YAML 列表项: "- value" 或 "- " ----
        if stripped.startswith("- ") or stripped == "-":
            item_val = stripped[2:].strip() if stripped.startswith("- ") else ""
            # 检查当前栈顶是否已经是 list (同级列表项直接追加)
            if stack and isinstance(stack[-1][1], list):
                stack[-1][1].append(_parse_yaml_value(item_val) if item_val else {})
                continue
            # 新列表的开始: 父节点应该是空 dict, 转换为 list
            current_key = stack[-1][2] if stack else None
            # 弹出到合适的父层级
            while len(stack) > 1 and stack[-1][0] >= indent:
                stack.pop()
            parent_node = stack[-1][1]
            if isinstance(parent_node, dict) and not parent_node and current_key:
                # 转换空 dict 为 list
                new_list: list = [_parse_yaml_value(item_val)] if item_val else []
                if len(stack) > 1:
                    grandparent = stack[-2][1]
                    if isinstance(grandparent, dict):
                        grandparent[current_key] = new_list
                stack[-1] = (indent, new_list, current_key)
            continue

        # ---- 普通键值对 ----
        if ":" not in stripped:
            continue
        key, _, val = stripped.partition(":")
        key = key.strip()
        val = val.strip()

        # 弹出不再属于当前层级的栈帧
        while len(stack) > 1 and stack[-1][0] >= indent:
            stack.pop()

        parent_node = stack[-1][1]

        if not val:
            # 子节点: 新建 dict 压栈
            child_d: Dict[str, Any] = {}
            if isinstance(parent_node, dict):
                parent_node[key] = child_d
            stack.append((indent, child_d, key))
        elif val.startswith("["):
            if isinstance(parent_node, dict):
                parent_node[key] = _parse_yaml_value(val)
        else:
            if isinstance(parent_node, dict):
                parent_node[key] = _parse_yaml_value(val)

    return root


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
        value = self._autofill_preset_fields(self._coerce(value))
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

    def delete_user(self, dotted: str) -> bool:
        """从用户层配置删除某个点路径键 (复位到内置默认值)。返回是否真的删掉了。

        仅作用于用户层 (~/.qingxiaotuan/config.yaml), 不影响 Profile/patch 层。
        """
        cfg = _load_yaml(self.user_config_path)
        keys = dotted.split(".")
        node = cfg
        for k in keys[:-1]:
            if not isinstance(node, dict) or k not in node:
                return False
            node = node[k]
        leaf = keys[-1]
        if not isinstance(node, dict) or leaf not in node:
            return False
        del node[leaf]
        self.home.mkdir(parents=True, exist_ok=True)
        dump_yaml(cfg, self.user_config_path)
        # 刷新内存视图: 重建 默认 -> 用户(已删) -> Profile。
        self.data = copy.deepcopy(DEFAULT_CONFIG)
        if self.profile in PRESET_PROFILES:
            self.data = deep_merge(self.data, PRESET_PROFILES[self.profile])
        self.data = deep_merge(self.data, cfg)
        self.data = deep_merge(self.data, _load_yaml(self.profile_dir / "config.yaml"))
        return True

    @staticmethod
    def _autofill_preset_fields(value: Any) -> Any:
        """dict 且带已知 provider 时, 从对应预设补全缺失字段。

        典型场景: `qxt config set model.worker '{"provider":"opencode-zen"}'` ——
        只写 provider 就能自动带好 base_url / api_key_env / model 等连接信息,
        与 provider_catalog / swarm worker 的补全语义保持一致。
        """
        if isinstance(value, dict) and isinstance(value.get("provider"), str):
            preset = PRESET_PROFILES.get(value["provider"])
            if preset and isinstance(preset.get("model"), dict):
                return deep_merge(preset["model"], value)
        return value

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
