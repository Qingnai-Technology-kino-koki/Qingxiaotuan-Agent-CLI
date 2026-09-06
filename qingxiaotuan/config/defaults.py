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
            return str(Path(__file__).resolve().parent / "resources" / "SOUL.md")
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
    # ---- 免费/极低价 ----
    "opencode-zen": {
        "model": {
            "provider": "opencode-zen",
            "base_url": "https://opencode.ai/zen/v1",
            "model": "deepseek-v4-flash-free",
            "api_key_env": "OPENCODE_ZEN_API_KEY",
            "temperature": 0.7,
            "max_tokens": 8192,
            "stream": True,
        },
        "desc": "OpenCode Zen 免费层 (每日有限免费额度, 需注册获取 Key)",
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
        "read_timeout": 300.0,           # 等待首字节/流式分片的最长空闲; 思考型模型会在此之上放宽
        "max_retries": 3,                # 调用失败自动重试次数 (agent 层指数退避)
        # 速率限制: 默认关闭 (opt-in), 付费模型无需限流, 开启后 agent 发送前会主动节流
        "rate_limit": {
            "enabled": False,
            "max_requests_per_minute": 10,
            "max_concurrent": 1,
        },
        # 多 Agent 协作 (herdr 式: 强模型规划/验收, 弱模型并发执行):
        #   planner = 强模型 (拆解任务 + 验收汇总), provider 留空 = 复用主 model
        #   worker  = 弱模型 (并发执行的"手"), 默认复用主 model
        # 只有 provider 必填; model/base_url/api_key_env 缺省时从主 model 继承。
        # 经济模式: 强模型规划 + 弱模型执行, 大幅降低 token 成本
        "planner": {"provider": ""},  # 留空 = 复用主 model
        "worker":  {"provider": ""},  # 留空 = 复用主 model
    },
    "agent": {
        "max_iterations": 30,
        "max_retries": 3,                # 模型调用失败重试 (指数退避)
        "retry_backoff": 2.0,            # 退避基数 (秒), 第 n 次等待 backoff * 2^(n-1)
        "retry_jitter": 0.3,             # 退避抖动比例 (0~1), 避免惊群
        "retry_on": [408, 409, 429, 500, 502, 503, 504],  # 这些状态码才重试
        # 熔断器 (第三道韧性防线): 连续失败达阈值后快速失败, 不再浪费重试预算打已故障上游。
        # 默认关闭 (enabled=False), 与历史行为完全一致; 长任务 / 弱网场景可开启。
        "circuit_breaker": {
            "enabled": False,
            "failure_threshold": 5,     # 连续失败几次后熔断
            "cooldown": 30.0,           # 熔断后冷却秒数, 期间所有调用快速失败
            "success_threshold": 1,     # 半开期连续成功几次后恢复 (CLOSED)
        },
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
        "subagent_max_workers": 5,        # 子 Agent 并发上限 (1~16)
        "subagent_timeout": 180,          # 单个子任务超时 (秒)
        "subagent_isolation": "process",  # process=进程级沙箱(生产默认) / thread=线程软隔离
    },
    # 可观测性: 默认全关, 开启后不产生任何行为变化, 仅在会话内累积 trace/span 与指标 (供 /stats 查看)。
    "observability": {
        "telemetry": {
            "enabled": False,  # 开启后 Agent 循环的每次模型调用/工具调用都生成 span 并统计延迟/错误率
        },
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
    # 联网 / 搜索基础设施。可通过 `qxt network configuration`(== `qxt net con`)查看与设定。
    "network": {
        "search_max_results": 500,         # 单次搜索最多可返回的网页数上限 (最高 500)
        "search_default_results": 5,       # 未指定 max_results 时的默认返回条数
        "search_top_k": 5,                 # 进入上下文的最相关条数 (token 节省: 采多、注精)
        "search_snippet_max_chars": 200,   # 每条摘要进入上下文前的最大字符数 (token 节省)
        "search_max_pages": 25,            # 分页请求页数上限 (每页约 20 条, 500/20=25)
        # 磁盘缓存 (按 引擎+查询词+条数 分桶): 重复查询直接命中, 省网络与 token
        "search_cache": True,              # 是否启用搜索结果磁盘缓存
        "search_cache_ttl": 21600,         # 缓存有效期 (秒, 默认 6 小时)
        "search_cache_dir": "~/.qingxiaotuan/cache/web",  # 缓存目录 (空串=禁用)
        # 搜索引擎优先级 (主引擎失败/空结果自动切换下一个)
        "search_engines": ["duckduckgo", "bing"],   # 顺序即优先级
        "fetch_max_chars": 15000,          # web_fetch 抓取正文最大字符数 (token 节省)
        "fetch_timeout": 30,               # 网络请求超时 (秒)
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
        # 用户自定义规则表 (对标 Claude Code 的 allow/deny/ask 规则):
        # 每条 {"tool": 工具名或通配符, "pattern": 可选参数通配符, "action": allow|deny|ask}
        # pattern 匹配文本: run_shell 取命令行, 其余优先 path/url, 兜底拼接参数值 (小写)。
        # 优先级: 内置 deny_patterns/网络白名单 > deny > ask > allow > 默认决策;
        # run_shell 的加固确认 (长命令/docker/fd 重定向等) 不受 allow 规则豁免。
        "rules": [],
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
    # 定时任务 (Cron): 无人值守的异步执行能力, 对标 Hermes 的 cron 机制
    "cron": {
        "enabled": True,              # 是否启用定时任务子系统
        "check_interval": 60,         # 守护进程检查到期任务的间隔 (秒)
        "notify": True,               # 任务执行完成后发送桌面通知
        "max_output_chars": 4000,     # 会话流中保存的最大输出长度
    },
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
        "servers": [],             # MCP server 列表: {name, command, args, env, security}
        "security": {
            "max_calls_per_minute": 60,   # 全局频率限制
            "audit_enabled": True,        # 启用审计日志
        },
    },
    # 安全加固 (harden) 配置: 审计加密后端 / 网络出口策略 / MCP 加固
    # 这些项此前仅靠 security_plugin 内联默认值读取, 现集中于此以便发现与覆盖。
    "security": {
        "crypto": {
            "provider": "software",   # 审计日志加密后端: software(默认) / gmssl(国密SM4) / hsm(需实体, 不适用流式日志时降级)
        },
        "network": {
            "allowed_domains": [],        # 仅允许出网的域名白名单 (空=不限制域名, 仅按红线/出口CIDR判断)
            "blocked_domains": [],        # 额外禁止的域名 (叠加内置敏感域名)
            "deny_remote_exec": True,     # 禁止 wget/curl | sh 等远程执行
            "deny_data_exfil": True,      # 禁止向外部上传/外泄数据
            "egress_cidr_allow": [],      # 出口 IP CIDR 白名单 (空=不限制; 含显式IP字面量越界即拒)
            "one_way_mode": False,        # 单向模式: 禁止 nc -l/socat LISTEN/http.server/ssh -D/sshd 等入站监听
        },
        "mcp": {
            "max_description_length": 10000,  # MCP 工具描述最大长度 (防提示词注入超长载荷)
            "audit_enabled": True,            # 审计 MCP 调用
            "block_on_injection": True,       # 检测到注入即阻断
        },
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
    # 编码验证闭环 (Verify Loop): 写工具后自动跑测试/lint/类型检查, 失败则自修复
    # 对标 Claude Code 的编码验证能力。
    "verify": {
        "enabled": True,              # 是否启用验证闭环
        "auto": True,                 # 写工具后自动触发 (需要 agent 主循环配合)
        "max_heal_rounds": 3,         # 最大自修复轮数 (超过则放弃并报告)
        "checks": {                   # 可覆盖检查命令 (留空=按项目类型自动推断)
            "test": "",               # 测试命令 (如 "python -m pytest -x -q")
            "typecheck": "",           # 类型检查命令 (如 "mypy .")
            "lint": "",               # lint 命令 (如 "ruff check .")
        },
    },
    # 模型路由: 根据任务难度自动选择模型
    "router": {
        "enabled": True,              # 是否启用智能模型路由
        "auto_switch": True,          # 是否真正自动切换模型 (False=仅 /route 咨询建议)
        "budget_limit": 0.0,          # 成本预算上限 (USD, 0=无限制)
        "difficulty_override": 0,     # 强制指定任务难度 (0=自动评估)
        # 规划/执行分离 (超越 Claude Code 的多模型经济主线):
        # 规划首轮用强模型把任务拆清楚, 其余执行轮用便宜模型改代码, 省钱提速。
        "plan_execute": False,        # 强模型规划 + 便宜模型执行 (默认关, 开启后按 turn 分流)
        "plan_provider": "",          # 可选: 强制规划阶段的供应商 (留空=按路由选 tier3)
        "plan_model": "",             # 可选: 强制规划阶段的模型
        # 卡住升级: 便宜模型连续失败/卡同一错误 → 自动升级强模型救场, 救完降回便宜。
        "escalate_on_stuck": True,    # 开启卡住自动升级 (默认开)
        "stuck_threshold": 3,         # 连续失败多少轮后升级到强模型
    },
    # 人机协作: 关键决策点暂停确认
    "collaboration": {
        "confirm_architecture": True,   # 架构级改动需确认
        "confirm_destructive": True,    # 破坏性操作需确认
        "confirm_external": True,       # 外部 API 调用需确认
        "auto_approve_minor": True,     # 小改动自动批准 (注释/格式/命名)
    },
    # 界面与回复语言 (十语言: zh-CN/zh-TW/en/ja/ko/es/pt-BR/fr/de/ru)。
    # 空 = 未配置; 首次交互式使用时弹出编号选择菜单并写回本键。
    # Agent 对话回复语言同样跟随此值 (见 core/prompts._reply_rule)。
    "language": "",
    "ui": {
        "theme": "dark",
        "show_token_usage": True,
        "show_cost_report": True,       # 会话结束时显示成本报告
        "show_reflect_summary": True,   # 显示反思摘要
        # 输出风格 (对标 Claude Code 的 Output Styles):
        # default / concise / learning, 或自定义风格文件的路径;
        # 也可放 .qxt/output-style.md 作为工作区级自定义风格。
        "output_style": "default",
        # 精确引用跳转使用的编辑器命令 (qxt open / open_file 工具)。
        # 空 = 自动探测 (优先 VSCode `code`, 否则系统默认应用)。
        # 可填: code / cursor / subl / 或完整可执行路径 (如 C:\\Program Files\\...\\Code.exe)
        "editor": "",
    },
    # 事务化操作账本 (最小影响半径的事后可逆闭环): 每个修改操作前自动快照,
    # 执行成功记录变更凭证, 工具异常自动回滚, 支持 /undo 精细撤销 (撤销最近 N 步/指定文件/全部)。
    "ledger": {
        "enabled": True,                 # 启用事务化操作账本
        "snapshot_dir": ".qxt/ledger",   # 快照存储目录 (相对工作区, 已加入 .gitignore 建议)
        "auto_rollback_on_error": True,  # 工具抛异常时自动从快照恢复 (事务保证, 不留半成品)
        "keep_snapshots": True,          # 回滚后保留快照 (便于审计); 设为 False 可节省磁盘
        "max_records": 200,              # 账本内存保留的最大变更凭证数 (超出丢弃最旧)
        "impact_preview": True,          # 确认提示中展示事前影响半径预览 (将创建/覆盖/删除哪些文件)
    },
    "hooks": {
        # 支持事件: PreToolUse / PostToolUse / UserPromptSubmit / Stop /
        #   SubagentStop / PreCompact / SessionStart / SessionEnd
        "enabled": True,                 # 启用用户级 Hooks
        "default_timeout": 30,           # 单个 hook 脚本超时 (秒), 超时强杀 (冷启动 Python 子进程需 >5s)
        "allow_blocking": True,          # 是否允许 PreToolUse hook 阻断工具执行 (需 hook 显式 blocking=true)
        "allow_edit_args": False,        # 是否允许 hook 改写工具参数 (默认关 = 更安全)
        "audit_log": True,               # 把 hook 调用写入审计事件 hook.executed
    },
    # 引擎调用隔离(进程级): 默认关闭, 维持既有「进程内直调」行为; 设为 true 后,
    # 非安全判定类引擎可经 JSONL 子进程隔离执行(qxt ext 路径用的就是真子进程);
    # 安全判定类引擎(safety)始终进程内(fail-closed), 不受此开关影响。
    "engine": {
        "isolation": False,              # 进程级隔离总开关 (opt-in)
        "isolation_timeout": 30.0,       # 子进程 IPC 超时 (秒)
    },
    # 系统级沙箱子系统 (4 层"滤网"统一兜底, 默认开启):
    #
    #   L0 意图滤网   static 识别致命红线/网络外泄/远程执行  →  deny-critical
    #   L1 信任滤网   工作区信任分级(trusted 放行 / untrusted 拒) + 计划模式 + 域名白名单
    #   L2 资源滤网   网络开关 + 内存/超时 + 高危写走副本→diff→apply 隔离
    #   L3 强隔离滤网  docker→bwrap(jobobject)→seatbelt→local 自动选后端, fail-closed
    #
    # 默认开启、trusted 工作区放行; 全部工具统一过这条流水线 (在 ToolExecutor 层堵侧门)。
    "sandbox": {
        "enabled": True,                 # 是否启用 4 层沙箱滤网流水线 (推荐保持 True)
        "backend": "auto",               # 隔离后端: auto 自动按强度降序探测 docker→landlock→seatbelt→jobobject→local
        "enforce_required": True,        # 需要强隔离但无强后端时 fail-closed 拒绝 (不静默降级裸执行)
        "deny_unknown_shell": False,     # 未知信任且含 shell 的命令是否直接拒绝
        "allowed_domains": [],           # 域名白名单 (叠加 security.network; 空=不限制)
        "resource": {
            "deny_network_by_default": False,   # 默认禁网: 含网络请求的命令改走无网隔离执行
            "max_memory_mb": 0,                 # 强隔离内存上限 (MB, 0=不限)
            "max_timeout": 0,                   # 强隔离超时上限 (秒, 0=用调用方默认)
            "isolate_copy_threshold": "high",   # 高于等于该严重度(high/critical) 的写操作走副本→diff→apply
        },
    },
}
