"""安全加固工具集 (qingxiaotuan.harden)。

集中提供可独立测试、可在 CI 中稳定运行的安全加固模块:

- ``crypto_provider`` : 可插拔密码学后端 (Software / Gmssl 国密 / Hsm PKCS#11 预留)
- ``audit_export``     : 安全事件标准化导出 (CEF / JSONL / RFC5424 Syslog) + 可选 HTTP 推送
- ``network_policy``  : 网络出口加固 (CIDR 出口白名单 + 单向模式)
- ``sbom``            : SPDX 2.3 软件物料清单生成
- ``repro_build``     : 可复现构建锁 (版本钉死 + 漂移检测)

这些模块**不**声称"国防级/军工认证" —— 那需要国密认证、HSM 实体、等保资质等代码之外的门槛。
它们做的是: 把代码层「可对接、可验证、可审计」的安全能力做实。
"""
from __future__ import annotations

from .crypto_provider import (
    CryptoProvider,
    SoftwareProvider,
    GmsslProvider,
    HsmProvider,
    get_crypto_provider,
    available_providers,
)
from .audit_export import AuditExporter, format_event, to_cef, to_jsonl, to_syslog
from .network_policy import EgressRestrictedNetworkGuard
from .sbom import generate_sbom, write_sbom, sbom_hash
from .repro_build import generate_lock, verify_lock

__all__ = [
    "CryptoProvider", "SoftwareProvider", "GmsslProvider", "HsmProvider",
    "get_crypto_provider", "available_providers",
    "AuditExporter", "format_event", "to_cef", "to_jsonl", "to_syslog",
    "EgressRestrictedNetworkGuard",
    "generate_sbom", "write_sbom", "sbom_hash",
    "generate_lock", "verify_lock",
]
