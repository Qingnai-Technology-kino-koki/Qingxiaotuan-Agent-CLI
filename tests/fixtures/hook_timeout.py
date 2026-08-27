"""fixture: 超时 hook。

sleep 30 秒, 用于测试 HookManager 的超时强杀 (default_timeout 远小于 30)。
"""
import time

time.sleep(30)
