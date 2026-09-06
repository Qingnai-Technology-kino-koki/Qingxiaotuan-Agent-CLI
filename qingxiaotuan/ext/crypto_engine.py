"""Crypto 引擎 —— PBKDF2 密钥派生 + 加密/解密 + 指纹 + HMAC + 随机令牌。

加密策略 (v2, 迁移至工业级 AEAD):
- 优先使用 `cryptography` 库的 AES-GCM (128-bit key, 96-bit nonce, 128-bit tag)
  → 认证加密, 同时提供机密性 + 完整性 + 认证, 无需额外 HMAC。
- 若 `cryptography` 未安装, 回退到纯 Python CTR 风格流式加密 + HMAC-SHA256
  → 轻量但非工业级, 仅用于开发/测试场景。
- seal/open 的返回格式向后兼容: 额外返回 `cipher` 字段标识使用的算法,
  旧密文 (无 cipher 字段) 按 CTR+HMAC 解密, 新密文按 AES-GCM 解密。

安装工业级加密: `pip install qingxiaotuan[crypto]` 或 `pip install cryptography`
"""
import json
import sys
import os
import hashlib
import hmac
import base64
import secrets

# PBKDF2 迭代次数下限: 低于此值视为配置错误而非兼容需求
_MIN_ITERATIONS = 1000
# 迭代次数上限: 防止恶意/失误的大迭代值拖垮 CPU (DoS)。
# 100k 是默认值; 上限留 50 倍余量, 远超任何合理派生需求。
_MAX_ITERATIONS = 5_000_000

# ---- 检测 cryptography 库是否可用 (AES-GCM) ----
try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    from cryptography.hazmat.primitives import hashes as crypto_hashes
    _HAS_CRYPTOGRAPHY = True
except ImportError:
    _HAS_CRYPTOGRAPHY = False


def _check_iterations(value) -> int:
    """校验迭代次数为正整数, 且落在 [下限, 上限] 安全区间内。"""
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError("iterations 必须是正整数")
    if value < _MIN_ITERATIONS:
        raise ValueError(f"iterations 不得低于 {_MIN_ITERATIONS} (防弱化)")
    if value > _MAX_ITERATIONS:
        raise ValueError(f"iterations 不得超过 {_MAX_ITERATIONS} (防 DoS)")
    return value


def _derive_key(passphrase: str, salt: bytes, iterations: int = 100000) -> bytes:
    """PBKDF2-HMAC-SHA256 密钥派生"""
    return hashlib.pbkdf2_hmac('sha256', passphrase.encode(), salt, iterations)


def _xor_bytes(a: bytes, b: bytes) -> bytes:
    """XOR 运算"""
    return bytes(x ^ y for x, y in zip(a, b))


def _xor_stream_legacy(data: bytes, key: bytes, nonce: bytes = b'', counter_start: int = 0) -> bytes:
    """旧版 CTR 流式加密 (v1): keystream = SHA256(域分隔符 ‖ key ‖ nonce ‖ counter)。

    仅用于解密旧密文 (cipher="ctr-hmac" 或无 cipher 字段)。
    新加密应使用 _xor_stream (HMAC-CTR)。
    """
    if not nonce:
        raise ValueError("nonce 不能为空")
    result = bytearray()
    counter = counter_start
    for i in range(0, len(data), 16):
        block = data[i:i + 16]
        material = b"qxt-stream\x00" + key + b"\x01" + nonce + b"\x02" + counter.to_bytes(8, 'big')
        keystream = hashlib.sha256(material).digest()
        result.extend(_xor_bytes(block, keystream[:len(block)]))
        counter += 1
    return bytes(result)


