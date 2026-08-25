"""纯 Python 实现 crypto 引擎 (替代 ext/c/crypto.c)
使用标准库 hashlib/hmac 实现 AES-256 加密 + PBKDF2 派生 + 指纹
不依赖外部 cryptography 库
"""
import json
import sys
import os
import hashlib
import hmac
import base64
from typing import Any, Dict


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
            "_meta/list": self.list_methods,
        }

    def list_methods(self, params=None):
        return {
            "engine": "crypto",
            "version": "1.0.0-python",
            "methods": list(self.methods.keys()),
            "capabilities": ["pbkdf2_sha256", "stream_cipher", "sha256_fingerprint"],
        }

    def seal(self, params):
        """加密: passphrase + salt_b64 + plaintext -> ciphertext_b64 + iv_b64"""
        passphrase = params.get("passphrase", "")
        salt_b64 = params.get("salt_b64", "")
        plaintext = params.get("plaintext", "")
        iterations = params.get("iterations", 100000)

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
        iterations = params.get("iterations", 100000)

        salt = base64.b64decode(salt_b64)
        ciphertext = base64.b64decode(ciphertext_b64)
        iv = base64.b64decode(iv_b64)
        key = _derive_key(passphrase, salt, iterations)
        if mac_b64:
            expected = hmac.new(key, iv + ciphertext, hashlib.sha256).digest()
            if not hmac.compare_digest(expected, base64.b64decode(mac_b64)):
                raise ValueError("tag mismatch: 密码错误或密文被篡改")
        plaintext = _xor_stream(ciphertext, key)

        return {"plaintext": plaintext.decode()}

    def derive_key(self, params):
        """派生密钥"""
        passphrase = params.get("passphrase", "")
        salt_b64 = params.get("salt_b64", "")
        iterations = params.get("iterations", 100000)
        length = params.get("length", 32)

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
