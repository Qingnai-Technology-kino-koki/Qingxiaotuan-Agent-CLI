import sys
sys.path.insert(0, r"E:\Qingxiaotuan Agent CLI")
from qingxiaotuan.core.ipc_client import IpcProcess
p = IpcProcess([r"E:\Qingxiaotuan Agent CLI\ext\dist\bin\qxt_index.exe"])
b = p.request("build", {"root": r"E:\Qingxiaotuan Agent CLI\ext\ts\src", "max_files": 100})
print("build files:", b.get("files"), "symbols:", b.get("symbol_count"))
q = p.request("query", {"lang": "TypeScript", "symbol": "IpcServer"})
print("query count:", q.get("count"))
for h in q.get("hits", [])[:5]:
    print("  ", h["file"], h["line"], h.get("lang"))
q2 = p.request("query", {"path": "*.ts", "symbol": "register"})
print("query2 (path=*.ts) count:", q2.get("count"))
p.close()
