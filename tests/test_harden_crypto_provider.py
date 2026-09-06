"""CryptoProvider 可插拔后端测试。"""
import base64

import pytest

from qingxiaotuan.harden.crypto_provider import (
    CryptoProvider,
    SoftwareProvider,
    GmsslProvider,
    HsmProvider,
    get_crypto_provider,
    available_providers,
)


def test_software_seal_open_roundtrip():
    p = SoftwareProvider()
    secret = b"top-secret-bytes-\xc4\xbd"
    sealed = p.seal(secret, "pw123")
    assert sealed["cipher"] in ("aes-gcm", "ctr-hmac")
    assert base64.b64decode(sealed["salt_b64"])
    opened = p.open(sealed, "pw123")
    assert opened == secret


def test_software_wrong_passphrase_fails():
    p = SoftwareProvider()
    sealed = p.seal(b"hello", "right")
    with pytest.raises(ValueError):
        p.open(sealed, "wrong")


def test_software_hmac_sign_verify():
    p = SoftwareProvider()
    key = b"k" * 32
    mac = p.hmac_sign(b"data", key)
    assert p.hmac_verify(b"data", key, mac)
    assert not p.hmac_verify(b"data", b"other" * 4, mac)


def test_software_random_and_fingerprint():
    p = SoftwareProvider()
    t = p.random_token(16)
    assert isinstance(t, str) and len(t) >= 16
    fp = p.fingerprint(b"abc")
    assert fp == p.fingerprint(b"abc") and fp != p.fingerprint(b"abd")


def test_iterations_bounds():
    p = SoftwareProvider()
    with pytest.raises(ValueError):
        p.seal(b"x", "pw", iterations=10)   # 低于下限
    with pytest.raises(ValueError):
        p.seal(b"x", "pw", iterations=9_999_999)  # 高于上限


def test_factory_auto_is_software():
    p = get_crypto_provider("auto")
    assert isinstance(p, SoftwareProvider)


def test_gmssl_unavailable_raises():
    # gmssl 未安装时应给出清晰错误 (不静默)
    if "gmssl" in available_providers():
        pytest.skip("gmssl 已安装, 跳过缺失依赖断言")
    with pytest.raises(RuntimeError):
        GmsslProvider()


def test_hsm_unavailable_raises():
    if "hsm" in available_providers():
        pytest.skip("pkcs11 已安装, 跳过缺失依赖断言")
    with pytest.raises(RuntimeError):
        HsmProvider()


def test_software_is_default_available():
    assert "software" in available_providers()
    # 抽象基类不可实例化
    with pytest.raises(TypeError):
        CryptoProvider()
