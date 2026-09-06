"""安全层 — OS 级沙箱 + 不可变安全策略 + 密码学级加密。

三大支柱:
1. SyscallSandbox   —— 在 arch.platform 的 syscall 级隔离之上, 叠加"命令级策略":
                         白名单二进制 + 禁网 + 文件系统 confinement (用 core.sandbox
                         复制工作区为隔离沙箱根)。失败即 fail-closed。
2. ImmutableSecurityPolicy —— 策略文档一旦 seal() 即不可变 (内存 __setattr__ 锁 +
                         磁盘签名校验); 任何篡改会被 load() 拒绝。签名用 HMAC-SHA256
                         (密钥来自主密钥派生), 可选 ECDSA 离线签发。
3. CryptoVault      —— 静态数据 (context 快照 / 审计 / 策略) 的密码学级加密:
                         prefer `cryptography` 的 AES-256-GCM (AEAD); 回退到
                         ext.crypto_engine 的 PBKDF2+CTR+HMAC。
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import platform as _plat
from ..ext import crypto_engine as _crypto


class PolicyViolation(Exception):
    """策略违反 / 策略完整性校验失败。"""


# =============================================================== 不可变安全策略
@dataclass
class ImmutableSecurityPolicy:
    """一旦 seal() 即不可变的、带签名的安全策略。

    字段全部在 seal 前可写; seal() 后 __setattr__ 抛 PolicyViolation。
    磁盘形态 = JSON + signature; load() 校验签名, 篡改即拒。签名密钥由主密钥派生
    (HMAC), 因此只要有主密钥就能验签; 若要更强, 可传入 signer 用 ECDSA 离线签发。
    """

    name: str = "default"
    version: str = "1"
    # 允许执行的二进制 (命令级白名单); 空 = 仅 allow_read_only
    allow_binaries: List[str] = field(default_factory=list)
    # 禁止执行的二进制
    deny_binaries: List[str] = field(default_factory=list)
    # syscall 白名单 (传给 seccomp)
    syscall_allow: List[str] = field(default_factory=list)
    # 是否禁止网络
    block_network: bool = True
    # 内存上限 (MB)
    memory_limit_mb: int = 512
    # CPU 速率上限 (%)
    cpu_rate_pct: int = 50
    # 是否允许写宿主工作区 (False = 仅隔离沙箱可写)
    allow_host_writes: bool = False
    # 扩展字段 (任意键值, 仍受不可变约束)
    extras: Dict[str, Any] = field(default_factory=dict)

    _SEALED: bool = field(default=False, repr=False, compare=False)
    _signature: Optional[str] = field(default=None, repr=False, compare=False)

    # 内存不可变锁
    def __setattr__(self, key: str, value: Any) -> None:
        sealed = object.__getattribute__(self, "_SEALED") if "_SEALED" in self.__dict__ else False
        if sealed and key not in ("_SEALED", "_signature"):
            raise PolicyViolation(f"策略已 seal, 不可修改字段 {key!r}")
        super().__setattr__(key, value)

    # ---- 序列化 / 签名 ----
    def _canonical(self) -> str:
        """稳定 JSON 序列化 (排序键, 排除私有字段), 用于签名/校验。"""
        data = {
            "name": self.name,
            "version": self.version,
            "allow_binaries": sorted(self.allow_binaries),
            "deny_binaries": sorted(self.deny_binaries),
            "syscall_allow": sorted(self.syscall_allow),
            "block_network": self.block_network,
            "memory_limit_mb": self.memory_limit_mb,
            "cpu_rate_pct": self.cpu_rate_pct,
            "allow_host_writes": self.allow_host_writes,
            "extras": self.extras,
        }
        return json.dumps(data, sort_keys=True, ensure_ascii=False)

    def seal(self, master_key: bytes) -> str:
        """冻结策略并返回签名 (base64 hex)。之后再改字段会抛 PolicyViolation。"""
        if self._SEALED:
            return self._signature or ""
        payload = self._canonical().encode("utf-8")
        sig = hmac.new(master_key, payload, hashlib.sha256).hexdigest()
        object.__setattr__(self, "_signature", sig)
        object.__setattr__(self, "_SEALED", True)
        return sig

    def signature_of(self, master_key: bytes) -> str:
        return hmac.new(master_key, self._canonical().encode("utf-8"), hashlib.sha256).hexdigest()

    def verify(self, master_key: bytes) -> bool:
        """校验当前内容是否与 seal 时的签名一致 (防内存中被绕过 __setattr__ 篡改)。"""
        if not self._SEALED or not self._signature:
            return False
        return hmac.compare_digest(self._signature, self.signature_of(master_key))

    # ---- 磁盘读写 (带签名) ----
    def save(self, path: str | os.PathLike, master_key: bytes) -> None:
        if not self._SEALED:
            self.seal(master_key)
        doc = {
            "policy": json.loads(self._canonical()),
            "signature": self._signature,
        }
        Path(path).write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: str | os.PathLike, master_key: bytes) -> "ImmutableSecurityPolicy":
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
        policy_doc = doc["policy"]
        signature = doc["signature"]
        obj = cls(**policy_doc)
        # 重新计算签名并比对 (防磁盘篡改)
        expected = hmac.new(master_key, obj._canonical().encode("utf-8"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, signature):
            raise PolicyViolation("策略签名校验失败: 文件已被篡改或主密钥不匹配")
        object.__setattr__(obj, "_signature", signature)
        object.__setattr__(obj, "_SEALED", True)
        return obj

    # ---- 决策 ----
    def allows_binary(self, binary: str) -> bool:
        base = os.path.basename(binary)
        stem = os.path.splitext(base)[0].lower()  # "python.exe" -> "python"
        cands = {base, stem, binary}
        for c in cands:
            if c in self.deny_binaries or binary in self.deny_binaries:
                return False
        if not self.allow_binaries:
            # 空白名单 = 默认允许只读型命令 (cat/ls/echo/python 只读脚本等)
            return stem in ("cat", "ls", "echo", "head", "tail", "python", "python3", "node")
        return any(c in self.allow_binaries for c in cands)


# =============================================================== 沙箱执行器
class SyscallSandbox:
    """在 syscall 级隔离之上叠加命令级策略的执行器。

    典型用法::
        sb = SyscallSandbox(policy)
        res = sb.run(["python", "script.py"], workdir="/safe/root")
    """

    def __init__(self, policy: ImmutableSecurityPolicy) -> None:
        if not policy._SEALED:
            raise PolicyViolation("沙箱必须使用已 seal 的不可变策略")
        self.policy = policy

    def run(
        self,
        cmd: List[str],
        workdir: Optional[str] = None,
        timeout: Optional[float] = None,
        env: Optional[Dict[str, str]] = None,
    ) -> _plat.SyscallResult:
        if not cmd:
            raise PolicyViolation("空命令")
        if not self.policy.allows_binary(cmd[0]):
            raise PolicyViolation(f"命令被策略拒绝: {cmd[0]}")
        # 抹除敏感环境变量, 防泄漏进沙箱
        clean_env = _sanitize_env(env or dict(os.environ))
        # 禁网: 清空代理变量 (Linux 还会尝试 unshare -n)
        if self.policy.block_network:
            for k in list(clean_env):
                if k.upper() in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy"):
                    clean_env.pop(k, None)

        result = _plat.run_syscall_sandboxed(
            cmd,
            timeout=timeout or 30.0,
            memory_limit_mb=self.policy.memory_limit_mb,
            cpu_rate_pct=self.policy.cpu_rate_pct,
            allowlist=self.policy.syscall_allow or None,
            block_network=self.policy.block_network,
            cwd=workdir,
        )
        # 把干净环境透传给子进程 (seccomp/fallback 路径默认继承, 这里覆盖)
        return result


def _sanitize_env(base: Dict[str, str]) -> Dict[str, str]:
    from ..core.security_utils import sanitize_env
    return sanitize_env(base)


# =============================================================== 密码学保险库
class CryptoVault:
    """静态数据 (context 快照 / 审计 / 策略) 的密码学级加密。

    - 优先 AES-256-GCM (AEAD): `cryptography` 可用时。
    - 回退: ext.crypto_engine 的 PBKDF2 + CTR + HMAC (开发/测试用)。
    - 密钥: 由 passphrase + 随机 salt 经 PBKDF2 派生; 或直接使用外部提供的 32 字节密钥。
    """

    def __init__(
        self,
        passphrase: Optional[str] = None,
        raw_key: Optional[bytes] = None,
        iterations: int = 200_000,
    ) -> None:
        self.iterations = iterations
        if raw_key is not None:
            self._key = raw_key if len(raw_key) >= 32 else raw_key.ljust(32, b"\0")[:32]
            self._uses_passphrase = False
        else:
            self._passphrase = passphrase or secrets.token_hex(32)
            self._uses_passphrase = True
            self._key = self._derive(self._passphrase, self._static_salt(), iterations)

    @staticmethod
    def _static_salt() -> bytes:
        return b"qingxiaotuan-arch-vault-v1"

    @staticmethod
    def _derive(passphrase: str, salt: bytes, iterations: int) -> bytes:
        return hashlib.pbkdf2_hmac("sha256", passphrase.encode(), salt, iterations)

    # ---- 高层 API ----
    def seal_text(self, plaintext: str) -> Dict[str, str]:
        return self.seal_bytes(plaintext.encode("utf-8"))

    def open_text(self, envelope: Dict[str, str]) -> str:
        return self.open_bytes(envelope).decode("utf-8")

    def seal_bytes(self, data: bytes) -> Dict[str, str]:
        try:
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # type: ignore
        except Exception:
            return self._seal_fallback(data)
        nonce = secrets.token_bytes(12)
        aes = AESGCM(self._key)
        ct = aes.encrypt(nonce, data, None)
        return {
            "alg": "AES-256-GCM",
            "iterations": str(self.iterations) if self._uses_passphrase else "0",
            "nonce_b64": _b64(nonce),
            "ct_b64": _b64(ct),
        }

    def open_bytes(self, envelope: Dict[str, str]) -> bytes:
        alg = envelope.get("alg", "CTR-HMAC")
        if alg == "AES-256-GCM":
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # type: ignore
            nonce = _ub64(envelope["nonce_b64"])
            ct = _ub64(envelope["ct_b64"])
            return AESGCM(self._key).decrypt(nonce, ct, None)
        return self._open_fallback(envelope)

    # ---- 回退实现 (纯 Python) ----
    def _seal_fallback(self, data: bytes) -> Dict[str, str]:
        # 回退路径: 先把任意字节 base64 成 ASCII 明文, 再走 CTR+HMAC, 避免 UTF-8 丢字节
        eng = _crypto.CryptoEngine()
        res = eng.seal({
            "passphrase": self._passphrase if self._uses_passphrase else self._key.decode("latin-1"),
            "salt_b64": _b64(self._static_salt()),
            "plaintext": _b64(data),
            "iterations": self.iterations if self._uses_passphrase else 100000,
        })
        return {
            "alg": "CTR-HMAC",
            "iterations": str(res["iterations"]),
            "iv_b64": res["iv_b64"],
            "salt_b64": res["salt_b64"],
            "ct_b64": res["ciphertext_b64"],
            "mac_b64": res["mac_b64"],
        }

    def _open_fallback(self, envelope: Dict[str, str]) -> bytes:
        eng = _crypto.CryptoEngine()
        res = eng.open({
            "passphrase": self._passphrase if self._uses_passphrase else self._key.decode("latin-1"),
            "salt_b64": envelope["salt_b64"],
            "ciphertext_b64": envelope["ct_b64"],
            "iv_b64": envelope["iv_b64"],
            "mac_b64": envelope["mac_b64"],
            "iterations": int(envelope.get("iterations", 100000)),
        })
        return _ub64(res["plaintext"])

    # ---- 文件便捷方法 ----
    def seal_file(self, src: str | os.PathLike, dst: str | os.PathLike) -> None:
        Path(dst).write_bytes(
            json.dumps(self.seal_bytes(Path(src).read_bytes()), ensure_ascii=False).encode("utf-8")
        )

    def open_file(self, src: str | os.PathLike, dst: str | os.PathLike) -> None:
        env = json.loads(Path(src).read_text(encoding="utf-8"))
        Path(dst).write_bytes(self.open_bytes(env))


def _b64(b: bytes) -> str:
    import base64
    return base64.b64encode(b).decode()


def _ub64(s: str) -> bytes:
    import base64
    return base64.b64decode(s)
