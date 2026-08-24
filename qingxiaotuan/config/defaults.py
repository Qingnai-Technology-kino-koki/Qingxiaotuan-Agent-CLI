"""配置默认值与内置预设 —— 源自 DeepSeek Harness 的 "configuration-as-composition" 理念。

叠加顺序 (后者覆盖前者):
    内置默认配置  ->  用户配置 ~/.qingxiaotuan/config.yaml
    ->  Profile 配置 ~/.qingxiaotuan/profiles/<name>/config.yaml
    ->  命令行 --patch 一次性覆盖层 (不写入文件)

dsh 的规则: patch 替换目标键的整个值, 而非深度合并其中的子键。
青小团沿用同一语义: 用户层与 profile 层做深合并, patch 层做整值替换。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

APP_DIR_NAME = ".qingxiaotuan"

# 单一权威 SOUL 源: 内置 resources/SOUL.md (importlib.resources 兼容打包读取)。
# ensure_home 首次启动会把这份 SOUL 写入用户家目录 ~/.qingxiaotuan/SOUL.md。
def load_builtin_soul() -> str:
    try:
        try:
            from importlib import resources

            return (resources.files("qingxiaotuan.resources") / "SOUL.md").read_text(encoding="utf-8")
        except Exception:
            return Path(__file__).resolve().parent / "resources" / "SOUL.md"
    except Exception:
        return (
            "你是「青小团」, 用户终端里的 Agent。少客套多做事, 先查再问, "
            "可复用方法蒸馏成 Skill, 不确定就明说, 做错就承认。"
        )

# 内置预设 Profile —— 用户可直接 `qxt --profile <name>` 选用, 也可在
# ~/.qingxiaotuan/profiles/<name>/config.yaml 自行覆盖。
# 覆盖场景: 快速体验免费层 / 本地部署 / 企业级 / 低成本批量任务
PRESET_PROFILES: Dict[str, Dict[str, Any]] = {
    # ---- 免费层: 零成本快速体验 ----
    "opencode-zen": {
        "model": {
            "provider": "opencode-zen",
            "base_url": "https://opencode.ai/zen/v1",
            "model": "deepseek-v4-flash-free",
            "api_key_env": "OPENCODE_ZEN_API_KEY",
            "temperature": 0.6,
            "max_tokens": 8192,
            "stream": True,
        },
        "desc": "OpenCode Zen 免费层 (50次/天, 零成本体验)",
    },
    "groq-free": {
        "model": {
            "provider": "groq",
            "base_url": "https://api.groq.com/openai/v1",
            "model": "llama-3.3-70b-versatile",
            "api_key_env": "GROQ_API_KEY",
            "temperature": 0.7,
            "max_tokens": 8192,
            "stream": True,
        },
        "desc": "Groq 超快推理 (有免费额度, Llama 70B)",
    },
    "siliconflow-free": {
        "model": {
            "provider": "siliconflow",
            "base_url": "https://api.siliconflow.cn/v1",
            "model": "Qwen/Qwen2.5-7B-Instruct",
            "api_key_env": "SILICONFLOW_API_KEY",
            "temperature": 0.7,
            "max_tokens": 8192,
            "stream": True,
        },
        "desc": "SiliconFlow 免费额度 (Qwen/DeepSeek 开源模型)",
    },
    "github-models": {
        "model": {
            "provider": "github-models",
            "base_url": "https://models.inference.ai.azure.com",
            "model": "gpt-4o-mini",
            "api_key_env": "GITHUB_TOKEN",
            "temperature": 0.7,
            "max_tokens": 8192,
            "stream": True,
        },
        "desc": "GitHub Models 免费市场 (GPT-4o-mini/Llama)",
    },
    # ---- 中国主流 ----
    "deepseek": {
        "model": {
            "provider": "deepseek",
            "base_url": "https://api.deepseek.com",
            "model": "deepseek-chat",
            "api_key_env": "DEEPSEEK_API_KEY",
            "temperature": 0.7,
            "max_tokens": 8192,
            "stream": True,
        },
        "desc": "DeepSeek 官方 (高性价比, 支持推理)",
    },
    "qwen": {
        "model": {
            "provider": "qwen",
            "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "model": "qwen-plus",
            "api_key_env": "DASHSCOPE_API_KEY",
            "temperature": 0.7,
            "max_tokens": 8192,
            "stream": True,
        },
        "desc": "阿里通义千问 (qwen-plus, 开源生态强)",
    },
    "moonshot": {
        "model": {
            "provider": "moonshot",
            "base_url": "https://api.moonshot.cn/v1",
            "model": "moonshot-v1-32k",
            "api_key_env": "MOONSHOT_API_KEY",
            "temperature": 0.7,
            "max_tokens": 8192,
            "stream": True,
        },
        "desc": "Moonshot Kimi (超长上下文 128K)",
    },
    "zhipu": {
        "model": {
            "provider": "zhipu",
            "base_url": "https://open.bigmodel.cn/api/paas/v4",
            "model": "glm-4-flash",
            "api_key_env": "ZHIPU_API_KEY",
            "temperature": 0.7,
            "max_tokens": 8192,
            "stream": True,
        },
        "desc": "智谱 GLM (glm-4-flash 免费)",
    },
    "doubao": {
        "model": {
            "provider": "doubao",
            "base_url": "https://ark.cn-beijing.volces.com/api/v3",
            "model": "doubao-1.5-pro-256k",
            "api_key_env": "ARK_API_KEY",
            "temperature": 0.7,
            "max_tokens": 8192,
            "stream": True,
        },
        "desc": "字节豆包 (超长上下文 256K, 价格极低)",
    },
    # ---- 国际主流 ----
    "openai": {
        "model": {
            "provider": "openai",
            "base_url": "https://api.openai.com/v1",
            "model": "gpt-4o-mini",
            "api_key_env": "OPENAI_API_KEY",
            "temperature": 0.7,
            "max_tokens": 8192,
            "stream": True,
        },
        "desc": "OpenAI (gpt-4o-mini, 行业标杆)",
    },
    "anthropic": {
        "model": {
            "provider": "anthropic",
            "base_url": "https://api.anthropic.com/v1",
            "model": "claude-sonnet-4-20250514",
            "api_key_env": "ANTHROPIC_API_KEY",
            "temperature": 0.7,
            "max_tokens": 8192,
            "stream": True,
        },
        "desc": "Anthropic Claude (原生 Messages API)",
    },
    "gemini": {
        "model": {
            "provider": "gemini",
            "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
            "model": "gemini-2.5-flash",
            "api_key_env": "GEMINI_API_KEY",
            "temperature": 0.7,
            "max_tokens": 8192,
            "stream": True,
        },
        "desc": "Google Gemini (免费额度慷慨)",
    },
    # ---- 本地部署 ----
    "ollama": {
        "model": {
            "provider": "ollama",
            "base_url": "http://localhost:11434/v1",
            "model": "qwen2.5:7b",
            "api_key_env": "OLLAMA_API_KEY",
            "temperature": 0.7,
            "max_tokens": 8192,
            "stream": True,
        },
        "desc": "Ollama 本地部署 (零网络延迟, 完全离线)",
    },
    "local": {
        "model": {
            "provider": "local",
            "base_url": "http://localhost:11434/v1",
            "model": "qwen2.5",
            "api_key_env": "LOCAL_API_KEY",
            "temperature": 0.7,
            "max_tokens": 8192,
            "stream": True,
        },
        "desc": "通用本地网关 (兼容 Ollama/vLLM/LM Studio)",
    },
}

DEFAULT_CONFIG: Dict[str, Any] = {
    "model": {
        "provider": "deepseek",
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-chat",
        "api_key_env": "DEEPSEEK_API_KEY",
        "temperature": 0.7,
        "max_tokens": 8192,
        "stream": True,
        "prompt_cache": True,             # 稳定 system 前缀打缓存, 重复请求只计费增量
        # 连接与超时 (秒)
        "timeout": 120,                  # 单次请求总体超时 (连接+读)
        "connect_timeout": 10.0,         # 建立 TCP/TLS 连接的最长等待
        "read_timeout": 120.0,           # 等待首字节/流式分片的最长空闲
        "max_retries": 3,                # 调用失败自动重试次数 (agent 层指数退避)
        # 速率限制 (适配 OpenCode Zen 免费层: 1 req/s, 10/min, 50次/天 8点重置)
        "rate_limit": {
            "enabled": True,
            "max_requests_per_minute": 10,
            "max_concurrent": 1,
        },
        # 多 Agent 协作 (herdr 式: 强模型规划/验收, 弱模型并发执行):
        #   planner = 强模型 (拆解任务 + 验收汇总), provider 留空 = 复用主 model
        #   worker  = 弱模型 (并发执行的"手"), 默认 opencode-zen 免费档
        # 只有 provider 必填; model/base_url/api_key_env 缺省时从主 model 继承。
        "planner": {"provider": ""},
        "worker":  {"provider": "opencode-zen", "model": "deepseek-v4-flash-free",
                    "base_url": "https://opencode.ai/zen/v1", "api_key_env": "OPENCODE_ZEN_API_KEY"},
    },
    "agent": {
        "max_iterations": 30,
        "max_retries": 3,                # 模型调用失败重试 (指数退避)
        "retry_backoff": 2.0,            # 退避基数 (秒), 第 n 次等待 backoff * 2^(n-1)
        "retry_jitter": 0.3,             # 退避抖动比例 (0~1), 避免惊群
        "retry_on": [408, 409, 429, 500, 502, 503, 504],  # 这些状态码才重试
        "skill_nudge_interval": 3,      # 每 N 个 turn 提醒一次技能蒸馏 (Hermes 闭环)
        "context_max_messages": 60,      # 超出后触发上下文压缩
        "auto_memory": True,             # 会话结束自动固化重要事实
        "show_reasoning": True,          # 是否实时显示模型的思维链 (deepseek-reasoner 等)
        # 推理投入级别 (Claude Code /effort 对应物): low=快省, medium=均衡, high=深究
        # 影响自主循环的迭代上限与自测严格度, 以及一次性任务的思考深度。
        "effort": "high",
        "effort_profiles": {
            "low":    {"loop_max_iter": 6,  "auto_test": False, "temperature": 0.8},
            "medium": {"loop_max_iter": 12, "auto_test": True,  "temperature": 0.6},
            "high":   {"loop_max_iter": 20, "auto_test": True,  "temperature": 0.4},
        },
        # 并发子 Agent (青小团的"多双手"): 把独立子任务派给隔离实例并发执行
        "subagent_max_workers": 4,        # 子 Agent 并发上限 (1~16)
        "subagent_timeout": 180,          # 单个子任务超时 (秒)
        "subagent_isolation": "process",  # process=进程级沙箱(生产默认) / thread=线程软隔离
    },
    "context": {
        "auto_index": True,              # 进入 chat 时自动索引工作区结构
        "index_max_files": 300,          # 纳入索引统计的最大文件数
        "index_max_loc": 200000,         # 纳入地图标注的最大总行数
        "pin_codebase": True,            # 把代码库地图钉在系统提示里 (大上下文机制)
        "compact_strategy": "smart",     # smart=模型摘要 / none=仅折叠
        "keep_recent": 14,               # 始终保留的最近消息条数
        "budget_tokens": 60000,          # 压缩后目标回到该估算预算内
        "compact_trigger": 60000,        # 超过该 token 预算才触发压缩 (可略低于 budget 提前压)
        "short_task_max_chars": 160,     # 短任务跳过语义召回，减少无效输入
        "max_task_context_chars": 1800,  # 首轮相关记忆/技能上下文总长度上限
    },
    "loop": {
        "enabled": True,
        "max_iterations": 12,            # 单轮任务自主迭代上限
        "ask_every": 1,                  # 每几轮向用户确认一次 (0=不主动问, 交给模型自行判断)
        "report_progress": True,         # 每轮主动向用户汇报进度
        "auto_test": True,               # 每轮尝试运行测试做自测
        "stop_on_user_ok": True,         # 用户满意即停止
        "reflect_every": 2,              # 每 N 轮反思一次，减少重复验证调用
    },
    "tools": {
        "shell": {"enabled": True, "require_confirm": True, "timeout": 60},
        "filesystem": {"enabled": True, "require_confirm_write": False},
        "web": {"enabled": True, "timeout": 30},
        "cache_ttl": 60.0,                 # 只读工具结果缓存 TTL (秒, 0=关闭)
    },
    # 运行模式: standard (默认, 危险操作逐项确认) / yolo (全部自动批准, 风险自担)
    "mode": {
        "default": "standard",
        # YOLO 模式下仍保留的"最后红线": 这些工具即便在 yolo 也强制确认
        # (留空 = 全部自动批准; 一般只保留不可逆/对外暴露的操作)
        "yolo_require_confirm": [],
    },
    "permissions": {
        "shell": {
            "deny_patterns": [
                r"\b(?:format|diskpart)\b",
                r"\b(?:shutdown|restart)-computer\b",
            ],
        },
        "network": {
            "allow_domains": [],  # 空列表表示不限制域名
        },
    },
    "memory": {
        "fts_enabled": True,
        "recall_limit": 5,
    },
    "skills": {
        "enabled": True,
        "auto_inject": True,
        "inject_limit": 3,
    },
    "cron": {"enabled": True},
    # 后台自主模式 ("手"): 终端不阻塞, 任务在独立会话里自己干活, 进展写进会话流
    "background": {
        "enabled": True,                  # 是否允许 qxt agent / run --bg
        "max_turns": 50,                  # 单任务后台自主轮次上限 (防失控)
        "report_every": 5,                # 每 N 轮把进展回写主会话流
        "poll_interval": 1.0,             # 主端查询后台进度的最小间隔 (秒)
        "heartbeat_timeout": 90.0,        # worker 无心跳且进程消失后的失联判定时间
    },
    "mcp": {
        "enabled": True,
        "timeout": 30.0,           # 单次 MCP 调用超时 (秒)
        "servers": [],             # MCP server 列表: {name, command, args, env}
    },
    # 外部引擎 (ext/c C 程序 + ext/ts TS 模块) 集成层
    "ext": {
        "enabled": True,                  # 是否启用外部引擎工具
        "node_exe": "",                   # node 可执行文件 (空=自动探测)
        "c_bin_dir": "",                  # qxt_*.exe 目录 (空=ext/dist/bin)
        "ts_src_dir": "",                 # TS 源码目录 (空=ext/ts)
        "ts_dist_dir": "",                # TS 编译产物目录 (空=ext/ts/dist)
        "request_timeout": 60.0,         # 单次 IPC 请求超时 (秒)
    },
    # 自主反思循环 (Reflector): Plan→Execute→Reflect→Re-plan 闭环
    "reflector": {
        "enabled": True,              # 是否启用 Reflector 反思引擎
        "max_auto_fix": 2,            # 连续失败几次后降级 (1=立即降级, 3=最多重试3次)
        "verify_timeout": 120,        # 单次验证 (测试/lint) 超时 (秒)
        "verify_tools": [],           # 指定验证工具列表, 空=自动检测
        # 验证工具检测规则:
        # - pytest: 有 pyproject.toml / pytest.ini / tests/ 目录
        # - ruff: pyproject.toml 中含 ruff 配置
        # - mypy: pyproject.toml 中含 mypy 配置
        # - npm_test: 有 package.json 且含 test script
        # - eslint: 有 .eslintrc 配置
    },
    # 模型路由: 根据任务难度自动选择模型
    "router": {
        "enabled": True,              # 是否启用智能模型路由
        "budget_limit": 0.0,          # 成本预算上限 (USD, 0=无限制)
        "difficulty_override": 0,     # 强制指定任务难度 (0=自动评估)
    },
    # 人机协作: 关键决策点暂停确认
    "collaboration": {
        "confirm_architecture": True,   # 架构级改动需确认
        "confirm_destructive": True,    # 破坏性操作需确认
        "confirm_external": True,       # 外部 API 调用需确认
        "auto_approve_minor": True,     # 小改动自动批准 (注释/格式/命名)
    },
    "ui": {
        "theme": "dark",
        "show_token_usage": True,
        "show_cost_report": True,       # 会话结束时显示成本报告
        "show_reflect_summary": True,   # 显示反思摘要
    },
}
