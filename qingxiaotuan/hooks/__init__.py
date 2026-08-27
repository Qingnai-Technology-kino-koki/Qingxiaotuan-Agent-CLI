"""用户级 Hooks —— 让用户在 Agent 工具执行前/后挂载自己的脚本。

对标 Claude Code 的 Hooks 模型, 但更克制、更贴合青小团的"最小影响半径"哲学:
- 命令强制 list(argv) 形式, 绝不接受裸 shell 字符串 → 杜绝 `; rm -rf` 类注入。
- 超时强杀, 单个 hook 异常不影响主流程 (fail-safe, 不阻断)。
- PreToolUse 的"阻断权"与"改参权"默认需显式声明, 不静默赋予。
- 所有 hook 调用写入审计事件 (kernel.emit("hook.executed"))。
"""

from .manager import HookManager, HookDecision, HOOK_EVENTS

__all__ = ["HookManager", "HookDecision", "HOOK_EVENTS"]
