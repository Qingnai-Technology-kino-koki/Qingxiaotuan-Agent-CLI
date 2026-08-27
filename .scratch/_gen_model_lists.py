"""生成 model_lists.py — 从权威 API 数据 + 精选清单合并, 统一标注付费/免费。

数据来源:
- OpenRouter: 官方 /api/v1/models (419 个, 21 免费) — 精确
- NVIDIA NIM: 官方 /v1/models (95 个) — 精确
- Novita: 官方 /v3/openai/models (150 个) — 精确
- SiliconFlow/Together/Fireworks/Groq: 精选清单 + 官方文档免费标注
- 直连平台: 精选清单 (仅当前支持模型), 全部付费
"""
import json
import sys
from pathlib import Path

ROOT = Path(r"e:\Qingxiaotuan Agent CLI")
OUT = ROOT / "qingxiaotuan" / "models" / "model_lists.py"

or_models = json.loads((ROOT / "_or_models.json").read_text(encoding="utf-8"))
nim_models = json.loads((ROOT / "_nim_models.json").read_text(encoding="utf-8"))
novita_models = json.loads((ROOT / "_novita_models.json").read_text(encoding="utf-8"))

sys.path.insert(0, str(ROOT))
from qingxiaotuan.models.model_lists import MODEL_LISTS as CURRENT  # noqa: E402

MODEL_LISTS = {}

# ---------------------------------------------------------------- OpenRouter (419, 21 免费)
MODEL_LISTS["openrouter"] = [
    f"{m['id']}|{'free' if m['free'] else 'paid'}" for m in or_models
]

# ---------------------------------------------------------------- NVIDIA NIM (95, 全部付费)
MODEL_LISTS["nvidia-nim"] = [f"{m['id']}|paid" for m in nim_models]

# ---------------------------------------------------------------- Novita (150, 全部付费)
MODEL_LISTS["novita"] = [f"{m['id']}|paid" for m in novita_models]

# ---------------------------------------------------------------- SiliconFlow (精选 + 免费标注)
SILICONFLOW_FREE = {
    "deepseek-ai/DeepSeek-R1-Distill-Qwen-7B",
    "deepseek-ai/DeepSeek-R1-Distill-Qwen-1.5B",
    "THUDM/GLM-Z1-9B-0414",
    "THUDM/GLM-4-9B-0414",
    "THUDM/glm-4-9b-chat",
    "THUDM/chatglm3-6b",
    "Qwen/Qwen2-7B-Instruct",
    "Qwen/Qwen2-1.5B-Instruct",
    "Qwen/Qwen1.5-7B-Chat",
    "BAAI/bge-m3",
    "BAAI/bge-reranker-v2-m3",
    "netease-youdao/bce-embedding-base_v1",
    "netease-youdao/bce-reranker-base_v1",
    "Kwai-Kolors/Kolors",
    "InternLM/internlm2_5-7b-chat",
    "Mistral-7B-Instruct-v0.2",
}
SILICONFLOW_EXTRA_FREE = [
    "Qwen/Qwen2-7B-Instruct",
    "Qwen/Qwen2-1.5B-Instruct",
    "Qwen/Qwen1.5-7B-Chat",
    "BAAI/bge-m3",
    "BAAI/bge-reranker-v2-m3",
    "netease-youdao/bce-embedding-base_v1",
    "netease-youdao/bce-reranker-base_v1",
    "Kwai-Kolors/Kolors",
    "InternLM/internlm2_5-7b-chat",
    "Mistral-7B-Instruct-v0.2",
]
siliconflow = []
seen_sf = set()
for m in CURRENT.get("siliconflow", []):
    if m in seen_sf:
        continue
    seen_sf.add(m)
    label = "free" if m in SILICONFLOW_FREE else "paid"
    siliconflow.append(f"{m}|{label}")
for m in SILICONFLOW_EXTRA_FREE:
    if m not in seen_sf:
        seen_sf.add(m)
        siliconflow.append(f"{m}|free")
MODEL_LISTS["siliconflow"] = siliconflow

# ---------------------------------------------------------------- Together (全部付费)
MODEL_LISTS["together"] = [f"{m}|paid" for m in CURRENT.get("together", [])]

# ---------------------------------------------------------------- Fireworks (全部付费)
MODEL_LISTS["fireworks"] = [f"{m}|paid" for m in CURRENT.get("fireworks", [])]