def _xor_stream(data: bytes, key: bytes, nonce: bytes = b'', counter_start: int = 0) -> bytes:
    """HMAC-CTR 流式加密 (v2): keystream = HMAC-SHA256(key, nonce ‖ counter)。

    采用标准 HMAC-CTR 构造 (FIPS 198-1 PRF):
    - HMAC-SHA256 作为伪随机函数 (PRF), 比裸 SHA256 提供更强的安全性保证;
    - nonce (16 字节随机 IV) + 8 字节大端 counter 作为 HMAC 消息;
    - 每块产出 32 字节 (SHA256 输出长度), 切取前 len(block) 字节做 XOR;
    - 不同 nonce → 不同密钥流 (语义安全); 块内 counter 递增保证块级唯一。
    """
    if not nonce:
        raise ValueError("nonce 不能为空 (密钥流必须依赖随机 iv)")
    result = bytearray()
    counter = counter_start
    block_size = 32  # HMAC-SHA256 输出长度
    for i in range(0, len(data), block_size):
        block = data[i:i + block_size]
        # 标准 HMAC-CTR: HMAC(key, nonce ‖ big_endian(counter))
        msg = nonce + counter.to_bytes(8, 'big')
        keystream = hmac.new(key, msg, hashlib.sha256).digest()
        result.extend(_xor_bytes(block, keystream[:len(block)]))
        counter += 1
    return bytes(result)


