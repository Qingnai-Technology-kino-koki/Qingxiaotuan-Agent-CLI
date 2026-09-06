"""轻量配置管理命令 —— cmd_config / _validate_config。

拆分自 cmd_chat (原 3105 行)。本模块不导入 app 链:
`qxt config get/set/dump/validate` 直接构造 Config 对象即可, 无需构建整个 kernel,
让配置类命令保持毫秒级启动。
"""

from __future__ import annotations

import json

from ..config import DEFAULT_CONFIG, Config, to_yaml_str
from ..ui.plain_console import console


def _validate_config(config) -> list[tuple]:
    """校验配置, 返回 [(level, section, msg)]; level ∈ ok/warn/err。"""
    from ..models import is_known_provider  # 惰性: 仅 validate 需要, 别拖慢 get/dump/set
    issues: list[tuple] = []

    def ok(section: str, msg: str) -> None:
        issues.append(("ok", section, msg))

    def warn(section: str, msg: str) -> None:
        issues.append(("warn", section, msg))

    def err(section: str, msg: str) -> None:
        issues.append(("err", section, msg))

    provider = config.get("model.provider", "")
    model = config.get("model.model", "")
    base_url = config.get("model.base_url", "")
    if not provider:
        err("model.provider", "未设置供应商")
    elif is_known_provider(provider):
        ok("model.provider", f"{provider} (已知供应商)")
    elif base_url:
        warn("model.provider", f"{provider} 为自定义网关, 已提供 base_url")
    else:
        err("model.provider", f"{provider} 不是内置供应商且未配置 base_url")

    if not model:
        err("model.model", "未设置模型名")
    else:
        ok("model.model", model)

    if base_url:
        if base_url.startswith(("http://", "https://")):
            ok("model.base_url", base_url)
        else:
            err("model.base_url", f"不是合法 URL: {base_url}")
    else:
        warn("model.base_url", "未显式设置 (将使用供应商预设)")

    temp = config.get("model.temperature", 0.7)
    if isinstance(temp, (int, float)) and 0 <= temp <= 2:
        ok("model.temperature", f"{temp} (范围 0~2)")
    else:
        err("model.temperature", f"应为 0~2 的数字, 当前: {temp!r}")

    mt = config.get("model.max_tokens", 0)
    if isinstance(mt, int) and mt > 0:
        ok("model.max_tokens", str(mt))
    else:
        err("model.max_tokens", f"应为正整数, 当前: {mt!r}")

    env_name = config.get("model.api_key_env", "DEEPSEEK_API_KEY")
    if config.api_key():
        ok("API Key", f"已配置 ({env_name})")
    else:
        err("API Key", f"未配置密钥 (期望环境变量 {env_name})")

    mode = config.get("mode.default", "standard")
    if mode in ("standard", "yolo"):
        ok("mode.default", mode)
    else:
        err("mode.default", f"应为 standard/yolo, 当前: {mode!r}")

    effort = config.get("agent.effort", "high")
    if effort in ("low", "medium", "high"):
        ok("agent.effort", effort)
    else:
        err("agent.effort", f"应为 low/medium/high, 当前: {effort!r}")

    mi = config.get("agent.max_iterations", 0)
    if isinstance(mi, int) and mi > 0:
        ok("agent.max_iterations", str(mi))
    else:
        err("agent.max_iterations", f"应为正整数, 当前: {mi!r}")

    budget = config.get("context.budget_tokens", 0)
    if isinstance(budget, (int, float)) and budget > 0:
        ok("context.budget_tokens", f"{budget:,}")
    else:
        err("context.budget_tokens", f"应为正数, 当前: {budget!r}")

    return issues


def cmd_config(args) -> int:
    """配置管理。"""
    config_cmd = getattr(args, "config_cmd", None)
    config = Config(profile=getattr(args, "profile", "default"),
                    patch_file=getattr(args, "patch", None))
    if config_cmd == "dump":
        print(to_yaml_str(config.data))
    elif config_cmd == "dump-default":
        print(to_yaml_str(DEFAULT_CONFIG))
    elif config_cmd == "get":
        key = getattr(args, "key", "")
        console.print(f"  {key} = {config.get(key)}")
    elif config_cmd == "set":
        key = getattr(args, "key", "")
        value = getattr(args, "value", "")
        try:
            value = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            pass
        config.set_user(key, value)
        console.print(f"{key} = {value}")
    elif config_cmd == "validate":
        issues = _validate_config(config)
        console.print("配置校验")
        errs = warns = 0
        for level, section, msg in issues:
            if level == "ok":
                console.print(f"  ✓ {section:<22s} {msg}")
            elif level == "warn":
                warns += 1
                console.print(f"  ! {section:<22s} {msg}")
            else:
                errs += 1
                console.print(f"  ✗ {section:<22s} {msg}")
        if errs or warns:
            console.print(f"\n结果: {errs} 错误, {warns} 警告")
            return 1 if errs else 0
        console.print("\n结果: 全部通过 ✓")
        return 0
    elif config_cmd == "profiles":
        console.print("可用 profiles: default")
    else:
        console.print("用法: qxt config dump|get|set|validate|profiles")
    return 0
