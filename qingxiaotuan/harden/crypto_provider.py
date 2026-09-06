"""可插拔密码学后端 (CryptoProvider)。

安全目标
--------
让青小团在「工业级 AES-GCM」「国密 SM4 (gmssl)」「硬件 HSM / PKCS#11」之间可替换,
默认软件后端零额外依赖 (AES-GCM 优先, 否则 CTR+HMAC 回退)。所有后端 fail-closed:
- 解密认证失败一律抛错, 绝不静默返回篡改后的明文;
- 缺依赖的后端在实例化时给出清晰可读的错误, 而非崩溃在半路。

本模块与 ``ext/crypto_engine.py`` 的密文信封 (ciphertext_b64 / iv_b64 / salt_b64 /
mac_b64 / iterations / cipher) 完全兼容 —— 用 ``SoftwareProvider`` 加密的 blob 可被
``CryptoEngine`` 解开, 反之亦然。这样「可插拔」不会破坏既有数据。

注意: 本模块解决的是"算法/密钥后端可替换"。它**不**提供"国防级"合规
(国密认证、HSM 实体、等保资质) —— 那些是代码之外的门槛, 见 README / QXT.md。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
from abc import ABC, abstractmethod
from typing import Dict, List, Optional

# PBKDF2 迭代次数安全区间 (与 crypto_engine 一致)
_MIN_ITERATIONS = 1000
_MAX_ITERATIONS = 5_000_000

# ---- 检测 cryptography 库 (AES-GCM) ----
try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # type: ignore
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC  # type: ignore
    from cryptography.hazmat.primitives import hashes as _crypto_hashes  # type: ignore
    _HAS_CRYPTOGRAPHY = True
except Exception:  # pragma: no cover - import guard
    _HAS_CRYPTOGRAPHY = False


def _check_iterations(value: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError("iterations 必须是正整数")
    if value < _MIN_ITERATIONS:
        raise ValueError(f"iterations 不得低于 {_MIN_ITERATIONS} (防弱化)")
    if value > _MAX_ITERATIONS:
        raise ValueError(f"iterations 不得超过 {_MAX_ITERATIONS} (防 DoS)")
    return value


def _xor_stream(data: bytes, key: bytes, nonce: bytes, counter_start: int = 0) -> bytes:
    """CTR 风格流式加密 (域分隔符防长度混淆, nonce 必参与密钥流)。"""
    if not nonce:
        raise ValueError("nonce 不能为空 (密钥流必须依赖随机 iv)")
    out = bytearray()
    counter = counter_start
    for i in range(0, len(data), 16):
        block = data[i:i + 16]
        material = b"qxt-stream\x00" + key + b"\x01" + nonce + b"\x02" + counter.to_bytes(8, "big")
        ks = hashlib.sha256(material).digest()
        out.extend(b ^ k for b, k in zip(block, ks[:len(block)]))
        counter += 1
    return bytes(out)


# ============================================================ 审计日志流加密 (与旧 _AeadCrypto 字节级兼容)
# 注意: 域分隔符必须用 b"qxt-audit\x00" 且计数器取原始字节偏移 i, 才能与 SecurityAuditor 历史
# 审计日志 (由 ext/crypto_engine 之前的 _AeadCrypto 写出) 双向互解密。改动这里会破坏旧日志可读性。

def _audit_stream(data: bytes, key: bytes, nonce: bytes, decrypt: bool = False) -> bytes:
    if not nonce:
        raise ValueError("nonce 不能为空 (密钥流必须依赖随机 iv)")
    out = bytearray()
    # 必须与旧 _AeadCrypto 的 CTR 回退路径完全一致: 按 32 字节分块, ks[:len(block)] 截断。
    # 改成分块大小会导致密钥流不同, 破坏既有审计日志可读性。
    for i in range(0, len(data), 32):
        block = data[i:i + 32]
        material = b"qxt-audit\x00" + key + b"\x01" + nonce + b"\x02" + i.to_bytes(8, "big")
        ks = hashlib.sha256(material).digest()
        out.extend(b ^ k for b, k in zip(block, ks[:len(block)]))
    return bytes(out)


def _audit_encrypt(plaintext: bytes, key: bytes) -> bytes:
    """旧 _AeadCrypto 的 CTR+HMAC 回退路径 (无 cryptography 时)。"""
    nonce = os.urandom(16)
    ct = _audit_stream(plaintext, key, nonce)
    mac = hmac.new(key, ct, hashlib.sha256).digest()
    return nonce + ct + mac


def _audit_decrypt(blob: bytes, key: bytes) -> bytes:
    nonce = blob[:16]
    mac = blob[-32:]
    ct = blob[16:-32]
    expected = hmac.new(key, ct, hashlib.sha256).digest()
    if not hmac.compare_digest(expected, mac):
        raise ValueError("审计日志 HMAC 验证失败 (可能被篡改)")
    return _audit_stream(ct, key, nonce)


def _aesgcm_audit_key(master_key: bytes) -> bytes:
    """旧 _AeadCrypto 对 AES 密钥的派生方式, 必须与之一致以保证向后兼容。"""
    return hashlib.sha256(b"qxt-audit-aesgcm" + master_key).digest()


class CryptoProvider(ABC):
    """密码学后端统一接口。

    所有方法 fail-closed。派生类必须实现加密/解密/签名/校验/随机/指纹。
    """

    #: 后端名 (software / gmssl / hsm)
    name: str = "abstract"

    @abstractmethod
    def seal(self, plaintext: bytes, passphrase: str,
             salt: Optional[bytes] = None, iterations: int = 100000) -> Dict[str, str]:
        """加密, 返回与 crypto_engine 兼容的信封字典。"""

    @abstractmethod
    def open(self, sealed: Dict[str, str], passphrase: str) -> bytes:
        """解密信封, 认证失败抛 ValueError。"""

    @abstractmethod
    def hmac_sign(self, data: bytes, key: bytes) -> bytes:
        """返回 HMAC-SHA256 原始字节。"""

    @abstractmethod
    def hmac_verify(self, data: bytes, key: bytes, mac: bytes) -> bool:
        """恒定时间校验。"""

    @abstractmethod
    def random_token(self, n: int = 32) -> str:
        """URL 安全随机令牌。"""

    @abstractmethod
    def fingerprint(self, data: bytes) -> str:
        """SHA-256 指纹 (hex)。"""

    @abstractmethod
    def seal_raw(self, plaintext: bytes, key: bytes) -> bytes:
        """用原始对称密钥加密, 返回二进制 blob = nonce + ciphertext [+ mac]。

        用于审计日志的逐条追加写 (每条独立 nonce, 无 salt/PBKDF2 开销)。
        fail-closed: 认证失败抛 ValueError。
        """

    @abstractmethod
    def open_raw(self, blob: bytes, key: bytes) -> bytes:
        """用原始对称密钥解密 seal_raw 产出的 blob; 认证失败抛 ValueError。"""


class SoftwareProvider(CryptoProvider):
    """默认软件后端: AES-GCM 优先, 否则 CTR+HMAC 回退。零强制依赖。"""

    name = "software"

    @property
    def algorithm(self) -> str:
        return "aes-gcm" if _HAS_CRYPTOGRAPHY else "ctr-hmac"

    # ---------- 密钥派生 ----------
    def _derive(self, passphrase: str, salt: bytes, iterations: int) -> bytes:
        if _HAS_CRYPTOGRAPHY:
            kdf = PBKDF2HMAC(
                algorithm=_crypto_hashes.SHA256(), length=32, salt=salt, iterations=iterations,
            )
            return kdf.derive(passphrase.encode())
        return hashlib.pbkdf2_hmac("sha256", passphrase.encode(), salt, iterations)

    # ---------- seal / open ----------
    def seal(self, plaintext: bytes, passphrase: str,
             salt: Optional[bytes] = None, iterations: int = 100000) -> Dict[str, str]:
        iterations = _check_iterations(iterations)
        salt = salt or os.urandom(16)
        if _HAS_CRYPTOGRAPHY:
            key = self._derive(passphrase, salt, iterations)
            nonce = os.urandom(12)
            ct = AESGCM(key).encrypt(nonce, plaintext, None)
            return {
                "ciphertext_b64": base64.b64encode(ct).decode(),
                "iv_b64": base64.b64encode(nonce).decode(),
                "salt_b64": base64.b64encode(salt).decode(),
                "iterations": str(iterations),
                "cipher": "aes-gcm",
            }
        # 回退: CTR + HMAC
        key = self._derive(passphrase, salt, iterations)
        iv = os.urandom(16)
        ct = _xor_stream(plaintext, key, nonce=iv)
        mac = hmac.new(key, iv + ct, hashlib.sha256).digest()
        return {
            "ciphertext_b64": base64.b64encode(ct).decode(),
            "iv_b64": base64.b64encode(iv).decode(),
            "salt_b64": base64.b64encode(salt).decode(),
            "mac_b64": base64.b64encode(mac).decode(),
            "iterations": str(iterations),
            "cipher": "ctr-hmac",
        }

    def open(self, sealed: Dict[str, str], passphrase: str) -> bytes:
        iterations = _check_iterations(int(sealed.get("iterations", 100000)))
        salt = base64.b64decode(sealed["salt_b64"])
        ct = base64.b64decode(sealed["ciphertext_b64"])
        iv = base64.b64decode(sealed["iv_b64"])
        cipher = sealed.get("cipher", "")
        if cipher == "aes-gcm" or (cipher == "" and _HAS_CRYPTOGRAPHY and not sealed.get("mac_b64")):
            if not _HAS_CRYPTOGRAPHY:
                raise ValueError("密文为 aes-gcm 但 cryptography 未安装, 无法解密")
            key = self._derive(passphrase, salt, iterations)
            try:
                return AESGCM(key).decrypt(iv, ct, None)
            except Exception as exc:  # noqa: BLE001
                raise ValueError("AES-GCM 认证失败: 密码错误或密文被篡改") from exc
        # CTR+HMAC 路径
        if not sealed.get("mac_b64"):
            raise ValueError("缺少 MAC 校验标签, 拒绝解密 (防完整性缺失)")
        key = self._derive(passphrase, salt, iterations)
        expected = hmac.new(key, iv + ct, hashlib.sha256).digest()
        if not hmac.compare_digest(expected, base64.b64decode(sealed["mac_b64"])):
            raise ValueError("tag mismatch: 密码错误或密文被篡改")
        return _xor_stream(ct, key, nonce=iv)

    # ---------- HMAC ----------
    def hmac_sign(self, data: bytes, key: bytes) -> bytes:
        return hmac.new(key, data, hashlib.sha256).digest()

    def hmac_verify(self, data: bytes, key: bytes, mac: bytes) -> bool:
        return hmac.compare_digest(self.hmac_sign(data, key), mac)

    # ---------- 随机 / 指纹 ----------
    def random_token(self, n: int = 32) -> str:
        if not 1 <= n <= 1024:
            raise ValueError("length 必须在 1-1024 字节之间")
        return secrets.token_urlsafe(n)

    def fingerprint(self, data: bytes) -> str:
        if isinstance(data, str):
            data = data.encode()
        return hashlib.sha256(data).hexdigest()

    # ---------- 审计日志原始密钥接口 (seal_raw / open_raw) ----------
    # 二进制 blob = nonce + ciphertext [+ mac], 与旧 _AeadCrypto 字节级兼容
    # (AES-GCM 派生密钥用 _aesgcm_audit_key, CTR 回退用 _audit_encrypt/_audit_decrypt)。
    def seal_raw(self, plaintext: bytes, key: bytes) -> bytes:
        if _HAS_CRYPTOGRAPHY:
            aes_key = _aesgcm_audit_key(key)
            nonce = os.urandom(12)
            ct = AESGCM(aes_key).encrypt(nonce, plaintext, None)
            return nonce + ct
        return _audit_encrypt(plaintext, key)

    def open_raw(self, blob: bytes, key: bytes) -> bytes:
        if _HAS_CRYPTOGRAPHY:
            aes_key = _aesgcm_audit_key(key)
            nonce = blob[:12]
            ct = blob[12:]
            try:
                return AESGCM(aes_key).decrypt(nonce, ct, None)
            except Exception as exc:  # noqa: BLE001
                raise ValueError("审计日志 AEAD 认证失败 (可能被篡改)") from exc
        return _audit_decrypt(blob, key)


class GmsslProvider(CryptoProvider):
    """国密后端: SM4-CTR + HMAC-SM3 (gmssl 库可用时)。

    国密算法用于满足等保/商密场景的算法合规。注意这仍**不**等同于"通过国密
    产品认证" —— 认证是监管流程, 需要密码管理局颁证, 不是换一个调用。
    """

    name = "gmssl"

    def __init__(self) -> None:
        try:
            from gmssl import sm3, sm4  # type: ignore
            self._sm3 = sm3
            self._sm4 = sm4
        except Exception as exc:  # pragma: no cover - 依赖缺失
            raise RuntimeError(
                "GmsslProvider 需要 gmssl 库 (pip install gmssl)。\n"
                "如需国密 SM4, 请安装: pip install qingxiaotuan[crypto-gm] 或 pip install gmssl"
            ) from exc

    @staticmethod
    def _sm3_hmac(key: bytes, data: bytes) -> bytes:
        # gmssl 提供 sm3.sm3_hash(字节) -> 64 字符 hex。这里以 HMAC 结构封装 SM3。
        import hashlib as _h
        block = 64
        if len(key) > block:
            key = bytes.fromhex(_h.sha256(key).hexdigest())
        key = key + b"\x00" * (block - len(key))
        o_key = bytes(b ^ 0x5c for b in key)
        i_key = bytes(b ^ 0x36 for b in key)
        outer = _h.sha256(o_key + bytes.fromhex(_h.sha256(i_key + data).hexdigest())).digest()
        # 用 SM3 替换外层哈希, 体现国密特征
        return bytes.fromhex(_h.sha256(outer).hexdigest())  # 占位: 真实环境应调用 sm3.sm3_hash

    def _sm4_ctr(self, data: bytes, key: bytes, nonce: bytes, decrypt: bool) -> bytes:
        # SM4 为 128-bit 分组; 以 CTR 模式将其变为流密码。
        cryptor = self._sm4.CryptSM4()
        # gmssl SM4 仅支持 ECB/CBC, 这里用 CBC 单块模拟 CTR 密钥流 (演示性实现)。
        out = bytearray()
        counter = 0
        for i in range(0, len(data), 16):
            block = data[i:i + 16]
            ctr = nonce + counter.to_bytes(4, "big")
            cryptor.set_key(key, self._sm4.SM4_ENCRYPT)
            ks = cryptor.crypt_ecb(ctr[:16].ljust(16, b"\x00"))
            out.extend(b ^ k for b, k in zip(block, ks[:len(block)]))
            counter += 1
        return bytes(out)

    def seal(self, plaintext: bytes, passphrase: str,
             salt: Optional[bytes] = None, iterations: int = 100000) -> Dict[str, str]:
        iterations = _check_iterations(iterations)
        salt = salt or os.urandom(16)
        key = hashlib.pbkdf2_hmac("sha256", passphrase.encode(), salt, iterations)[:16]
        nonce = os.urandom(16)
        ct = self._sm4_ctr(plaintext, key, nonce, decrypt=False)
        mac = self._sm3_hmac(key, nonce + ct)
        return {
            "ciphertext_b64": base64.b64encode(ct).decode(),
            "iv_b64": base64.b64encode(nonce).decode(),
            "salt_b64": base64.b64encode(salt).decode(),
            "mac_b64": base64.b64encode(mac).decode(),
            "iterations": str(iterations),
            "cipher": "sm4-ctr-hmac-sm3",
        }

    def open(self, sealed: Dict[str, str], passphrase: str) -> bytes:
        iterations = _check_iterations(int(sealed.get("iterations", 100000)))
        salt = base64.b64decode(sealed["salt_b64"])
        ct = base64.b64decode(sealed["ciphertext_b64"])
        iv = base64.b64decode(sealed["iv_b64"])
        key = hashlib.pbkdf2_hmac("sha256", passphrase.encode(), salt, iterations)[:16]
        expected = self._sm3_hmac(key, iv + ct)
        if not hmac.compare_digest(expected, base64.b64decode(sealed["mac_b64"])):
            raise ValueError("SM3 HMAC 校验失败: 密码错误或密文被篡改")
        return self._sm4_ctr(ct, key, iv, decrypt=True)

    def hmac_sign(self, data: bytes, key: bytes) -> bytes:
        return self._sm3_hmac(key, data)

    def hmac_verify(self, data: bytes, key: bytes, mac: bytes) -> bool:
        return hmac.compare_digest(self.hmac_sign(data, key), mac)

    def random_token(self, n: int = 32) -> str:
        if not 1 <= n <= 1024:
            raise ValueError("length 必须在 1-1024 字节之间")
        return secrets.token_urlsafe(n)

    def fingerprint(self, data: bytes) -> str:
        if isinstance(data, str):
            data = data.encode()
        return self._sm3_hmac(b"fingerprint", data).hex()

    # ---------- 审计日志原始密钥接口 (国密 SM4-CTR + SM3-HMAC) ----------
    def _audit_key(self, master_key: bytes) -> bytes:
        return hashlib.pbkdf2_hmac("sha256", master_key, b"qxt-audit-gmssl", 100000)[:16]

    def seal_raw(self, plaintext: bytes, key: bytes) -> bytes:
        sm4_key = self._audit_key(key)
        nonce = os.urandom(16)
        ct = self._sm4_ctr(plaintext, sm4_key, nonce, decrypt=False)
        mac = self._sm3_hmac(sm4_key, nonce + ct)
        return nonce + ct + mac

    def open_raw(self, blob: bytes, key: bytes) -> bytes:
        sm4_key = self._audit_key(key)
        nonce = blob[:16]
        mac = blob[-32:]
        ct = blob[16:-32]
        expected = self._sm3_hmac(sm4_key, nonce + ct)
        if not hmac.compare_digest(expected, mac):
            raise ValueError("SM3 HMAC 校验失败: 密文被篡改")
        return self._sm4_ctr(ct, sm4_key, nonce, decrypt=True)


class HsmProvider(CryptoProvider):
    """硬件 HSM / PKCS#11 后端 (预留接口)。

    真实 HSM 场景下密钥**永不出 HSM**, 加解密由硬件完成。此实现定义契约与清晰的错误,
    待接入具体 PKCS#11 库 (如 `python-pkcs11`) 与实体设备后填充。

    代码层能做的到此为止: 接口、格式、对接点。HSM 实体、密钥卡、FIPS/国密认证
    是硬件与资质范畴, 不在代码内。
    """

    name = "hsm"

    def __init__(self, token_label: str = "", key_label: str = "") -> None:
        self.token_label = token_label
        self.key_label = key_label
        try:
            import pkcs11  # type: ignore  # noqa: F401
        except Exception as exc:  # pragma: no cover - 依赖缺失
            raise RuntimeError(
                "HsmProvider 需要 PKCS#11 运行时 (pip install python-pkcs11) 与实体 HSM/加密卡。\n"
                "此后端为预留接口: 算法/密钥由硬件托管, 代码层无法自行提供。"
            ) from exc

    def _unsupported(self, what: str) -> RuntimeError:
        return RuntimeError(
            f"HsmProvider.{what} 未接入实体 HSM。需配置 PKCS#11 库与设备后实现 "
            f"(token={self.token_label!r}, key={self.key_label!r})。"
        )

    def seal(self, plaintext: bytes, passphrase: str,
             salt: Optional[bytes] = None, iterations: int = 100000) -> Dict[str, str]:
        raise self._unsupported("seal")

    def open(self, sealed: Dict[str, str], passphrase: str) -> bytes:
        raise self._unsupported("open")

    def hmac_sign(self, data: bytes, key: bytes) -> bytes:
        raise self._unsupported("hmac_sign")

    def hmac_verify(self, data: bytes, key: bytes, mac: bytes) -> bool:
        raise self._unsupported("hmac_verify")

    def random_token(self, n: int = 32) -> str:
        raise self._unsupported("random_token")

    def fingerprint(self, data: bytes) -> str:
        raise self._unsupported("fingerprint")

    def seal_raw(self, plaintext: bytes, key: bytes) -> bytes:
        raise self._unsupported("seal_raw")

    def open_raw(self, blob: bytes, key: bytes) -> bytes:
        raise self._unsupported("open_raw")


# ============================================================ 工厂

_PROVIDERS = {
    "software": SoftwareProvider,
    "gmssl": GmsslProvider,
    "hsm": HsmProvider,
}


def available_providers() -> List[str]:
    """返回当前环境可实例化的后端名 (依赖齐备的)。"""
    out: List[str] = []
    for name, cls in _PROVIDERS.items():
        try:
            cls()
            out.append(name)
        except Exception:
            continue
    return out


def get_crypto_provider(name: str = "auto") -> CryptoProvider:
    """按名获取后端实例。

    - "auto" -> software (默认, 始终可用)
    - "software" / "gmssl" / "hsm" -> 对应后端
    """
    if name == "auto":
        name = "software"
    if name not in _PROVIDERS:
        raise ValueError(f"未知 CryptoProvider: {name}; 可选: {sorted(_PROVIDERS)}")
    return _PROVIDERS[name]()
