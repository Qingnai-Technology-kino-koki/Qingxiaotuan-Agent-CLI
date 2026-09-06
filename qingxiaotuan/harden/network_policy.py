"""网络出口加固层 (Egress-restricted Network Guard)。

核心能力 (CIDR 出口白名单 + 单向模式) 已下沉到 ``core.network_guard.NetworkGuard``,
``tools.shell`` 执行路径通过全局单例直接生效。本模块保留
``EgressRestrictedNetworkGuard`` 作为语义化别名 / CLI 便捷封装, 并补充策略描述接口,
便于 ``qxt harden network-check`` 调用与审计展示。

行为契约 (与 core 一致):
1. **CIDR 出口白名单** (``egress_cidr_allow``):
   命令中出现显式 IP 字面量且不在白名单 CIDR 内一律 deny (防数据外泄);
   仅出现域名时降级为 confirm, 避免无 DNS 解析的离线误判; 未配置则与原 NetworkGuard 行为一致。
2. **单向模式** (``one_way_mode``):
   禁止入站监听类命令 (nc -l / socat LISTEN / python -m http.server / ssh -D / sshd 等),
   适配气隙/光闸场景 —— 只允许向外发起连接, 不允许本机开端口接收入站。

纯标准库, 不引入新依赖。
"""
from __future__ import annotations

from typing import Iterable, Optional

from ..core.network_guard import NetworkGuard


class EgressRestrictedNetworkGuard(NetworkGuard):
    """带 CIDR 出口白名单与单向模式的网络守卫 (core.NetworkGuard 薄封装)。"""

    def __init__(
        self,
        egress_cidr_allow: Optional[Iterable[str]] = None,
        one_way_mode: bool = False,
        **kwargs,
    ) -> None:
        # 全部出口加固逻辑由 core.NetworkGuard 承担, 此处仅透传参数
        super().__init__(
            egress_cidr_allow=egress_cidr_allow,
            one_way_mode=one_way_mode,
            **kwargs,
        )

    # ---------------------------------------------------------- 策略描述
    def describe_policy(self) -> dict:
        """返回当前策略的可读摘要 (供 CLI / 审计展示)。"""
        return {
            "egress_restricted": getattr(self, "_egress_restricted", False),
            "egress_cidr_allow": [str(n) for n in getattr(self, "_egress_cidrs", [])],
            "one_way_mode": getattr(self, "_one_way_mode", False),
            "allowed_domains": len(getattr(self, "_allowed_domains", set())),
            "blocked_domains": len(getattr(self, "_blocked_domains", set())),
        }
