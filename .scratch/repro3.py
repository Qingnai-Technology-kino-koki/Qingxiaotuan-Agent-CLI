import sys, time
sys.path.insert(0, r"E:\Qingxiaotuan Agent CLI")
from qingxiaotuan.core.ipc_client import ExternalEngineManager

mgr = ExternalEngineManager({}, quiet=False)
print("avail:", mgr.available(), flush=True)
for eng in ["rules","skill-market","crypto","index","ansi","diff","plugin-host","mcp-client","dashboard","agent-sdk"]:
    try:
        meta = mgr.call(eng, "_meta", {}, timeout=15)
        print(f"{eng:12s} OK  methods={len(meta.get('methods',[]))}", flush=True)
    except Exception as e:
        print(f"{eng:12s} FAIL {e}", flush=True)
mgr.close_all()
