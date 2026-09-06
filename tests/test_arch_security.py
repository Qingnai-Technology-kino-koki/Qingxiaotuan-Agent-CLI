"""安全层测试: 不可变策略 + 沙箱 + 加密保险库。"""
import json
import os
import sys

import pytest

from qingxiaotuan.arch.security import (
    CryptoVault,
    ImmutableSecurityPolicy,
    PolicyViolation,
    SyscallSandbox,
)


KEY = b"master-key-for-test-only-32bytes!!"


def test_policy_seals_immutable():
    p = ImmutableSecurityPolicy(name="p1", allow_binaries=["python"])
    sig = p.seal(KEY)
    assert p._SEALED
    assert p.verify(KEY)
    # seal 后修改应被拒绝
    with pytest.raises(PolicyViolation):
        p.allow_host_writes = True
    # 签名不变
    assert p.signature_of(KEY) == sig


def test_policy_tamper_detected_on_load(tmp_path):
    p = ImmutableSecurityPolicy(name="p1", block_network=False)
    p.seal(KEY)
    path = tmp_path / "policy.json"
    p.save(path, KEY)

    # 篡改磁盘副本 (改 block_network), 签名不匹配 -> 拒绝
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["policy"]["block_network"] = True
    tampered = tmp_path / "policy_tampered.json"
    tampered.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(PolicyViolation):
        ImmutableSecurityPolicy.load(tampered, KEY)

    # 原始文件 (未篡改) 正确主密钥可加载
    p2 = ImmutableSecurityPolicy.load(path, KEY)
    assert p2.block_network is False
    assert p2.verify(KEY)


def test_policy_default_allowlist_lets_readonly():
    p = ImmutableSecurityPolicy(name="ro")
    p.seal(KEY)
    assert p.allows_binary("echo")
    assert p.allows_binary("cat")
    # 未授权二进制被拒
    assert not p.allows_binary("rm")
    assert not p.allows_binary("curl")


def test_sandbox_denies_disallowed_binary():
    p = ImmutableSecurityPolicy(name="strict", deny_binaries=["python"])
    p.seal(KEY)
    sb = SyscallSandbox(p)
    with pytest.raises(PolicyViolation):
        sb.run(["python", "-c", "print(1)"])


def test_sandbox_runs_allowed_command():
    p = ImmutableSecurityPolicy(name="ok", allow_binaries=["python"])
    p.seal(KEY)
    sb = SyscallSandbox(p)
    res = sb.run([sys.executable, "-c", "print('hi-from-sandbox')"], timeout=30)
    assert res.returncode == 0
    assert "hi-from-sandbox" in (res.stdout or "")


def test_vault_roundtrip_text():
    v = CryptoVault(passphrase="hunter2", iterations=50_000)
    env = v.seal_text("secret-context-state")
    assert "ct_b64" in env and env["alg"] in ("AES-256-GCM", "CTR-HMAC")
    assert v.open_text(env) == "secret-context-state"


def test_vault_roundtrip_bytes_and_file(tmp_path):
    v = CryptoVault(raw_key=os.urandom(32))
    blob = os.urandom(2000)
    env = v.seal_bytes(blob)
    assert v.open_bytes(env) == blob
    src = tmp_path / "plain.bin"
    dst = tmp_path / "enc.json"
    src.write_bytes(blob)
    v.seal_file(src, dst)
    assert dst.exists()
    out = tmp_path / "dec.bin"
    v.open_file(dst, out)
    assert out.read_bytes() == blob