def _fingerprint(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ================================================================ AES-GCM (cryptography 库)

def _derive_key_aesgcm(passphrase: str, salt: bytes, iterations: int = 100000) -> bytes:
    """使用 cryptography 库的 PBKDF2 派生 32 字节密钥 (AES-256)。"""
    kdf = PBKDF2HMAC(
        algorithm=crypto_hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=iterations,
    )
    return kdf.derive(passphrase.encode())


def _seal_aesgcm(plaintext: bytes, key: bytes) -> tuple[bytes, bytes]:
    """AES-GCM 加密: 返回 (ciphertext_with_tag, nonce)。

    AES-GCM 在一次操作中同时提供:
    - 机密性 (AES-CTR 内部模式)
    - 完整性 (GHASH 认证标签)
    - 认证 (防止密文替换)

    nonce 为 12 字节 (96-bit), 每次加密随机生成。
    认证标签为 16 字节 (128-bit), 附加在密文末尾。
    """
    nonce = os.urandom(12)  # 96-bit nonce for AES-GCM
    aesgcm = AESGCM(key)
    # associated_data=None: 无额外认证数据 (只认证密文本身)
    ciphertext_with_tag = aesgcm.encrypt(nonce, plaintext, None)
    return ciphertext_with_tag, nonce


def _open_aesgcm(ciphertext_with_tag: bytes, key: bytes, nonce: bytes) -> bytes:
    """AES-GCM 解密: 验证认证标签后解密。

    如果密文被篡改或密钥错误, 抛出 InvalidTag 异常 (fail-closed)。
    """
    from cryptography.exceptions import InvalidTag
    aesgcm = AESGCM(key)
    try:
        return aesgcm.decrypt(nonce, ciphertext_with_tag, None)
    except InvalidTag:
        raise ValueError("AES-GCM 认证失败: 密码错误或密文被篡改")


class CryptoEngine:
    """统一 JSONL IPC 协议的 crypto 引擎"""

    def __init__(self):
        self.methods = {
            "seal": self.seal,
            "open": self.open,
            "derive_key": self.derive_key,
            "fingerprint": self.fingerprint,
            "hmac_sign": self.hmac_sign,
            "hmac_verify": self.hmac_verify,
            "random_token": self.random_token,
            "_meta/list": self.list_methods,
        }

    def list_methods(self, params=None):
        cipher = "aes-gcm" if _HAS_CRYPTOGRAPHY else "hmac-ctr"
        return {
            "engine": "crypto",
            "version": "2.1.0-python",
            "methods": list(self.methods.keys()),
            "capabilities": [
                "pbkdf2_sha256", cipher, "sha256_fingerprint",
                "hmac_sign_verify", "random_token",
            ],
            "cipher": cipher,
            "has_cryptography": _HAS_CRYPTOGRAPHY,
        }

    def seal(self, params):
        """加密: passphrase + salt_b64 + plaintext -> ciphertext_b64 + iv_b64

        优先使用 AES-GCM (cryptography 库可用时), 否则回退 CTR+HMAC。
        返回格式: 新增 `cipher` 字段标识算法 ("aes-gcm" 或 "ctr-hmac"),
        旧密文 (无 cipher 字段) 按 CTR+HMAC 解密, 向后兼容。
        """
        passphrase = params.get("passphrase", "")
        salt_b64 = params.get("salt_b64", "")
        plaintext = params.get("plaintext", "")
        iterations = _check_iterations(params.get("iterations", 100000))

        salt = base64.b64decode(salt_b64) if salt_b64 else os.urandom(16)
        data = plaintext.encode()

        if _HAS_CRYPTOGRAPHY:
            # AES-GCM: 工业级认证加密
            key = _derive_key_aesgcm(passphrase, salt, iterations)
            ciphertext_with_tag, nonce = _seal_aesgcm(data, key)
            return {
                "ciphertext_b64": base64.b64encode(ciphertext_with_tag).decode(),
                "iv_b64": base64.b64encode(nonce).decode(),
                "salt_b64": base64.b64encode(salt).decode(),
                "iterations": iterations,
                "cipher": "aes-gcm",
            }
        else:
            # CTR+HMAC: 纯 Python 回退 (轻量但非工业级)
            key = _derive_key(passphrase, salt, iterations)
            iv = os.urandom(16)
            ciphertext = _xor_stream(data, key, nonce=iv)
            mac = hmac.new(key, iv + ciphertext, hashlib.sha256).digest()
            return {
                "ciphertext_b64": base64.b64encode(ciphertext).decode(),
                "iv_b64": base64.b64encode(iv).decode(),
                "salt_b64": base64.b64encode(salt).decode(),
                "mac_b64": base64.b64encode(mac).decode(),
                "iterations": iterations,
                "cipher": "ctr-hmac",
            }

    def open(self, params):
        """解密: passphrase + salt_b64 + ciphertext_b64 + iv_b64 -> plaintext。

        自动检测 cipher 字段:
        - cipher="aes-gcm": 使用 AES-GCM 解密 (认证标签已含完整性校验)
        - cipher="ctr-hmac" 或无 cipher 字段: 使用 CTR+HMAC 解密 (向后兼容)

        fail-closed: 完整性校验为强制项, 校验不通过一律拒绝解密。
        """
        passphrase = params.get("passphrase", "")
        salt_b64 = params.get("salt_b64", "")
        ciphertext_b64 = params.get("ciphertext_b64", "")
        iv_b64 = params.get("iv_b64", "")
        mac_b64 = params.get("mac_b64", "")
        iterations = _check_iterations(params.get("iterations", 100000))
        cipher = params.get("cipher", "")

        salt = base64.b64decode(salt_b64)
        ciphertext = base64.b64decode(ciphertext_b64)
        iv = base64.b64decode(iv_b64)

        # 自动选择解密路径
        use_aesgcm = (cipher == "aes-gcm" or
                       (cipher == "" and _HAS_CRYPTOGRAPHY and not mac_b64))

        if use_aesgcm and _HAS_CRYPTOGRAPHY:
            # AES-GCM 解密 (认证标签在密文末尾, 一次性验证+解密)
            key = _derive_key_aesgcm(passphrase, salt, iterations)
            plaintext = _open_aesgcm(ciphertext, key, iv)
        else:
            # CTR+HMAC 解密 (向后兼容旧密文)
            key = _derive_key(passphrase, salt, iterations)
            # fail-closed: HMAC 校验必须存在且通过
            if not mac_b64:
                raise ValueError(
                    "缺少 MAC 校验标签, 拒绝解密 (防完整性缺失; "
                    "如需 AES-GCM 请重新加密)")
            expected = hmac.new(key, iv + ciphertext, hashlib.sha256).digest()
            if not hmac.compare_digest(expected, base64.b64decode(mac_b64)):
                raise ValueError("tag mismatch: 密码错误或密文被篡改")
            plaintext = _xor_stream(ciphertext, key, nonce=iv)

        try:
            text = plaintext.decode()
        except UnicodeDecodeError:
            raise ValueError(
                "解密结果不是有效 UTF-8: 密码错误, 或密文来源不符")
        return {"plaintext": text, "cipher": cipher or ("aes-gcm" if use_aesgcm else "ctr-hmac")}

    def derive_key(self, params):
        """派生密钥"""
        passphrase = params.get("passphrase", "")
        salt_b64 = params.get("salt_b64", "")
        iterations = _check_iterations(params.get("iterations", 100000))
        length = int(params.get("length", 32))

        salt = base64.b64decode(salt_b64) if salt_b64 else os.urandom(16)
        key = _derive_key(passphrase, salt, iterations)
        key = key[:length]

        return {"key_b64": base64.b64encode(key).decode(), "salt_b64": base64.b64encode(salt).decode()}

    def fingerprint(self, params):
        """指纹"""
        data = params.get("data", "")
        if isinstance(data, str):
            data = data.encode()
        fp = _fingerprint(data)
        return {"fingerprint": fp, "algorithm": "sha256"}

    @staticmethod
    def _resolve_key(params) -> bytes:
        """从 key_b64 或 passphrase+salt_b64 解析 HMAC 密钥。"""
        key_b64 = params.get("key_b64", "")
        if key_b64:
            return base64.b64decode(key_b64)
        passphrase = params.get("passphrase", "")
        salt_b64 = params.get("salt_b64", "")
        salt = base64.b64decode(salt_b64) if salt_b64 else b"qxt-hmac"
        iterations = _check_iterations(params.get("iterations", 100000))
        return _derive_key(passphrase, salt, iterations)

    def hmac_sign(self, params):
        """HMAC-SHA256 签名: key_b64 (或 passphrase+salt_b64) + data -> mac_b64"""
        data = params.get("data", "")
        raw = data.encode() if isinstance(data, str) else data
        key = self._resolve_key(params)
        mac = hmac.new(key, raw, hashlib.sha256).digest()
        return {"mac_b64": base64.b64encode(mac).decode(), "algorithm": "hmac-sha256"}

    def hmac_verify(self, params):
        """HMAC 校验 (恒定时间比较): {valid: true/false}, 不泄露差异位置"""
        data = params.get("data", "")
        raw = data.encode() if isinstance(data, str) else data
        key = self._resolve_key(params)
        mac_given = base64.b64decode(params.get("mac_b64", ""))
        expected = hmac.new(key, raw, hashlib.sha256).digest()
        return {"valid": hmac.compare_digest(expected, mac_given),
                "algorithm": "hmac-sha256"}

    def random_token(self, params):
        """URL 安全随机令牌 (secrets 模块), length 为字节数 (默认 32, 上限 1024)"""
        length = int(params.get("length", 32))
        if not 1 <= length <= 1024:
            raise ValueError("length 必须在 1-1024 字节之间")
        token = secrets.token_urlsafe(length)
        return {"token": token, "bytes": length}

    def handle(self, line):
        try:
            req = json.loads(line)
            method = req.get("method", "")
            params = req.get("params", {})
            req_id = req.get("id", None)
            if method in self.methods:
                result = self.methods[method](params)
                resp = {"id": req_id, "ok": True, "result": result}
            else:
                resp = {"id": req_id, "ok": False, "error": f"Unknown method: {method}"}
            return json.dumps(resp, ensure_ascii=False)
        except Exception as e:
            resp = {"id": None, "ok": False, "error": str(e)}
            return json.dumps(resp, ensure_ascii=False)

    def run(self):
        sys.stdout.write(json.dumps({"ready": True}) + "\n")
        sys.stdout.flush()
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            sys.stdout.write(self.handle(line) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    CryptoEngine().run()
