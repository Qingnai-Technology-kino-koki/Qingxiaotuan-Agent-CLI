"""纯 Python 实现 crypto 引擎
使用标准库 hashlib/hmac/secrets 实现 PBKDF2 密钥派生 + SHA256-keystream 流式加密
+ 指纹 + HMAC 签名校验 + 随机令牌
不依赖外部 cryptography 库 (流式加密为轻量 CTR 风格替代, 非 AES)
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


def _check_iterations(value) -> int:
    """校验迭代次数为正整数且不低于下限。"""
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError("iterations 必须是正整数")
    if value < _MIN_ITERATIONS:
        raise ValueError(f"iterations 不得低于 {_MIN_ITERATIONS} (防弱化)")
    return value


def _derive_key(passphrase: str, salt: bytes, iterations: int = 100000) -> bytes:
    """PBKDF2-HMAC-SHA256 密钥派生"""
    return hashlib.pbkdf2_hmac('sha256', passphrase.encode(), salt, iterations)


def _xor_bytes(a: bytes, b: bytes) -> bytes:
    """XOR 运算"""
    return bytes(x ^ y for x, y in zip(a, b))


def _xor_stream(data: bytes, key: bytes, counter_start: int = 1) -> bytes:
    """流式 XOR 加密 (简单 CTR 模式替代 AES-GCM)"""
    result = bytearray()
    counter = counter_start
    for i in range(0, len(data), 16):
        block = data[i:i+16]
        keystream = hashlib.sha256(key + counter.to_bytes(8, 'big')).digest()
        result.extend(_xor_bytes(block, keystream[:len(block)]))
        counter += 1
    return bytes(result)


def _fingerprint(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


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
        return {
            "engine": "crypto",
            "version": "1.1.0-python",
            "methods": list(self.methods.keys()),
            "capabilities": ["pbkdf2_sha256", "stream_cipher", "sha256_fingerprint",
                             "hmac_sign_verify", "random_token"],
        }

    def seal(self, params):
        """加密: passphrase + salt_b64 + plaintext -> ciphertext_b64 + iv_b64"""
        passphrase = params.get("passphrase", "")
        salt_b64 = params.get("salt_b64", "")
        plaintext = params.get("plaintext", "")
        iterations = _check_iterations(params.get("iterations", 100000))

        salt = base64.b64decode(salt_b64) if salt_b64 else os.urandom(16)
        key = _derive_key(passphrase, salt, iterations)
        iv = os.urandom(16)
        data = plaintext.encode()
        # 组合: iv + ciphertext
        ciphertext = _xor_stream(data, key)
        mac = hmac.new(key, iv + ciphertext, hashlib.sha256).digest()

        result = {
            "ciphertext_b64": base64.b64encode(ciphertext).decode(),
            "iv_b64": base64.b64encode(iv).decode(),
            "salt_b64": base64.b64encode(salt).decode(),
            "mac_b64": base64.b64encode(mac).decode(),
            "iterations": iterations,
        }
        return result

    def open(self, params):
        """解密: passphrase + salt_b64 + ciphertext_b64 + iv_b64 -> plaintext (可带 mac_b64 校验)"""
        passphrase = params.get("passphrase", "")
        salt_b64 = params.get("salt_b64", "")
        ciphertext_b64 = params.get("ciphertext_b64", "")
        iv_b64 = params.get("iv_b64", "")
        mac_b64 = params.get("mac_b64", "")
        iterations = _check_iterations(params.get("iterations", 100000))

        salt = base64.b64decode(salt_b64)
        ciphertext = base64.b64decode(ciphertext_b64)
        iv = base64.b64decode(iv_b64)
        key = _derive_key(passphrase, salt, iterations)
        if mac_b64:
            expected = hmac.new(key, iv + ciphertext, hashlib.sha256).digest()
            if not hmac.compare_digest(expected, base64.b64decode(mac_b64)):
                raise ValueError("tag mismatch: 密码错误或密文被篡改")
        plaintext = _xor_stream(ciphertext, key)

        try:
            text = plaintext.decode()
        except UnicodeDecodeError:
            raise ValueError(
                "解密结果不是有效 UTF-8: 密码错误且未提供 mac 校验, 或密文来源不符")
        return {"plaintext": text}

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
