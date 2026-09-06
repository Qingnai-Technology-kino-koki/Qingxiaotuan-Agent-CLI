"""ACP 协议版本协商。

自研实现 —— ACP 协议版本协商。当前仅支持协议版本
整数 ``1``；``negotiate_version`` 返回 ≤ 客户端请求的最高支持版本（即永远是 1，
除非客户端请求低于最低版本，此时仍返回当前版本 1，交由客户端决定断开）。
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