# ---------------------------------------------------------------- Groq (全部免费)
GROQ_EXTRA = [
    "llama-3.1-70b-versatile",
    "llama-3.1-405b-reasoning",
    "mixtral-8x7b-32768",
    "mixtral-8x22b-instruct",
    "gemma-7b-it",
    "gemma2-9b-it",
    "qwen-2.5-72b-instruct",
    "qwen-2.5-32b-instruct",
    "qwen-2.5-14b-instruct",
    "qwen-2.5-7b-instruct",
    "deepseek-r1-distill-llama-8b",
    "deepseek-chat",
    "meta-llama/llama-prompt-guard-2-86m",
    "canopylabs/orpheus-arabic-saudi",
]
groq = list(CURRENT.get("groq", []))
for m in GROQ_EXTRA:
    if m not in groq:
        groq.append(m)
MODEL_LISTS["groq"] = [f"{m}|free" for m in groq]

# ---------------------------------------------------------------- 直连平台 (全部付费)
# Gemini 补充 agent 确认的新模型
GEMINI_EXTRA = ["gemini-3.7-flash", "gemini-3.5-flash", "gemini-3.1-pro-preview", "gemini-3.1-flash-lite"]
gemini = list(CURRENT.get("gemini", []))
for m in GEMINI_EXTRA:
    if m not in gemini:
        gemini.insert(0, m)
MODEL_LISTS["gemini"] = [f"{m}|paid" for m in gemini]

# OpenAI 补充 agent 确认的新模型
OPENAI_EXTRA = ["gpt-5.6-sol-pro", "gpt-5.6-luna-mini"]
openai = list(CURRENT.get("openai", []))
for m in OPENAI_EXTRA:
    if m not in openai:
        openai.append(m)
MODEL_LISTS["openai"] = [f"{m}|paid" for m in openai]

for name in ["deepseek", "anthropic", "qwen", "moonshot", "zhipu", "doubao"]:
    MODEL_LISTS[name] = [f"{m}|paid" for m in CURRENT.get(name, [])]

# ---------------------------------------------------------------- 写文件
def _fmt_list(name: str, models: list) -> str:
    lines = [f'    "{name}": [']
    for m in models:
        lines.append(f'        "{m}",')
    lines.append("    ],")
    return "\n".join(lines)

parts = []
parts.append('"""各供应商当前可选模型清单 (2026-08 调研整理, 自动生成)。')
parts.append("")
parts.append("设计原则:")
parts.append("- 聚合平台 (OpenRouter / SiliconFlow / Novita / Together / Fireworks / Groq / NVIDIA NIM):")
parts.append("  一个 Key 通吃, 模型 ID 为各平台 API 调用时的精确字符串, 每个模型标注 |free 或 |paid。")
parts.append("- 直连平台 (DeepSeek / OpenAI / Anthropic / Gemini / 通义 / Kimi / 智谱 / 豆包):")
parts.append("  只列官方当前仍支持、可调用的模型; 已下架/停用的老模型一律剔除。")
parts.append("- 每个清单的第一项为推荐默认模型, 交互选择时默认落在第一项。")
parts.append("")
parts.append('来源: 各平台官方模型 API 与文档 (openrouter.ai / docs.siliconflow.cn / novita.ai /')
parts.append('api.together.xyz / fireworks.ai / console.groq.com / docs.api.nvidia.com /')
parts.append('platform.deepseek.com / platform.openai.com / docs.anthropic.com / ai.google.dev /')
parts.append('help.aliyun.com / platform.moonshot.cn / docs.bigmodel.cn / docs.volcengine.com)。')
parts.append('"""')
parts.append("")
parts.append("from typing import Dict, List")
parts.append("")
parts.append("MODEL_LISTS: Dict[str, List[str]] = {")

blocks = []
for name in MODEL_LISTS:
    blocks.append(_fmt_list(name, MODEL_LISTS[name]))
parts.append("\n".join(blocks))
parts.append("}")

OUT.write_text("\n".join(parts) + "\n", encoding="utf-8")
print("written:", OUT)
for name, models in MODEL_LISTS.items():
    free = sum(1 for m in models if m.endswith("|free"))
    print(f"  {name:<14s} {len(models):>4d} 个  (free={free})")
