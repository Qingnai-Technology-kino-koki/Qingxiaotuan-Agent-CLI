"""codedev —— 代码开发子系统（对标 Claude Code 的底层能力, 非 loop）。

公开 API：
    - CodeIndex / RetrievalResult / Symbol        （确定性检索）
    - Verifier / VerificationReport / Diagnostic   （确定性验证）
    - Decomposer / DevSpec / Subtask               （规格驱动分解）
    - CodeDevEngine / DevelopResult                （编排核心, 非循环）
    - CodeDevPlugin                                （内核服务 + 工具注册）
"""

from .retrieval import CodeIndex, RetrievalResult, Symbol, RetrievalHit
from .verify import Verifier, VerificationReport, Diagnostic
from .decompose import Decomposer, DevSpec, Subtask
from .engine import CodeDevEngine, DevelopResult
from .plugin import CodeDevPlugin

__all__ = [
    "CodeIndex", "RetrievalResult", "Symbol", "RetrievalHit",
    "Verifier", "VerificationReport", "Diagnostic",
    "Decomposer", "DevSpec", "Subtask",
    "CodeDevEngine", "DevelopResult",
    "CodeDevPlugin",
]
