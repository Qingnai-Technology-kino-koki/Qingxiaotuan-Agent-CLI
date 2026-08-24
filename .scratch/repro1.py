import sys, json
sys.path.insert(0, r"E:/Qingxiaotuan Agent CLI")
from qingxiaotuan.core.ipc_client import ExternalEngineManager

mgr = ExternalEngineManager({}, quiet=False)
print("avail:", mgr.available(), flush=True)

print("--- crypto derive ---", flush=True)
d = mgr.call("crypto", "derive", {"passphrase": "pw"}, timeout=20)
print("derive:", d, flush=True)
print("--- crypto seal ---", flush=True)
s = mgr.call("crypto", "seal", {"passphrase": "pw", "salt_b64": d["salt_b64"], "plaintext": "青小团秘密"}, timeout=20)
print("seal:", s, flush=True)
print("--- crypto unseal ---", flush=True)
u = mgr.call("crypto", "unseal", {"passphrase": "pw", "salt_b64": d["salt_b64"], "blob": s["blob"]}, timeout=20)
print("unseal:", u, flush=True)
mgr.close_all()
