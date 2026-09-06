"""CryptoProvider.raw 对称接口测试: 往返 + 与旧 _AeadCrypto 字节级兼容 + HSM 报错。"""
import hashlib
import hmac

from qingxiaotuan.harden.crypto_provider import (
    GmsslProvider,
    HsmProvider,
    SoftwareProvider,
    get_crypto_provider,
)


def _old_ct_encrypt(plaintext: bytes, key: bytes, nonce: bytes) -> bytes:
    """复刻已删除的 _AeadCrypto CTR+HMAC 回退路径, 用于向后兼容断言。"""
    result = bytearray()
    for i in range(0, len(plaintext), 32):
        block = plaintext[i:i + 32]
        material = b"qxt-audit\x00" + key + b"\x01" + nonce + b"\x02" + i.to_bytes(8, "big")
        ks = hashlib.sha256(material).digest()
        result.extend(bytes(a ^ b for a, b in zip(block, ks[:len(block)])))
    mac = hmac.new(key, bytes(result), hashlib.sha256).digest()
    return nonce + bytes(result) + mac


def _old_ct_decrypt(blob: bytes, key: bytes) -> bytes:
    nonce = blob[:16]
    mac = blob[-32:]
    ct = blob[16:-32]
    expected = hmac.new(key, ct, hashlib.sha256).digest()
    assert hmac.compare_digest(expected, mac)
    result = bytearray()
    for i in range(0, len(ct), 32):
        block = ct[i:i + 32]
        material = b"qxt-audit\x00" + key + b"\x01" + nonce + b"\x02" + i.to_bytes(8, "big")
        ks = hashlib.sha256(material).digest()
        result.extend(bytes(a ^ b for a, b in zip(block, ks[:len(block)])))
    return bytes(result)


def test_software_seal_open_raw_roundtrip():
    sp = SoftwareProvider()
    key = bytes(range(32))
    pt = b"hello audit log \x00\x01 payload" * 4
    blob = sp.seal_raw(pt, key)
    assert sp.open_raw(blob, key) == pt


def test_software_backward_compatible_with_old_aead():
    """新软件后端必须能解密旧 _AeadCrypto 写出的 blob (反之亦然)。"""
    sp = SoftwareProvider()
    key = bytes(range(32))
    pt = b"legacy audit record payload" * 3
    # 旧格式 -> 新后端可读
    nonce = b"\x11" * 16
    old_blob = _old_ct_encrypt(pt, key, nonce)
    assert sp.open_raw(old_blob, key) == pt
    # 新后端写出 -> 旧算法可读 (向前兼容)
    new_blob = sp.seal_raw(pt, key)
    assert _old_ct_decrypt(new_blob, key) == pt


def test_software_raw_fails_on_tamper():
    sp = SoftwareProvider()
    key = bytes(range(32))
    blob = bytearray(sp.seal_raw(b"secret", key))
    blob[-1] ^= 0xFF  # 篡改密文尾部
    try:
        sp.open_raw(bytes(blob), key)
        assert False, "篡改的 blob 应当解密失败"
    except ValueError:
        pass


def test_gmssl_or_skip():
    try:
        gp = GmsslProvider()
    except RuntimeError:
        import pytest
        pytest.skip("gmssl 未安装, 跳过国密后端测试")
    key = bytes(range(32))
    pt = b"gmssl audit payload" * 3
    blob = gp.seal_raw(pt, key)
    assert gp.open_raw(blob, key) == pt


def test_hsm_raw_unsupported():
    try:
        hp = HsmProvider()
    except RuntimeError:
        # 没有 PKCS#11/HSM 时工厂会在审计器里降级; 这里直接验证接口契约:
        # HsmProvider 的 seal_raw/open_raw 必须是"明确报错"而非假装可用。
        from qingxiaotuan.harden.crypto_provider import HsmProvider as HP
        hp = object.__new__(HP)  # 绕过 __init__ 的依赖检查
        hp.token_label = ""
        hp.key_label = ""
    else:
        pass
    # 无论是否拿到实例, 接口都应明确报错 (不静默)
    import pytest
    with pytest.raises(RuntimeError):
        hp.seal_raw(b"x", b"k")
    with pytest.raises(RuntimeError):
        hp.open_raw(b"x", b"k")


def test_get_crypto_provider_auto_is_software():
    assert get_crypto_provider("auto").name == "software"
