"""permission_fusion.py —— 敏感文件纵深防御: 原生 PermissionPolicy 的叠加层 (defense-in-depth)。

设计原则:
- 原生 ``PermissionPolicy`` 仍是唯一决策源, 本类只是「叠加层」, 绝不替换:
  * 原生 ``deny``   -> 原样返回 (绝不降级放行)
  * 原生 ``confirm`` -> 原样返回 (原生加固已足够)
  * 原生 ``allow``   -> 若工具为写/改类 (``not read_only``) 且任一参数命中敏感文件判定,
                        升级为 ``confirm`` (理由标注检测来源)
- 敏感文件检测始终生效, 无需配置开关。
"""

from __future__ import annotations

from typing import Any, Optional

from .permissions import PermissionDecision, PermissionPolicy


def _cfg_bool(cfg: Any, key: str, default: bool = False) -> bool:
    """读取 config 布尔键 (与 tools/base.py 同名 helper 语义一致)。"""
    if cfg is None:
        return default
    value = cfg.get(key, default)
    return default if value is None else bool(value)


class FusionPermissionPolicy:
    """叠加型权限策略: 包裹原生 PermissionPolicy, 追加 kernel 敏感文件检测。

    仅暴露与原生 ``PermissionPolicy.decide`` 兼容的接口, 可直接作为 ``ctx.permissions``
    或 ``PermissionPolicy`` 的替代品使用。
    """

    def __init__(self, delegate: PermissionPolicy, cfg: Any = None) -> None:
        self._delegate = delegate

    def decide(
        self, tool: Any, args: dict[str, Any], *, yolo: bool = False
    ) -> PermissionDecision:
        base = self._delegate.decide(tool, args, yolo=yolo)
        # 原生已拒绝/已要求确认: 尊重原生裁决, 不降级、不重复升级。
        if base.action in ("deny", "confirm"):
            return base
        # 仅对写/改类工具做敏感文件追加检测 (读类工具保持原生放行, 避免过问)。
        if getattr(tool, "read_only", False):
            return base
        sensitive = self._first_sensitive_path(args)
        if sensitive is not None:
            return PermissionDecision(
                "confirm",
                f"敏感文件访问 (融合 kernel 检测): 工具 {getattr(tool, 'name', '?')} "
                f"将触及敏感路径 {sensitive}，需人工确认",
            )
        return base

    @staticmethod
    def _first_sensitive_path(args: Any) -> Optional[str]:
        """在参数中递归查找首个命中 kernel 敏感文件判定 (basename 维度) 的字符串路径。"""
        # 惰性 import: 仅在开关开启且确实走到这里时引入 kernel 子树。
        from ..kernel.tools.args import is_sensitive_file

        def walk(value: Any) -> Optional[str]:
            if isinstance(value, str):
                if is_sensitive_file(value):
                    return value
            elif isinstance(value, dict):
                for sub in value.values():
                    found = walk(sub)
                    if found is not None:
                        return found
            elif isinstance(value, (list, tuple)):
                for sub in value:
                    found = walk(sub)
                    if found is not None:
                        return found
            return None

        if not isinstance(args, dict):
            return None
        return walk(args)


def build_permission_policy(cfg: Any) -> PermissionPolicy:
    """工具执行权限策略工厂。

    总是包裹 ``FusionPermissionPolicy`` 叠加敏感文件纵深检测;
    原生 PermissionPolicy 仍是唯一决策源, 叠加层仅做 defense-in-depth。
    """
    base = PermissionPolicy(cfg)
    return FusionPermissionPolicy(base, cfg)


__all__ = ["FusionPermissionPolicy", "build_permission_policy"]
