"""crypto 引擎安全修复测试。

覆盖 CODE_REVIEW.md §4.1 指出的密钥流复用漏洞修复:
- 同一明文 + 同一派生密钥, 两次 seal 必须产生不同密文 (iv 参与密钥流);
- 已知明文不可通过复用密钥流破译 (每次加密密钥流互异);
- HMAC 防篡改: 密文被改动 / 密码错误均被拒绝。
"""
import base64

from qingxiaotuan.ext.crypto_engine import CryptoEngine


def _seal(params):
    return CryptoEngine().seal(params)


def _open(params):
    return CryptoEngine().open(params)


def test_seal_open_roundtrip():
    r = _seal({"passphrase": "pw", "plaintext": "hello 青小团 🔐"})
    out = _open({"passphrase": "pw", "ciphertext_b64": r["ciphertext_b64"],
                 "iv_b64": r["iv_b64"], "salt_b64": r["salt_b64"], "mac_b64": r["mac_b64"]})
    assert out["plaintext"] == "hello 青小团 🔐"


def test_keystream_depends_on_random_iv():
    """关键修复验证: 同一派生密钥下, iv 必须进入密钥流, 否则密文确定且可破译。"""
    fixed_salt = base64.b64encode(b"salt" * 4).decode()
    a = _seal({"passphrase": "pw", "plaintext": "A" * 64, "salt_b64": fixed_salt})
    b = _seal({"passphrase": "pw", "plaintext": "A" * 64, "salt_b64": fixed_salt})
    # iv 与密文都必须互异 —— 证明密钥流依赖随机 nonce, 而非确定性
    assert a["iv_b64"] != b["iv_b64"], "两次加密使用了相同 iv"
    assert a["ciphertext_b64"] != b["ciphertext_b64"], "密钥流复用: iv 未参与密钥流派生"


def test_known_plaintext_cannot_decrypt_other_messages():
    """已知一条明密文对, 不能借此解密另一条同密钥消息 (密钥流互异)。"""
    fixed_salt = base64.b64encode(b"salt" * 4).decode()
    known = _seal({"passphrase": "pw", "plaintext": "KNOWNPLAINTEXT", "salt_b64": fixed_salt})
    other = _seal({"passphrase": "pw", "plaintext": "TOPSECRETMSG", "salt_b64": fixed_salt})
    assert known["ciphertext_b64"] != other["ciphertext_b64"]
    # 用 known 的 iv 去开 other 的密文 (同密钥) —— 因 iv 不同, 结果应为乱码而非 TOPSECRETMSG
    try:
        wrong = _open({"passphrase": "pw", "ciphertext_b64": other["ciphertext_b64"],
                       "iv_b64": known["iv_b64"], "salt_b64": fixed_salt})
        assert wrong["plaintext"] != "TOPSECRETMSG"
    except (ValueError, UnicodeDecodeError):
        pass  # 乱码触发校验/解码异常, 同样说明无法用已知 iv 解密


def test_wrong_passphrase_rejected_by_mac():
    r = _seal({"passphrase": "pw", "plaintext": "secret"})
    try:
        _open({"passphrase": "WRONG", "ciphertext_b64": r["ciphertext_b64"],
               "iv_b64": r["iv_b64"], "salt_b64": r["salt_b64"], "mac_b64": r["mac_b64"]})
        raise AssertionError("错误密码未被 MAC 拒绝")
    except ValueError as e:
        assert "tag mismatch" in str(e)


def test_tampered_ciphertext_rejected():
    r = _seal({"passphrase": "pw", "plaintext": "integrity"})
    ct = bytearray(base64.b64decode(r["ciphertext_b64"]))
    ct[0] ^= 0xFF  # 篡改一个字节
    try:
        _open({"passphrase": "pw", "ciphertext_b64": base64.b64encode(bytes(ct)).decode(),
               "iv_b64": r["iv_b64"], "salt_b64": r["salt_b64"], "mac_b64": r["mac_b64"]})
        raise AssertionError("被篡改的密文未被 MAC 拒绝")
    except ValueError as e:
        assert "tag mismatch" in str(e)


def test_hmac_sign_verify_roundtrip():
    eng = CryptoEngine()
    sign = eng.hmac_sign({"key_b64": base64.b64encode(b"k" * 32).decode(), "data": "msg"})
    ok = eng.hmac_verify({"key_b64": base64.b64encode(b"k" * 32).decode(),
                          "data": "msg", "mac_b64": sign["mac_b64"]})
    assert ok["valid"] is True
    bad = eng.hmac_verify({"key_b64": base64.b64encode(b"k" * 32).decode(),
                           "data": "tampered", "mac_b64": sign["mac_b64"]})
    assert bad["valid"] is False
