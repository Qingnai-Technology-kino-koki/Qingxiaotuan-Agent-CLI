"""共享安全工具 —— 环境变量脱敏等跨模块复用的安全原语。

消除 core/sandbox.py + core/sandbox_provider.py + arch/security.py +
tools/mcp/sandbox.py 中重复的 _sanitize_env / SECRET_HINTS 定义。
"""

from __future__ import annotations

import os
from typing import Dict, Optional


# 敏感环境变量名片段 (统一来源, 所有沙箱/安全模块从此处导入)
SECRET_HINTS: tuple = (
    "API_KEY", "SECRET", "TOKEN", "PASSWORD", "PASSWD", "PRIVATE_KEY",
    "ACCESS_KEY", "CREDENTIAL", "AUTH",
    # 项目前缀
    "QXT_",
    # 主流 Provider
    "OPENAI", "ANTHROPIC", "DEEPSEEK", "MOONSHOT", "DASHSCOPE",
    "ZHIPU", "GROQ", "SILICONFLOW",
    # 云平台
    "AWS_", "GCP", "AZURE", "DATABASE_URL",
)


def sanitize_env(
    base: Optional[Dict[str, str]] = None,
    extra: Optional[Dict[str, str]] = None,
) -> Dict[str, str]:
    """拷贝并抹除敏感环境变量, 防止密钥泄漏到沙箱子进程。

    - base: 源环境 (None = os.environ)
    - extra: 额外注入的非敏感变量 (不会被抹除)

    返回脱敏后的环境变量字典。
    """
    env = dict(base if base is not None else os.environ)
    if extra:
        env.update(extra)
    return {
        k: ("" if any(h in k.upper() for h in SECRET_HINTS) else v)
        for k, v in env.items()
    }
