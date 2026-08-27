import sys, threading
sys.path.insert(0, r"e:\Qingxiaotuan Agent CLI")
from qingxiaotuan.ui.fullscreen import FullScreenTUI

tui = FullScreenTUI(lambda _text: "ok")
for t in threading.enumerate():
    if t.name != "MainThread":
        print("THREAD:", t.name, "daemon=", t.daemon, "alive=", t.is_alive())
print("--- after close ---")
tui.close()
for t in threading.enumerate():
    if t.name != "MainThread":
        print("THREAD:", t.name, "daemon=", t.daemon, "alive=", t.is_alive())
print("--- after app.exit ---")
try:
    tui._app.exit()
except Exception as e:
    print("exit err:", e)
import time
time.sleep(0.3)
for t in threading.enumerate():
    if t.name != "MainThread":
        print("THREAD:", t.name, "daemon=", t.daemon, "alive=", t.is_alive())
