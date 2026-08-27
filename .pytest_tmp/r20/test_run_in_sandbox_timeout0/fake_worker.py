
import json, sys, os
req = json.loads(open(sys.argv[1], encoding='utf-8').read())
sandbox = req['workspace']
# 在沙箱里写一个标记文件, 证明 worker 确实跑在沙箱内
with open(os.path.join(sandbox, '.sandbox_ran'), 'w', encoding='utf-8') as f:
    f.write('ran')
if 5:
    import time; time.sleep(5)
result = {
    "ok": True,
    "output": "fake-output:" + req['task'][:20],
    "turns": 1,
    "error": "fake-fail" if False else None,
    "tool_calls": []
}
open(sys.argv[2], 'w', encoding='utf-8').write(json.dumps(result, ensure_ascii=False))
sys.exit(0)
