"""Run a batch of test files with timeout, append results to .scratch/test_results.txt"""
import subprocess
import sys
from pathlib import Path

VENV = Path(".venv/Scripts/python.exe").resolve()
batch = sys.argv[1]
files = sorted(Path("tests").glob("test_*.py"))
step = 15
start_i = int(batch) * step
chunk = files[start_i:start_i + step]
lines = []
for f in chunk:
    out_f = Path(".scratch") / f"{f.stem}.out"
    try:
        with out_f.open("w", encoding="utf-8") as fo:
            r = subprocess.run(
                [str(VENV), "-m", "pytest", str(f), "-q", "-p", "no:cacheprovider", "--tb=line",
                 "--basetemp", str(Path(".scratch/ptmp").resolve())],
                stdout=fo, stderr=subprocess.STDOUT, timeout=45,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        tail = out_f.read_text(encoding="utf-8").strip().splitlines()
        tail = tail[-1] if tail else ""
        status = "PASS" if r.returncode == 0 else "FAIL"
        lines.append(f"{f.name}: {status} rc={r.returncode} {tail}")
    except subprocess.TimeoutExpired:
        lines.append(f"{f.name}: HANG (>45s)")
    except Exception as e:
        lines.append(f"{f.name}: ERROR {e}")
Path(".scratch/test_results.txt").open("a", encoding="utf-8").write("\n".join(lines) + "\n")
print("\n".join(lines))
