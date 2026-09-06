"""ACP 协议版本协商（融合层，复用自 kernel 的 ``kernel/acp/version.py``）。

原生 ACP server 此前不做版本协商；融合层在 ``fusion.acp_enhanced`` 开启时补上，
与遵循 opencode/Zed ACP 约定的客户端保持一致。当前仅支持协议版本整数 ``1``。
"""

from __future__ import annotations

# 当前服务器支持的协议版本（协商整数）。
CURRENT_VERSION = 1
# 支持版本集合（未来扩展时在此加入 2、3 …）。
SUPPORTED_VERSIONS = frozenset({1})
MIN_PROTOCOL_VERSION = 1


def negotiate_version(client_protocol_version: int) -> int:
    """与客户端协商协议版本。

    Args:
        client_protocol_version: 客户端在 ``initialize`` 中声明的 protocolVersion。

    Returns:
        服务器支持且不高于客户端请求的、最高的协议版本整数。当前恒为 ``1``。
    """
    if client_protocol_version < MIN_PROTOCOL_VERSION:
        return CURRENT_VERSION
    best = -1
    for ver in SUPPORTED_VERSIONS:
        if ver <= client_protocol_version and ver > best:
            best = ver
    return best if best >= 0 else CURRENT_VERSION
