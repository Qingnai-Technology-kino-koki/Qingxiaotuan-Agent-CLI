import sys
sys.path.insert(0, r"E:\Qingxiaotuan Agent CLI")
from qingxiaotuan.core.ipc_client import IpcProcess
p = IpcProcess([r"E:\Qingxiaotuan Agent CLI\ext\dist\bin\qxt_index.exe"])
p.request("build", {"root": r"E:\Qingxiaotuan Agent CLI\ext\ts\src", "max_files": 100})
q = p.request("query", {"symbol": "register"})
print("symbol=register count:", q.get("count"))
for h in q.get("hits", [])[:3]: print("  ", h["file"], h["text"][:40])
q2 = p.request("query", {"path": "*.ts"})
print("path=*.ts count:", q2.get("count"))
q3 = p.request("query", {"lang": "Python"})
print("lang=Python count:", q3.get("count"))
p.close()
