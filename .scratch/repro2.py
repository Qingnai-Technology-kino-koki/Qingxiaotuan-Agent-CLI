import sys, json
sys.path.insert(0, r"E:/Qingxiaotuan Agent CLI")
from qingxiaotuan.core.ipc_client import ExternalEngineManager

mgr = ExternalEngineManager({}, quiet=False)
yaml = (
    "- id: no-todo\n"
    "  severity: error\n"
    '  match:/n    path: "*.py"\n'
    '  assert: \'not contains(content, "TODO")\'\n'
    "  message: found TODO\n"
)
print("--- rules load ---", flush=True)
print(mgr.call("rules", "load", {"rules_yaml": yaml}, timeout=20), flush=True)
print("--- rules check (has TODO) ---", flush=True)
print(mgr.call("rules", "check", {"path": "app.py", "content": "x = 1 # TODO fix"}, timeout=20), flush=True)
print("--- rules check (clean) ---", flush=True)
print(mgr.call("rules", "check", {"path": "app.py", "content": "x = 1"}, timeout=20), flush=True)
print("--- skill search ---", flush=True)
print(mgr.call("skill-market", "search", {"query": "zzz-no-such-skill"}, timeout=20), flush=True)
mgr.close_all()
