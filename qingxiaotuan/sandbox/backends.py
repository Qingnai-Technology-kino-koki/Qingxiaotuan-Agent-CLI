"""隔离后端自动选择: docker → landlock(bwrap) → seatbelt → jobobject → local。

对比 TraeWork / CodeX 的单后端"硬切换":
  - CodeX sandbox*(bwrap/seatbelt/token-acl) 通常"选中一个就用它, 缺依赖即整体失效"。
  - 这里是"当前机器能拿到的最强隔离"按强度自动降序探测 —— 不会因为缺某一运行时
    就放弃隔离, 而是拿到可用的最强一个; 全部缺失时仍有 local 兜底(可用但无 OS 级隔离,
    由上层策略决定是否 fail-closed).
"""
from __future__ import annotations

import logging
import shutil
import subprocess
import sys
from typing import Dict, List, Tuple

log = logging.getLogger(__name__)


def _sh_ok(args: List[str], timeout: float = 5.0) -> bool:
    try:
        proc = subprocess.run(args, stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL, timeout=timeout)
        return proc.returncode == 0
    except Exception:  # pragma: no cover
        return False


def _docker_available() -> bool:
    return _sh_ok(["docker", "info"])


def _bwrap_available() -> bool:
    if not sys.platform.startswith("linux"):
        return False
    if shutil.which("bwrap") is None:
        return False
    try:
        return _sh_ok([shutil.which("bwrap"), "--ro-bind", "/", "/", "true"])
    except Exception:
        return False


def _seatbelt_available() -> bool:
    if sys.platform != "darwin":
        return False
    return shutil.which("sandbox-exec") is not None


def _jobobject_available() -> bool:
    return sys.platform == "win32"


def _token_acl_available() -> bool:
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False


# 强度降序: landlock(linux bwrap) → docker → seatbelt(mac) → token-acl/win → jobobject
_BACKENDS: List[Tuple[str, object]] = [
    ("landlock", _bwrap_available),
    ("docker", _docker_available),
    ("seatbelt", _seatbelt_available),
    ("token-acl", _token_acl_available),
    ("jobobject", _jobobject_available),
]


def detect_backends() -> List[Dict[str, object]]:
    """探测所有后端的可用性, 返回降序列表 (可靠, 不抛异常)。"""
    out: List[Dict[str, object]] = []
    for name, probe in _BACKENDS:
        try:
            ok = bool(probe())
        except Exception:  # pragma: no cover
            ok = False
        out.append({"name": name, "available": ok,
                    # WSL2 docker 在提供强隔离的同时可能较慢, 并入候选中
                    "detail": _probe_detail(name, ok)})
    out.append({"name": "local", "available": True, "detail": "本地执行(无 OS 级隔离, 兜底)"})
    return out


def _probe_detail(name: str, ok: bool) -> str:
    hint = {
        "landlock": "Linux bubblewrap/seccomp",
        "docker": "Docker 容器",
        "seatbelt": "macOS sandbox-exec",
        "token-acl": "Windows 受限令牌+ACL(需管理员)",
        "jobobject": "Windows Job Object(资源/进程隔离)",
    }.get(name, "")
    return f"{hint} {'可用' if ok else '不可用'}"


def pick_backend(prefer: str = "auto") -> Tuple[str, bool]:
    """选择要用的后端, 返回 (name, available)。

    - prefer == 'local' → 强制 local (总有兜底).
    - prefer 指定具体名 → 该后端可用则用, 否则回落到 auto 探测.
    - prefer == 'auto' 或其它 → 按强度降序返回首个可用; local 永远兜底.
    """
    if prefer == "local":
        return "local", True

    if prefer and prefer != "auto":
        # 显式指定: 必须存在且探测通过
        probe = dict(_BACKENDS).get(prefer)
        try:
            if probe is not None and probe():
                return prefer, True
        except Exception:  # pragma: no cover
            pass
        # 指定后端不可用 → 记录并回落 auto (fail-soft, 交给上层 enforce 策略)
        log.warning("指定的沙箱后端 %r 不可用, 回落到 auto", prefer)

    for name, probe in _BACKENDS:
        try:
            if probe():
                return name, True
        except Exception:  # pragma: no cover
            continue
    return "local", True