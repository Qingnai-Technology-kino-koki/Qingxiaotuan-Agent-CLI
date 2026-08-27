"""按文件分批运行 pytest, 每个文件带超时, 汇总结果。"""
import subprocess
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(r"e:\Qingxiaotuan Agent CLI")
PY = ROOT / ".venv" / "Scripts" / "python.exe"
TESTS = sorted((ROOT / "tests").glob("test_*.py"))
TIMEOUT = 180  # 每个文件 180s


def main():
    results = []
    for t in TESTS:
        start = time.time()
        try:
            p = subprocess.run(
                [str(PY), "-m", "pytest", str(t), "-q", "--tb=no",
                 "-p", "no:cacheprovider", "--basetemp", str(ROOT / ".pytest_tmp")],
                capture_output=True, text=True, timeout=TIMEOUT,
            )
            elapsed = time.time() - start
            tail = (p.stdout or "") + (p.stderr or "")
            summary = ""
            for line in tail.splitlines():
                if "passed" in line or "failed" in line or "error" in line:
                    summary = line.strip()
            results.append((t.name, p.returncode, elapsed, summary))
        except subprocess.TimeoutExpired:
            results.append((t.name, -1, time.time() - start, "TIMEOUT"))

    print(f"{'FILE':<42} {'RC':>4} {'TIME':>7}  SUMMARY", flush=True)
    print("-" * 100, flush=True)
    for name, rc, elapsed, summary in results:
        flag = "OK " if rc == 0 else "FAIL" if rc == 1 else "ERR "
        print(f"{name:<42} {flag} {elapsed:6.1f}s  {summary}", flush=True)

    fails = [r for r in results if r[1] != 0]
    print(f"\n总计: {len(results)} 文件, {len(fails)} 个失败/超时", flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        sys.exit(2)
