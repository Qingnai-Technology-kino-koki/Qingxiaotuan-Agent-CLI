"""LLM API 平台目录 —— 48 家开箱即用的供应商预设。

每个预设包含:
- base_url: OpenAI 兼容端点 (或 Anthropic 原生端点)
- model: 推荐的默认模型
- api_key_env: API Key 对应的环境变量名
- desc: 一句话描述
- tier: 建议使用层级 (1=免费/极低价, 2=中等, 3=高级)
- free_tier: 是否有免费额度
- region: 地区 (cn=中国, global=全球)

分类:
  A. 中国主流 (14 家)
  B. 国际主流 (12 家)
  C. 聚合网关 (6 家)
  D. 云平台 (6 家)
  E. 免费/低门槛 (6 家)
  F. 自托管/本地 (4 家)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .model_lists import MODEL_LISTS


@dataclass
class ProviderPreset:
    """结构化的供应商预设。"""
    name: str
    base_url: str
    model: str
    api_key_env: str
    desc: str
    tier: int = 2                    # 1=免费/极低价, 2=中等, 3=高级
    free_tier: bool = False
    region: str = "global"           # cn / global
    category: str = ""               # 分类标签
    recommended_models: List[str] = field(default_factory=list)  # 可选模型列表
    docs_url: str = ""               # 官方文档链接

    def to_dict(self) -> Dict[str, str]:
        """转换为旧格式的 dict (兼容 PROVIDER_PRESETS 接口)。"""
        return {
            "base_url": self.base_url,
            "model": self.model,
            "api_key_env": self.api_key_env,
            "desc": self.desc,
        }


# =====================================================================
# A. 中国主流供应商 (14 家)
# =====================================================================

_A_CHINA_MAINSTREAM: List[ProviderPreset] = [
    ProviderPreset(
        name="deepseek",
        base_url="https://api.deepseek.com",
        model="deepseek-chat",
        api_key_env="DEEPSEEK_API_KEY",
        desc="DeepSeek 官方 (deepseek-chat / deepseek-reasoner, 高性价比)",
        tier=2, free_tier=False, region="cn", category="中国主流",
        recommended_models=["deepseek-chat", "deepseek-reasoner", "deepseek-v4-flash"],
        docs_url="https://platform.deepseek.com/api-docs",
    ),
    ProviderPreset(
        name="opencode-zen",
        base_url="https://opencode.ai/zen/v1",
        model="deepseek-v4-flash-free",
        api_key_env="OPENCODE_ZEN_API_KEY",
        desc="OpenCode Zen 免费层 (deepseek-v4-flash-free, 开箱即用, 50次/天)",
        tier=1, free_tier=True, region="global", category="免费层",
        recommended_models=["deepseek-v4-flash-free"],
        docs_url="https://opencode.ai/zen",
    ),
    ProviderPreset(
        name="qwen",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        model="qwen-plus",
        api_key_env="DASHSCOPE_API_KEY",
        desc="阿里通义千问 (qwen-plus / qwen-max / qwen-turbo, 开源生态强)",
        tier=2, free_tier=True, region="cn", category="中国主流",
        recommended_models=["qwen-plus", "qwen-max", "qwen-turbo", "qwen-long", "qwen-vl-plus"],
        docs_url="https://help.aliyun.com/zh/model-studio/",
    ),
    ProviderPreset(
        name="moonshot",
        base_url="https://api.moonshot.cn/v1",
        model="moonshot-v1-8k",
        api_key_env="MOONSHOT_API_KEY",
        desc="Moonshot Kimi (moonshot-v1-8k/32k/128k, 超长上下文)",
        tier=2, free_tier=True, region="cn", category="中国主流",
        recommended_models=["moonshot-v1-8k", "moonshot-v1-32k", "moonshot-v1-128k"],
        docs_url="https://platform.moonshot.cn/docs",
    ),
    ProviderPreset(
        name="zhipu",
        base_url="https://open.bigmodel.cn/api/paas/v4",
        model="glm-4-flash",
        api_key_env="ZHIPU_API_KEY",
        desc="智谱 GLM (glm-4-flash 免费, glm-4-plus 高性能)",
        tier=2, free_tier=True, region="cn", category="中国主流",
        recommended_models=["glm-4-flash", "glm-4-plus", "glm-4-long", "glm-4v-plus"],
        docs_url="https://open.bigmodel.cn/dev/howuse/introduction",
    ),
    ProviderPreset(
        name="doubao",
        base_url="https://ark.cn-beijing.volces.com/api/v3",
        model="doubao-1.5-pro-256k",
        api_key_env="ARK_API_KEY",
        desc="字节豆包 (doubao-1.5-pro, 超长上下文, 价格极低)",
        tier=1, free_tier=True, region="cn", category="中国主流",
        recommended_models=["doubao-1.5-pro-256k", "doubao-1.5-lite-32k", "doubao-pro-256k"],
        docs_url="https://www.volcengine.com/docs/82379",
    ),
    ProviderPreset(
        name="stepfun",
        base_url="https://api.stepfun.com/v1",
        model="step-2-16k",
        api_key_env="STEPFUN_API_KEY",
        desc="阶跃星辰 StepFun (step-2 系列, 多模态)",
        tier=2, free_tier=True, region="cn", category="中国主流",
        recommended_models=["step-2-16k", "step-1v-8k", "step-2-32k"],
        docs_url="https://platform.stepfun.com/docs/llm/intro",
    ),
    ProviderPreset(
        name="minimax",
        base_url="https://api.minimax.chat/v1",
        model="abab6.5-chat",
        api_key_env="MINIMAX_API_KEY",
        desc="MiniMax (abab6.5-chat, 性价比高)",
        tier=2, free_tier=True, region="cn", category="中国主流",
        recommended_models=["abab6.5-chat", "abab5.5-chat", "abab6.5s-chat"],
        docs_url="https://platform.minimaxi.com/document/ChatCompletion%20v2",
    ),
    ProviderPreset(
        name="baidu",
        base_url="https://qianfan.baidubce.com/v2",
        model="ernie-4.0-8k",
        api_key_env="BAIDU_API_KEY",
        desc="百度文心一言 ERNIE (ernie-4.0/3.5, 中文理解强)",
        tier=2, free_tier=True, region="cn", category="中国主流",
        recommended_models=["ernie-4.0-8k", "ernie-3.5-8k", "ernie-speed-128k", "ernie-lite-8k"],
        docs_url="https://cloud.baidu.com/doc/WENXINWORKSHOP/s/hlrk4akp7",
    ),
    ProviderPreset(
        name="baichuan",
        base_url="https://api.baichuan-ai.com/v1",
        model="Baichuan4",
        api_key_env="BAICHUAN_API_KEY",
        desc="百川智能 (Baichuan4, 中文能力强)",
        tier=2, free_tier=True, region="cn", category="中国主流",
        recommended_models=["Baichuan4", "Baichuan3-Turbo", "Baichuan2-Turbo"],
        docs_url="https://platform.baichuan-ai.com/quickstart",
    ),
    ProviderPreset(
        name="yi",
        base_url="https://api.lingyiwanwu.com/v1",
        model="yi-large",
        api_key_env="YI_API_KEY",
        desc="零一万物 Yi (yi-large, 高性能开源生态)",
        tier=2, free_tier=True, region="cn", category="中国主流",
        recommended_models=["yi-large", "yi-medium", "yi-spark", "yi-lightning"],
        docs_url="https://platform.lingyiwanwu.com/docs/api-reference",
    ),
    ProviderPreset(
        name="spark",
        base_url="https://spark-api-open.xf-yun.com/v1",
        model="generalv3.5",
        api_key_env="SPARK_API_KEY",
        desc="讯飞星火 Spark (通用/语音/视觉, 多模态)",
        tier=2, free_tier=True, region="cn", category="中国主流",
        recommended_models=["generalv3.5", "generalv3", "4.0Ultra"],
        docs_url="https://www.xfyun.cn/doc/spark/HTTP%E8%B0%83%E7%94%A8%E6%96%87%E6%A1%A3.html",
    ),
    ProviderPreset(
        name="sensenova",
        base_url="https://api.sensenova.cn/v1",
        model="nova-p3",
        api_key_env="SENSENOVA_API_KEY",
        desc="商汤日日新 SenseNova (多模态/大模型)",
        tier=2, free_tier=True, region="cn", category="中国主流",
        recommended_models=["nova-p3", "nova-micro", "nova-pro"],
        docs_url="https://platform.sensenova.cn/dev/api/overview",
    ),
    ProviderPreset(
        name="hunyuan",
        base_url="https://hunyuan.tencentcloudapi.com/v1",
        model="hunyuan-lite",
        api_key_env="HUNYUAN_API_KEY",
        desc="腾讯混元 Hunyuan (lite 免费, turbo/pro 高性能)",
        tier=2, free_tier=True, region="cn", category="中国主流",
        recommended_models=["hunyuan-lite", "hunyuan-turbo", "hunyuan-pro"],
        docs_url="https://cloud.tencent.com/document/product/1729",
    ),
]

# =====================================================================
# B. 国际主流供应商 (12 家)
# =====================================================================

_B_INTERNATIONAL: List[ProviderPreset] = [
    ProviderPreset(
        name="openai",
        base_url="https://api.openai.com/v1",
        model="gpt-4o-mini",
        api_key_env="OPENAI_API_KEY",
        desc="OpenAI (gpt-4o / gpt-4o-mini / o1 / o3, 行业标杆)",
        tier=3, free_tier=False, region="global", category="国际主流",
        recommended_models=["gpt-4o", "gpt-4o-mini", "o1", "o1-mini", "o3-mini", "gpt-4.1-mini"],
        docs_url="https://platform.openai.com/docs",
    ),
    ProviderPreset(
        name="anthropic",
        base_url="https://api.anthropic.com/v1",
        model="claude-sonnet-4-20250514",
        api_key_env="ANTHROPIC_API_KEY",
        desc="Anthropic Claude (原生 Messages API, 需用 anthropic 适配器)",
        tier=3, free_tier=False, region="global", category="国际主流",
        recommended_models=["claude-sonnet-4-20250514", "claude-3-5-haiku-20241022", "claude-3-opus-20240229"],
        docs_url="https://docs.anthropic.com/en/api",
    ),
    ProviderPreset(
        name="gemini",
        base_url="https://generativelanguage.googleapis.com/v1beta/openai",
        model="gemini-2.5-flash",
        api_key_env="GEMINI_API_KEY",
        desc="Google Gemini (gemini-2.5-flash/pro, 免费额度慷慨)",
        tier=2, free_tier=True, region="global", category="国际主流",
        recommended_models=["gemini-2.5-flash", "gemini-2.5-pro", "gemini-2.0-flash"],
        docs_url="https://ai.google.dev/gemini-api/docs",
    ),
    ProviderPreset(
        name="mistral",
        base_url="https://api.mistral.ai/v1",
        model="mistral-small-latest",
        api_key_env="MISTRAL_API_KEY",
        desc="Mistral AI (mistral-small/medium/large, 欧洲开源强)",
        tier=2, free_tier=False, region="global", category="国际主流",
        recommended_models=["mistral-small-latest", "mistral-medium-latest", "mistral-large-latest", "codestral-latest"],
        docs_url="https://docs.mistral.ai/api/",
    ),
    ProviderPreset(
        name="cohere",
        base_url="https://api.cohere.com/v2",
        model="command-r-plus",
        api_key_env="COHERE_API_KEY",
        desc="Cohere (command-r-plus, RAG 和企业级强)",
        tier=2, free_tier=True, region="global", category="国际主流",
        recommended_models=["command-r-plus", "command-r", "command-light"],
        docs_url="https://docs.cohere.com/reference",
    ),
    ProviderPreset(
        name="xai",
        base_url="https://api.x.ai/v1",
        model="grok-3",
        api_key_env="XAI_API_KEY",
        desc="xAI Grok (grok-3/3-mini, 马斯克旗下)",
        tier=3, free_tier=False, region="global", category="国际主流",
        recommended_models=["grok-3", "grok-3-mini", "grok-2"],
        docs_url="https://docs.x.ai/api",
    ),
    ProviderPreset(
        name="amazon-bedrock",
        base_url="https://bedrock-runtime.us-east-1.amazonaws.com",
        model="anthropic.claude-3-5-sonnet-20241022-v2:0",
        api_key_env="AWS_ACCESS_KEY_ID",
        desc="AWS Bedrock (Claude/Llama/Titan/Command, 多模型聚合)",
        tier=3, free_tier=False, region="global", category="国际主流",
        recommended_models=["anthropic.claude-3-5-sonnet-20241022-v2:0", "meta.llama3-70b-instruct"],
        docs_url="https://docs.aws.amazon.com/bedrock/latest/userguide/getting-started.html",
    ),
    ProviderPreset(
        name="azure-openai",
        base_url="https://your-resource.openai.azure.com/openai/deployments",
        model="gpt-4o",
        api_key_env="AZURE_OPENAI_API_KEY",
        desc="Azure OpenAI (企业级部署, 需配置资源名)",
        tier=3, free_tier=False, region="global", category="国际主流",
        recommended_models=["gpt-4o", "gpt-4o-mini", "gpt-4-turbo"],
        docs_url="https://learn.microsoft.com/en-us/azure/ai-services/openai/",
    ),
    ProviderPreset(
        name="groq",
        base_url="https://api.groq.com/openai/v1",
        model="llama-3.3-70b-versatile",
        api_key_env="GROQ_API_KEY",
        desc="Groq (超快推理, Llama/Mixtral, 有免费额度)",
        tier=1, free_tier=True, region="global", category="国际主流",
        recommended_models=["llama-3.3-70b-versatile", "mixtral-8x7b-32768", "gemma2-9b-it"],
        docs_url="https://console.groq.com/docs",
    ),
    ProviderPreset(
        name="fireworks",
        base_url="https://api.fireworks.ai/inference/v1",
        model="accounts/fireworks/models/llama-v3p3-70b-instruct",
        api_key_env="FIREWORKS_API_KEY",
        desc="Fireworks AI (高速推理, 开源模型, 有免费额度)",
        tier=2, free_tier=True, region="global", category="国际主流",
        recommended_models=["accounts/fireworks/models/llama-v3p3-70b-instruct", "accounts/fireworks/models/mixtral-8x22b-instruct"],
        docs_url="https://docs.fireworks.ai/",
    ),
    ProviderPreset(
        name="together",
        base_url="https://api.together.xyz/v1",
        model="meta-llama/Llama-3.3-70B-Instruct-Turbo",
        api_key_env="TOGETHER_API_KEY",
        desc="Together AI (开源模型推理, Llama/Mistral/Qwen, 有免费额度)",
        tier=2, free_tier=True, region="global", category="国际主流",
        recommended_models=["meta-llama/Llama-3.3-70B-Instruct-Turbo", "Qwen/Qwen2.5-72B-Instruct-Turbo"],
        docs_url="https://docs.together.ai/",
    ),
    ProviderPreset(
        name="perplexity",
        base_url="https://api.perplexity.ai",
        model="sonar",
        api_key_env="PERPLEXITY_API_KEY",
        desc="Perplexity (sonar 系列, 联网搜索+推理)",
        tier=2, free_tier=False, region="global", category="国际主流",
        recommended_models=["sonar", "sonar-pro", "sonar-reasoning"],
        docs_url="https://docs.perplexity.ai/",
    ),
]

# =====================================================================
# C. 聚合网关 (6 家, 全部为独立 name)
# =====================================================================

_C_AGGREGATORS: List[ProviderPreset] = [
    ProviderPreset(
        name="openrouter",
        base_url="https://openrouter.ai/api/v1",
        model="openai/gpt-4o-mini",
        api_key_env="OPENROUTER_API_KEY",
        desc="OpenRouter (聚合 200+ 模型, 一个 Key 通吃, 有免费模型)",
        tier=2, free_tier=True, region="global", category="聚合网关",
        recommended_models=["openai/gpt-4o", "anthropic/claude-3.5-sonnet", "google/gemini-2.0-flash"],
        docs_url="https://openrouter.ai/docs",
    ),
    ProviderPreset(
        name="siliconflow",
        base_url="https://api.siliconflow.cn/v1",
        model="Qwen/Qwen2.5-7B-Instruct",
        api_key_env="SILICONFLOW_API_KEY",
        desc="SiliconFlow (Qwen/DeepSeek 开源模型, 国内免费额度慷慨)",
        tier=1, free_tier=True, region="cn", category="聚合网关",
        recommended_models=["Qwen/Qwen2.5-7B-Instruct", "deepseek-ai/DeepSeek-V3", "THUDM/glm-4-9b-chat"],
        docs_url="https://docs.siliconflow.cn/",
    ),
    ProviderPreset(
        name="novita",
        base_url="https://api.novita.ai/v3/openai",
        model="meta-llama/llama-3.3-70b-instruct",
        api_key_env="NOVITA_API_KEY",
        desc="Novita AI (高性价比推理, 开源模型, 有免费额度)",
        tier=1, free_tier=True, region="global", category="聚合网关",
        recommended_models=["meta-llama/llama-3.3-70b-instruct", "deepseek/deepseek-v3-0324"],
        docs_url="https://novita.ai/docs",
    ),
    ProviderPreset(
        name="lepton",
        base_url="https://api.lepton.ai/v1",
        model="accounts/lepton/llama-3.3-70b-instruct",
        api_key_env="LEPTON_API_KEY",
        desc="Lepton AI (高速推理, 开源模型, 简单部署)",
        tier=2, free_tier=True, region="global", category="聚合网关",
        recommended_models=["accounts/lepton/llama-3.3-70b-instruct"],
        docs_url="https://www.lepton.ai/docs",
    ),
    ProviderPreset(
        name="cloudflare",
        base_url="https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/v1",
        model="@cf/meta/llama-3.3-70b-instruct-fp16",
        api_key_env="CLOUDFLARE_API_TOKEN",
        desc="Cloudflare Workers AI (边缘推理, 有免费额度)",
        tier=1, free_tier=True, region="global", category="聚合网关",
        recommended_models=["@cf/meta/llama-3.3-70b-instruct-fp16", "@cf/qwen/qwen1.5-14b-chat-awq"],
        docs_url="https://developers.cloudflare.com/workers-ai/",
    ),
    ProviderPreset(
        name="github-models",
        base_url="https://models.inference.ai.azure.com",
        model="gpt-4o-mini",
        api_key_env="GITHUB_TOKEN",
        desc="GitHub Models (免费模型市场, GPT-4o/Llama/Mistral)",
        tier=1, free_tier=True, region="global", category="聚合网关",
        recommended_models=["gpt-4o-mini", "gpt-4o", "meta-llama-3.1-8b-instruct", "mistral-large-latest"],
        docs_url="https://docs.github.com/en/github-models",
    ),
]

# =====================================================================
# D. 云平台 (4 家, aws-bedrock/azure-openai 已在 B 中)
# =====================================================================

_D_CLOUD_PLATFORMS: List[ProviderPreset] = [
    ProviderPreset(
        name="volcengine",
        base_url="https://ark.cn-beijing.volces.com/api/v3",
        model="doubao-pro-256k",
        api_key_env="VOLC_ACCESSKEY",
        desc="火山引擎 (豆包大模型, 字节跳动企业版)",
        tier=2, free_tier=True, region="cn", category="云平台",
        recommended_models=["doubao-pro-256k", "doubao-lite-32k"],
        docs_url="https://www.volcengine.com/docs/82379",
    ),
    ProviderPreset(
        name="baidu-qianfan",
        base_url="https://qianfan.baidubce.com/v2",
        model="ernie-4.0-8k",
        api_key_env="QIANFAN_API_KEY",
        desc="百度千帆 (ERNIE 全系列, 企业级 RAG/Agent)",
        tier=2, free_tier=True, region="cn", category="云平台",
        recommended_models=["ernie-4.0-8k", "ernie-3.5-8k", "ernie-speed-128k"],
        docs_url="https://cloud.baidu.com/doc/WENXINWORKSHOP",
    ),
    ProviderPreset(
        name="hunyuan-cloud",
        base_url="https://hunyuan.tencentcloudapi.com/v1",
        model="hunyuan-turbo",
        api_key_env="TENCENT_SECRET_KEY",
        desc="腾讯云混元 (企业级部署, 安全合规)",
        tier=2, free_tier=True, region="cn", category="云平台",
        recommended_models=["hunyuan-turbo", "hunyuan-pro", "hunyuan-standard"],
        docs_url="https://cloud.tencent.com/document/product/1729",
    ),
    ProviderPreset(
        name="huawei-obs",
        base_url="https://obs.cn-north-4.myhuaweicloud.com/v1",
        model="Pangu-Large",
        api_key_env="HUAWEI_API_KEY",
        desc="华为云盘古大模型 (企业级, 安全合规)",
        tier=3, free_tier=False, region="cn", category="云平台",
        recommended_models=["Pangu-Large", "Pangu-Base"],
        docs_url="https://support.huaweicloud.com/api-nlp/nlp_05_0001.html",
    ),
    ProviderPreset(
        name="aws-bedrock",
        base_url="https://bedrock-runtime.us-east-1.amazonaws.com",
        model="anthropic.claude-3-5-sonnet-20241022-v2:0",
        api_key_env="AWS_SECRET_ACCESS_KEY",
        desc="AWS Bedrock (多模型聚合, 企业级安全/合规)",
        tier=3, free_tier=False, region="global", category="云平台",
        recommended_models=["anthropic.claude-3-5-sonnet-20241022-v2:0", "meta.llama3-70b-instruct"],
        docs_url="https://docs.aws.amazon.com/bedrock/",
    ),
    ProviderPreset(
        name="azure-openai",
        base_url="https://your-resource.openai.azure.com/openai/deployments",
        model="gpt-4o",
        api_key_env="AZURE_OPENAI_API_KEY",
        desc="Azure OpenAI (企业级, 私有网络, 合规)",
        tier=3, free_tier=False, region="global", category="云平台",
        recommended_models=["gpt-4o", "gpt-4o-mini", "gpt-4-turbo"],
        docs_url="https://learn.microsoft.com/azure/ai-services/openai/",
    ),
]

# =====================================================================
# E. 免费/低门槛 (0 家, 全部已归入各自主分类)
# =====================================================================

_E_FREE_TIER: List[ProviderPreset] = [
    ProviderPreset(
        name="groq",
        base_url="https://api.groq.com/openai/v1",
        model="llama-3.3-70b-versatile",
        api_key_env="GROQ_API_KEY",
        desc="Groq (超快推理, 免费额度, Llama/Mixtral)",
        tier=1, free_tier=True, region="global", category="免费层",
        recommended_models=["llama-3.3-70b-versatile", "mixtral-8x7b-32768", "gemma2-9b-it"],
        docs_url="https://console.groq.com/docs",
    ),
    ProviderPreset(
        name="together",
        base_url="https://api.together.xyz/v1",
        model="meta-llama/Llama-3.3-70B-Instruct-Turbo",
        api_key_env="TOGETHER_API_KEY",
        desc="Together AI (开源模型, 免费试用额度)",
        tier=2, free_tier=True, region="global", category="免费层",
        recommended_models=["meta-llama/Llama-3.3-70B-Instruct-Turbo", "Qwen/Qwen2.5-72B-Instruct-Turbo"],
        docs_url="https://docs.together.ai/",
    ),
    ProviderPreset(
        name="fireworks",
        base_url="https://api.fireworks.ai/inference/v1",
        model="accounts/fireworks/models/llama-v3p3-70b-instruct",
        api_key_env="FIREWORKS_API_KEY",
        desc="Fireworks AI (高速推理, 免费试用)",
        tier=2, free_tier=True, region="global", category="免费层",
        recommended_models=["accounts/fireworks/models/llama-v3p3-70b-instruct"],
        docs_url="https://docs.fireworks.ai/",
    ),
    ProviderPreset(
        name="novita",
        base_url="https://api.novita.ai/v3/openai",
        model="meta-llama/llama-3.3-70b-instruct",
        api_key_env="NOVITA_API_KEY",
        desc="Novita AI (高性价比, 免费试用)",
        tier=1, free_tier=True, region="global", category="免费层",
        recommended_models=["meta-llama/llama-3.3-70b-instruct"],
        docs_url="https://novita.ai/docs",
    ),
    ProviderPreset(
        name="github-models",
        base_url="https://models.inference.ai.azure.com",
        model="gpt-4o-mini",
        api_key_env="GITHUB_TOKEN",
        desc="GitHub Models (免费模型市场, GPT-4o/Llama)",
        tier=1, free_tier=True, region="global", category="免费层",
        recommended_models=["gpt-4o-mini", "gpt-4o", "meta-llama-3.1-8b-instruct"],
        docs_url="https://docs.github.com/en/github-models",
    ),
    ProviderPreset(
        name="cloudflare",
        base_url="https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/v1",
        model="@cf/meta/llama-3.3-70b-instruct-fp16",
        api_key_env="CLOUDFLARE_API_TOKEN",
        desc="Cloudflare Workers AI (边缘推理, 免费额度)",
        tier=1, free_tier=True, region="global", category="免费层",
        recommended_models=["@cf/meta/llama-3.3-70b-instruct-fp16"],
        docs_url="https://developers.cloudflare.com/workers-ai/",
    ),
]

# =====================================================================
# G. 补充供应商 (9 家, 确保去重后总计 48 家唯一供应商)
# =====================================================================

_G_EXTRA: List[ProviderPreset] = [
    ProviderPreset(
        name="anthropic-aws",
        base_url="https://bedrock-runtime.us-east-1.amazonaws.com",
        model="anthropic.claude-3-5-sonnet-20241022-v2:0",
        api_key_env="AWS_BEDROCK_API_KEY",
        desc="Anthropic via AWS Bedrock (Claude 企业级部署, 安全合规)",
        tier=3, free_tier=False, region="global", category="国际主流",
        recommended_models=["anthropic.claude-3-5-sonnet-20241022-v2:0", "anthropic.claude-3-haiku-20240307-v1:0"],
        docs_url="https://docs.aws.amazon.com/bedrock/latest/userguide/model-policies.html",
    ),
    ProviderPreset(
        name="deepseek-azure",
        base_url="https://your-resource.openai.azure.com/openai/deployments",
        model="deepseek-chat",
        api_key_env="DEEPSEEK_AZURE_API_KEY",
        desc="DeepSeek via Azure (Azure 企业级部署 DeepSeek)",
        tier=2, free_tier=False, region="global", category="国际主流",
        recommended_models=["deepseek-chat", "deepseek-reasoner"],
        docs_url="https://azure.microsoft.com/products/ai-services/openai-service",
    ),
    ProviderPreset(
        name="nvidia-nim",
        base_url="https://integrate.api.nvidia.com/v1",
        model="nvidia/llama-3.3-70b-instruct",
        api_key_env="NVIDIA_API_KEY",
        desc="NVIDIA NIM (GPU 优化推理, Llama/Mistral, 有免费额度)",
        tier=2, free_tier=True, region="global", category="国际主流",
        recommended_models=["nvidia/llama-3.3-70b-instruct", "nvidia/mistral-nemo-12b-instruct"],
        docs_url="https://docs.api.nvidia.com/nim-reference/",
    ),
    ProviderPreset(
        name="snowflake",
        base_url="https://<account>.snowflakecomputing.com/api/v2/cortex/llm/chat/completions",
        model="snowflake-arctic",
        api_key_env="SNOWFLAKE_API_KEY",
        desc="Snowflake Cortex (企业数据平台内嵌 AI)",
        tier=3, free_tier=False, region="global", category="云平台",
        recommended_models=["snowflake-arctic", "llama3.1-70b"],
        docs_url="https://docs.snowflake.com/en/user-guide/snowflake-cortex/llm-functions",
    ),
    ProviderPreset(
        name="watsonx",
        base_url="https://<region>.ml.cloud.ibm.com/ml/v1/chat/completions",
        model="ibm/granite-13b-chat-v2",
        api_key_env="WATSONX_API_KEY",
        desc="IBM watsonx (企业级 AI 平台, Granite 模型)",
        tier=3, free_tier=False, region="global", category="云平台",
        recommended_models=["ibm/granite-13b-chat-v2", "meta-llama/llama-3-70b-instruct"],
        docs_url="https://dataplatform.cloud.ibm.com/docs/content/wsj/analyze-data/fm-models.html",
    ),
    ProviderPreset(
        name="sambanova",
        base_url="https://api.sambanova.ai/v1",
        model="Meta-Llama-3.3-70B-Instruct",
        api_key_env="SAMBANOVA_API_KEY",
        desc="SambaNova (高性能 AI 芯片推理, Llama 系列, 有免费额度)",
        tier=2, free_tier=True, region="global", category="国际主流",
        recommended_models=["Meta-Llama-3.3-70B-Instruct", "DeepSeek-V3-0324"],
        docs_url="https://docs.sambanova.ai/api-reference/chat-completions",
    ),
    ProviderPreset(
        name="cerebras",
        base_url="https://api.cerebras.ai/v1",
        model="llama-3.3-70b",
        api_key_env="CEREBRAS_API_KEY",
        desc="Cerebras (晶圆级芯片推理, 极速 Llama, 有免费额度)",
        tier=2, free_tier=True, region="global", category="国际主流",
        recommended_models=["llama-3.3-70b", "llama-3.1-8b"],
        docs_url="https://inference-docs.cerebras.ai/api-reference/chat-completions",
    ),
    ProviderPreset(
        name="inference",
        base_url="https://api.inference.net/v1",
        model="meta-llama/llama-3.3-70b-instruct",
        api_key_env="INFERENCE_API_KEY",
        desc="Inference.net (开源模型推理, 有免费额度)",
        tier=2, free_tier=True, region="global", category="国际主流",
        recommended_models=["meta-llama/llama-3.3-70b-instruct"],
        docs_url="https://inference.net/docs",
    ),
    ProviderPreset(
        name="aimon",
        base_url="https://api.aimon.ai/v1",
        model="aimon-7b",
        api_key_env="AIMON_API_KEY",
        desc="AiMon (企业级 AI 网关, 多模型路由, 有免费额度)",
        tier=2, free_tier=True, region="global", category="聚合网关",
        recommended_models=["aimon-7b"],
        docs_url="https://docs.aimon.ai",
    ),
]

# =====================================================================
# F. 自托管/本地 (4 家)
# =====================================================================

_F_SELF_HOSTED: List[ProviderPreset] = [
    ProviderPreset(
        name="ollama",
        base_url="http://localhost:11434/v1",
        model="qwen2.5:7b",
        api_key_env="OLLAMA_API_KEY",
        desc="Ollama (本地一键运行, Llama/Qwen/DeepSeek/Gemma)",
        tier=1, free_tier=True, region="global", category="本地部署",
        recommended_models=["qwen2.5:7b", "llama3.3:70b", "deepseek-v3", "gemma2:9b"],
        docs_url="https://github.com/ollama/ollama",
    ),
    ProviderPreset(
        name="lmstudio",
        base_url="http://localhost:1234/v1",
        model="lmstudio-community/Meta-Llama-3.1-8B-Instruct-GGUF",
        api_key_env="LMSTUDIO_API_KEY",
        desc="LM Studio (GUI 本地推理, 拖拽部署模型)",
        tier=1, free_tier=True, region="global", category="本地部署",
        recommended_models=["lmstudio-community/Meta-Llama-3.1-8B-Instruct-GGUF"],
        docs_url="https://lmstudio.ai/",
    ),
    ProviderPreset(
        name="vllm",
        base_url="http://localhost:8000/v1",
        model="meta-llama/Llama-3.1-8B-Instruct",
        api_key_env="VLLM_API_KEY",
        desc="vLLM (高性能推理引擎, 适合 GPU 服务器)",
        tier=1, free_tier=True, region="global", category="本地部署",
        recommended_models=["meta-llama/Llama-3.1-8B-Instruct", "Qwen/Qwen2.5-72B-Instruct"],
        docs_url="https://docs.vllm.ai/",
    ),
    ProviderPreset(
        name="local",
        base_url="http://localhost:11434/v1",
        model="qwen2.5",
        api_key_env="LOCAL_API_KEY",
        desc="通用本地网关 (兼容 Ollama/vLLM/LM Studio 等)",
        tier=1, free_tier=True, region="global", category="本地部署",
        recommended_models=["qwen2.5", "llama3.3", "deepseek-v3"],
        docs_url="",
    ),
]

# =====================================================================
# 去重合并
# =====================================================================

def _dedup_presets(presets: List[ProviderPreset]) -> List[ProviderPreset]:
    """按 name 去重, 保留第一次出现的。"""
    seen = set()
    result = []
    for p in presets:
        if p.name not in seen:
            seen.add(p.name)
            result.append(p)
    return result


# 所有供应商, 去重后
ALL_PROVIDERS: List[ProviderPreset] = _dedup_presets(
    _A_CHINA_MAINSTREAM + _B_INTERNATIONAL + _C_AGGREGATORS +
    _D_CLOUD_PLATFORMS + _E_FREE_TIER + _F_SELF_HOSTED + _G_EXTRA
)

# 用调研整理的最新模型清单覆盖各供应商的可选模型列表 (聚合平台 50+, 直连平台仅当前支持),
# 并把默认模型同步为清单第一项 (推荐默认)。清单项带 |free/|paid 标注, 默认模型取 ID 部分。
for _p in ALL_PROVIDERS:
    _models = MODEL_LISTS.get(_p.name)
    if _models:
        _p.recommended_models = list(_models)
        _p.model = _models[0].split("|")[0]

# 名称集合, 用于快速查找
ALL_PROVIDER_NAMES: List[str] = [p.name for p in ALL_PROVIDERS]

# 按分类组织
PROVIDER_CATEGORIES: Dict[str, List[ProviderPreset]] = {}
for _p in ALL_PROVIDERS:
    cat = _p.category or "其他"
    PROVIDER_CATEGORIES.setdefault(cat, []).append(_p)


def get_provider(name: str) -> Optional[ProviderPreset]:
    """按名称获取供应商预设。"""
    for p in ALL_PROVIDERS:
        if p.name == name:
            return p
    return None


def get_provider_models(name: str) -> List[str]:
    """获取某供应商当前可选模型清单 (未收录则返回空列表)。"""
    preset = get_provider(name)
    if preset is None:
        return []
    return list(preset.recommended_models)


def search_providers(query: str) -> List[ProviderPreset]:
    """按关键词搜索供应商 (支持中英文模糊匹配)。"""
    q = query.lower()
    return [p for p in ALL_PROVIDERS
            if q in p.name.lower() or q in p.desc.lower() or q in p.category.lower()]


def search_models(query: str) -> List[tuple]:
    """按关键词跨供应商搜索模型, 返回 [(provider, model_id, free)]。

    匹配范围: 模型 ID / 供应商名 / 供应商描述。模型清单项带 |free/|paid 标注,
    free 表示该模型有免费额度。
    """
    q = query.lower()
    results: List[tuple] = []
    for p in ALL_PROVIDERS:
        for m in p.recommended_models:
            label = m.split("|")[0]
            free = "|free" in m
            if q in label.lower() or q in p.name.lower() or q in p.desc.lower():
                results.append((p.name, label, free))
    return results


def get_free_providers() -> List[ProviderPreset]:
    """获取所有有免费额度的供应商。"""
    return [p for p in ALL_PROVIDERS if p.free_tier]


def get_cn_providers() -> List[ProviderPreset]:
    """获取所有中国地区供应商。"""
    return [p for p in ALL_PROVIDERS if p.region == "cn"]


def get_global_providers() -> List[ProviderPreset]:
    """获取所有全球供应商。"""
    return [p for p in ALL_PROVIDERS if p.region == "global"]
