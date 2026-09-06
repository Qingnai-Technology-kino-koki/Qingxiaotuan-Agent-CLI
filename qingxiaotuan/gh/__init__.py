"""GitHub 原生绑定与安全沙箱。

`qxt gh` 优先直接调用本机 `gh` CLI (原生体验, 同样的输出/退出码/流式),
再在其上叠加一层**不可绕过的安全分级**: 读取与本地克隆放行, 远端状态变更受保护,
一切破坏性操作 (删除仓库/注销账户/任意 `-X DELETE`) 一律硬性拒绝 ——
即便用户显式传入 `--yes` / `--force` / `-D` 也无法绕过。
"""

from .policy import (
    Action,
    classify,
    SafetyPolicy,
    load_policy,
    FORBIDDEN_HINT,
)
from .native import NativeGH, GHNotInstalledError

__all__ = [
    "Action", "classify", "SafetyPolicy", "load_policy", "FORBIDDEN_HINT",
    "NativeGH", "GHNotInstalledError",
]