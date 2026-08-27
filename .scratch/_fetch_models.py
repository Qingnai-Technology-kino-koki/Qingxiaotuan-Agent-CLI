import json
import urllib.request

def fetch(url, headers=None, timeout=60):
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))

# OpenRouter
try:
    or_data = fetch("https://openrouter.ai/api/v1/models")
    or_models = or_data.get("data", [])
    print("OpenRouter total:", len(or_models))
    out = []
    for m in or_models:
        pricing = m.get("pricing", {}) or {}
        prompt = pricing.get("prompt")
        completion = pricing.get("completion")
        try:
            p = float(prompt) if prompt else 0.0
            c = float(completion) if completion else 0.0
        except (TypeError, ValueError):
            p = c = -1.0
        free = (p == 0.0 and c == 0.0)
        out.append({"id": m.get("id"), "free": free, "prompt": p, "completion": c})
    with open(r"e:\Qingxiaotuan Agent CLI\_or_models.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    n_free = sum(1 for x in out if x["free"])
    print("  free:", n_free, "paid:", len(out) - n_free)
except Exception as e:
    print("OpenRouter fetch failed:", type(e).__name__, e)

# NVIDIA NIM
try:
    nim_data = fetch("https://integrate.api.nvidia.com/v1/models")
    nim_models = nim_data.get("data", [])
    print("NVIDIA NIM total:", len(nim_models))
    out2 = []
    for m in nim_models:
        out2.append({"id": m.get("id")})
    with open(r"e:\Qingxiaotuan Agent CLI\_nim_models.json", "w", encoding="utf-8") as f:
        json.dump(out2, f, ensure_ascii=False, indent=1)
    print("  sample:", [x["id"] for x in out2[:5]])
except Exception as e:
    print("NIM fetch failed:", type(e).__name__, e)
