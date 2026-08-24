# 青小团 CLI (Qingxiaotuan Agent CLI)

> 一个以"最小影响半径"为核心设计理念的 Agent CLI：在 Agent 替你改东西之前，先想清楚这一步会动到哪里、会不会翻车。
>
> 当前状态（客观数据，非宣传）：核心测试 **215 passed / 3 failed**；外部引擎 **16/16 就绪**（8 C + 8 TS，`qxt ext selftest` 验证）。

一个独立的 Agent 命令行工具，融合多项开源 Agent 项目的核心理念二次开发：

| 来源 | 吸收的理念 |
| --- | --- |
| **DeepSeek Harness (dsh)** | 微内核（Cordis 风格）、一切皆插件、Profile 组合式配置、模型中立适配、headless 一次性任务、append-only 会话事件流 |
| **Hermes Agent** | 三层记忆（会话/事实/技能）、技能自进化闭环、SOUL.md 身份、SQLite FTS5 跨会话检索、自注册工具、cron 定时任务 |
| **Claude Code** | 流畅 CLI（多行输入/命令历史）、实时思考过程与工具状态展示、大上下文机制（整库结构+文件+历史智能管理）、`/clear` `/help` 等交互命令 |
| **Hermes 自主进化** | Agent 持续自主迭代开发 Loop（分析→规划→实现→自测→核实→汇报），直到用户明确满意才停止 |

## 吉祥物：青小团 (Mascot)

青小团是一只圆润的青色小团子，呼应项目名 Qingxiaotuan。它不是静态 logo，而是**一个有"生命体征"的状态机**——随任务推进实时变化，让你一眼看清 Agent 现在在干什么：

```
  idle    thinking    working     alert      done
  ( ◡ )   ? ⠋        ( • • )    ( @ • )    ✨
 ╭─────╮ ( ◠ ◠ )    ╭─────╮    ╭─────╮    ( ^ ^ )
 ╰─────╯ ╭─────╮    ╰─────╯    ╰─────╯    ╭─────╮
         ╰─────╯     ▔▔▔▔▔      ⚠ 拦截     ╰─────╯
  待命    思考(摇摆)  干活(浮动)  安全拦截    完成(弯月)
```

| 状态 | 触发时机 | 视觉 |
| --- | --- | --- |
| `idle` 待命 | 空闲 / 等待输入 | 平静呼吸 |
| `thinking` 思考 | 模型推理中 | 左右摇摆 + 旋转光标 |
| `working` 干活 | 工具调用执行中 | 身体浮动 + 进度环 |
| `alert` 警戒 | **安全护栏拦截**危险命令 | 变橙、抖动（最小影响半径生效的直观信号） |
| `done` 完成 | 任务收尾 | 弯月眼 + 星光 |

富 UI / 文档中使用带 SMIL 动画的 SVG 版本（`qingxiaotuan/ui/mascot.py` 的 `Mascot.svg()`），终端里使用 ANSI 着色的 ASCII 帧（`Mascot.ascii()`），两者状态完全对齐。
| **企业级健壮性** | 模型调用分离连接/读超时 + 指数退避+抖动自动重试（默认 3 次，429 限流友好退避）；循环遇模型故障不崩溃，经 `on_error` 友好回传；工具结果失败自动识别 |

> Model + Harness = Agent。模型负责思考，青小团负责让思考可控地运行、并记住每一次经验、并自主把事情做完整。

## 设计理念 (Design Principles)

青小团不追求"比某工具多几个按钮"。我们盯住的是**开发者每天都会遇到的三件事**，并围绕它们做工程取舍：

1. **最小影响半径 (Minimum Blast Radius)** —— Agent 的价值不是"能改更多"，而是"**能把破坏锁在最小半径内**"。每次 shell 命令执行前，C 引擎 `safety` 会做静态风险分析：是否会触及迁移、生产配置、force push？并给出风险评分与替代建议。实现上，`critical` 级命令（`rm -rf` / `git push --force` / `DROP TABLE` 等）在 `run_shell` 执行前被**默认拦截**；`high`/`medium` 级在确认提示里展示更安全的写法；即便开启 YOLO 模式也绝不自动执行致命红线。目标是让开发者在按键前就能看清"这一步会动到哪里"。
2. **可重放的工作流 (Replayable Workflow)** —— 把"如何不翻车"的经验固化成**可审计、可重放**的规则与技能（rules 引擎 + skill 提炼）。一次学会，团队可像 review 代码一样 review 工作流。
3. **自我改进闭环 (Self-Improve Loop)** —— 每次执行后自动复盘（订阅 `tool.executed` 事件）：从成功 / 失败 / 被拒的事件中抽取经验，生成或修正规则。`self-improve apply` 落盘的规则会立即加载，下一次工具分发前自动查询——命中 learned 护栏的调用被**升级为必须人工确认**（而非静默硬阻断），控制权始终在人手中。技能草稿需人工审阅后才激活。
4. **统一的多语言能力内核** —— C 与 TypeScript 外部引擎通过统一 JSONL IPC 协议与 Python 内核对话，把 CPU 密集（加密 / 哈希 / 大文件 diff / index）与 IO 密集（搜索 / 通知 / MCP / 插件）任务分流到最合适的语言。当前包含 10 个 C 引擎（`diff`/`patch`/`merge3`/`crypto`/`index`/`ansi`/`sandbox`/`watch`/`safety`/`json`）+ 若干 TS 模块，`json` 引擎提供 RFC 6901 Pointer 取值、结构化 diff、深合并，面向后端 / 前端的 JSON 工程场景。

> 一句话定位：在你按下回车之前，青小团先想清楚"这一步会不会翻车"。

## 开源

青小团是一个 **MIT 许可** 的开源项目。它不绑架任何一家模型——DeepSeek 不强时，你可以随时把"脑子"换成 Claude / Gemini / 本地网关（Ollama / vLLM / LM Studio）。

- **许可证**：[MIT](./LICENSE)
- **参与贡献**：见 [CONTRIBUTING.md](./CONTRIBUTING.md)，行为准则见 [CODE_OF_CONDUCT.md](./CODE_OF_CONDUCT.md)
- **核心差异化能力**：后台自主（"手"）+ 进程级沙箱并发子 Agent（"多双手"）+ 多供应商运行时热切换 + 对标 Claude Code 的无感 compact 与 prompt 缓存工程

```bash
# 跑通只需三步
pip install -e .
qxt setup                 # 30秒向导: 选供应商 + 填 API Key
qxt chat                  # 开聊
```

**48 家模型供应商预置** — DeepSeek / OpenAI / Claude / Gemini / 通义千问 / Kimi / 豆包 / Groq（免费）/ Ollama（本地）等，按数字选一家即可，无需手填任何地址。

## 架构

分层清晰, Plugin 类归属各自领域模块 (一切皆插件):

```
qingxiaotuan/
├── cli/          命令行层: parser(参数) + commands(各子命令 + slash)
├── core/         内核与编排: kernel(微内核) / agent(ReAct) / devloop(自主循环) / subagents(并发子Agent) / background(后台自主) / prompts(系统提示)
├── config/       配置: defaults(默认) / loader(Config类) / plugin(ConfigPlugin) / validate(校验)
├── models/       模型适配: base(接口) / openai_compat / plugin(ModelPlugin)
├── memory/       记忆: store(FTS5) / sessions(事件流) / plugin
├── skills/       技能: manager / plugin
├── tools/        工具: base(注册表+缓存) / filesystem/shell/web/code/memory_tool/skill_tool/dispatch(并发派活) / mcp/
├── context/      上下文: indexer(代码库地图) / manager(压缩) / plugin
├── cron/         定时任务: store / plugin
├── ui/           Kimi Code CLI 风格 TUI (repl)
├── resources/    内置技能模板 + SOUL.md 单一权威源 (随 wheel 分发)
└── app.py        装配器: build_kernel / create_agent / seed_builtin_skills
```

微内核 Kernel 只做三件事: 插件装配 / 服务发现 / 事件总线。每个领域模块以 Plugin 形式注册自身,
Agent 主循环 = ReAct + 技能 nudge, DevLoop = 分析→规划→实现→自测→核实→汇报。
工具注册表内置**只读结果缓存** (TTL, 写操作自动失效), 减少多轮循环里的重复 IO。

### 外部能力引擎 (C / TypeScript)

性能敏感 / 需要 native 能力的部分被拆成独立子进程, 通过统一的 **JSONL IPC 协议**
(`请求 {id,method,params}` → `响应 {id,ok,result|error}` → `流 {id,stream,chunk}` → `首帧 {ready:true}`)
与 Python 内核对话。内核只认协议, 不关心实现语言:

- **C 引擎** (`ext/c/*`, gcc 编译成 `ext/dist/bin/qxt_*.exe`): 零依赖, 直接吃 libc。
  - `diff` 行/词级 Myers diff + patch + 3-way merge
  - `crypto` AES-256-GCM 加解密 + PBKDF2-HMAC-SHA256 派生 + 指纹 (标准实现, 过 NIST 向量)
  - `index` FNV-1a 增量符号索引, 支持 path/lang/symbol 多维检索
  - `ansi` 终端转义解析 / 剥离 / 渲染
  - `sandbox` / `watch` 受控执行沙箱 / 文件变更监听
  - `safety` 「最小影响半径」护栏: 命令/SQL/写操作执行前静态风险评分 (none→critical) + blast_radius 估算 + 安全替代建议, `critical` 自动阻断
  - `json` 结构化 JSON 引擎: RFC 6901 Pointer 精确取值 / 逐路径 diff / 深合并 (overlay 覆盖 base), 面向开发者 JSON 工程场景
- **TypeScript 模块** (`ext/ts/src/*`, node 运行, 零构建 `tsc`): 复用 Node 生态。
  - `rules` 规则引擎 (断言: 熵/密钥/危险模式…) + validate
  - `plugin-host` / `mcp-client` / `skill-market` / `dashboard` / `agent-sdk`
  - `search` 递归正则检索 (忽略 node_modules/.git, 跳过二进制) + `notify` 跨平台桌面通知

统一协议实现: `ext/c/common/ipc.{h,c}`、`ext/ts/src/protocol.ts`、`qingxiaotuan/core/ipc_client.py`。

调试 / 自检外部引擎 (无需启动完整 Agent):

```bash
qxt ext engines                 # 列出当前环境真正可用的引擎 (区分 C / TS)
qxt ext call crypto seal '{"passphrase":"x","salt_b64":"...","plaintext":"hi"}'
qxt ext info search             # 查看某引擎的方法列表 / 版本
qxt ext selftest                # 逐个启动引擎跑 _meta/list, 报告健康度
```

#### 构建外部引擎

```bash
# C 引擎 (MinGW / gcc, 共享 ipc.c 静态链接; Windows 用 sandbox_win.c / watch_win.c)
gcc -O2 -std=c11 -Iext/c/common \
    ext/c/crypto/crypto.c ext/c/common/ipc.c -o ext/dist/bin/qxt_crypto.exe -lbcrypt
# 其余引擎同理: diff / index / ansi / sandbox(+sandbox_win.c) / watch(+watch_win.c, -pthread)
# 或用 CMake:  cmake -S ext/c -B build && cmake --build build

# TypeScript 模块 (编译到 dist, 也可用 node --experimental-strip-types 直接跑 src)
cd ext/ts && npx tsc -p tsconfig.json     # 产物: ext/ts/dist/<module>/main.js
node ext/ts/dist/search/main.js           # 即作为 IPC 子进程被内核拉起
```

#### 测试

```bash
pytest tests/test_external_engines.py     # 覆盖 C / TS 引擎的启动、协议、往返
```

## 安装

> 要求 Python ≥ 3.11（核心内核与纯 Python 工具仅需 Python，无需编译）。
> 外部能力引擎（C / TypeScript）为**可选增强**：不编译也能用核心 Agent 能力，编译后获得安全护栏、加密、diff、JSON 等高性能能力。

**Windows（一键安装）**

```powershell
git clone <repo-url> qingxiaotuan && cd qingxiaotuan
.\install.ps1          # 建 venv + 安装 + 把 qxt 加入用户 PATH
```

**macOS / Linux**

```bash
git clone <repo-url> qingxiaotuan && cd qingxiaotuan
python -m venv .venv && source .venv/bin/activate
pip install -e .
```

安装后**重开终端**，即可在任意目录直接敲 `qxt`。

**可选：编译外部引擎（需 gcc / MinGW + Node.js ≥ 18）**

```bash
npm install            # 安装 TS 依赖
npm run build          # 编译 C 引擎 (scripts/build-c.sh) + TS 引擎 (tsc)
qxt ext selftest       # 验证所有引擎健康, 应输出 "所有引擎就绪"
```

> 不编译外部引擎时，`ext_safety_*` / `ext_crypto_*` 等工具不可用，但 `run_shell` 的安全拦截仍会优雅降级（放行但不做静态评分）。

### 三条最常用的命令

```bash
qxt                 # 直接打开交互界面 (默认进 chat)
qxt --yolo          # 无限制模式: 危险操作自动批准, 全速体验
qxt model           # 交互式挑模型供应商 (开箱支持 48 家)
```

`qxt model` 会列出 48 家预置的供应商，按分类浏览（中国主流/国际主流/聚合网关/云平台/免费层/本地部署），支持搜索和过滤，按数字选一家，自动带好网关地址和密钥变量，无需手填。

### 配置 API Key 并体验

```bash
# 方式一: 30秒快速设置 (推荐)
qxt setup              # 选供应商 → 填 API Key → 完成

# 方式二: 交互式挑一家
qxt model              # 分类浏览 48 家供应商, 按数字选

# 方式三: 命令行直接指定
qxt model set deepseek deepseek-chat https://api.deepseek.com DEEPSEEK_API_KEY

# 健康检查（含连通性轻探测）
qxt doctor

# 开聊！
qxt                    # 等同 qxt chat
qxt --yolo             # YOLO: 危险操作自动批准
qxt chat --tui         # 全屏 TUI 工作台
```

**setup 三种模式：**
- ⚡ **快速设置**（默认）: 选一家推荐供应商 → 填 API Key → 30秒搞定
- 🔧 **完整设置**: 逐项配置供应商/模型/网关/密钥/模式/推理投入
- 📭 **空白设置**: 最小化默认配置，后续手动调整

> 首次启动会自动 seed 内置技能、初始化记忆库与 SOUL.md，无需手动操作。
> 密钥一律从环境变量读取，写进 `~/.qingxiaotuan/.env`（已被 `.gitignore` 排除，不进版本库）。
> 免费层（OpenCode Zen / Groq / SiliconFlow / GitHub Models 等）可零成本体验。

**⚠️ 关于"没有输入框 / 打不了字"**

`qxt` 是交互式终端程序，**必须在真实的系统终端里运行**，不能在聊天软件 / IDE 的内嵌命令行 / 被管道或重定向的 shell 里跑——那些环境不会把键盘输入转发给 `qxt`，于是它卡在等输入、看起来"没有输入框"。

- ✅ 正确：打开 **命令提示符 / PowerShell / Windows Terminal**，直接敲 `qxt`（或 `.\.venv\Scripts\Activate.ps1` 后 `qxt`）。
- ❌ 错误：在本软件的对话里、或在把 stdout 重定向走的脚本里执行 `qxt`。
- 若在非交互环境里误跑，新版本会直接打印一句提示告诉你"请在本机终端运行"，而不是静默卡死。
- 不想记命令？直接**双击项目里的 `qxt.bat`** 也能启动。

### 卸载 / 重装

```bash
pip uninstall qingxiaotuan         # 仅移除命令，~/.qingxiaotuan 数据保留
rm -rf ~/.qingxiaotuan             # 如需彻底清除本地数据（含技能/记忆/配置）
```

## 快速上手

```bash
qxt chat                          # 交互式对话 (默认命令, 直接 qxt 也行)
qxt chat --tui                    # 全屏 TUI 工作台 (面板/日志/输入/快捷键)
qxt run "总结当前目录的结构"        # headless 一次性任务, 跑完退出, 适合脚本/CI
qxt run --yes "跑一下测试"         # 自动批准危险操作 (等同于 --mode yolo)

# 后台自主 ("手"): 独立 worker 跨命令运行, 终端不阻塞
qxt agent "把本周的日志整理成周报"  # 提交后即返回, 用 qxt bg 查看进度
qxt run "巡检所有服务" --bg        # 一次性任务也支持后台
qxt bg list                       # 列出后台任务
qxt bg logs <job_id>              # 查看某后台任务的实时进展 (会话流尾部)
qxt bg wait <job_id>              # 阻塞等待某个后台任务结束
qxt bg cancel <job_id>            # 取消正在运行的后台任务

# 缓存命中率实测 (对标 dsh 99% 水平)
qxt bench cache --rounds 8      # 固定长对话脚本跑 8 轮, 输出逐轮/平均缓存命中率

# 并发子 Agent (青小团的"多双手")
# 交互模式里: /parallel 调研A || 调研B || 调研C   (用 || 分隔独立子任务)
# 或让主 Agent 自己调用 dispatch_tasks 工具并发派出隔离子 Agent

# 多 Agent 协作 (herdr 式: 强模型规划/验收 + 弱模型并发 + 共享黑板)
# 交互模式里: /swarm 写三篇开源宣传稿 || 3   (末尾 || n 指定子任务数上限)

# 会话管理
qxt session list                  # 列出最近的会话 (按修改时间)
qxt session resume                # 恢复最近一个会话 (也可 resume 2 / resume <文件名>)

# 配置自检
qxt config validate               # 校验配置 (类型/范围/枚举/密钥是否齐全)

# 混合语言工程质量检查
# Agent 可自动识别 Python/TypeScript，并调用 project_languages / language_checks
qxt run "检查并修复当前 Python 和 TypeScript 工程的类型与测试问题"

# 双运行模式
qxt chat --mode yolo              # 单次以 YOLO 模式运行 (危险操作自动批准, 审计兜底)
qxt mode yolo                     # 切换默认模式为 YOLO (再切回: qxt mode standard)
qxt mcp list                      # 查看已接入的 MCP server 与桥接工具
```

交互模式中的 slash 命令：`/help` `/tools` `/skills` `/memory` `/usage` `/clear` `/index` `/context` `/loop` `/parallel` `/swarm` `/exit`

> 安全红线：写文件/改文件/移动/删除/写记忆/存技能 在 standard 模式逐项确认；每次会话结束若开启
> `agent.auto_memory`，会把本次会话主题自动固化进长期记忆，跨会话连续。

### 两种运行模式

| | 标准模式 (默认) | YOLO 模式 (`--mode yolo`) |
| --- | --- | --- |
| 危险操作 (删文件/执行命令/写记忆/存技能) | 逐项向你确认 | 自动批准, 同时写入审计日志 |
| 适用 | 日常、敏感仓库 | 沙箱/可信环境、追求全速 |
| 红线 | — | `mode.yolo_require_confirm` 红名单仍可强制确认 |

YOLO 模式不降低能力, 只是把"确认"环节自动化, 每个自动批准的危险操作都会经 `on_auto_approve` 提示并落日志。

### 权限策略

工具执行统一经过权限策略。除工作区路径边界外，还可以在 `~/.qingxiaotuan/config.yaml` 中限制
shell 命令和网络域名：

```yaml
permissions:
  shell:
    deny_patterns: ["\\bformat\\b", "\\bshutdown\\b"]
  network:
    allow_domains: ["docs.python.org", "*.github.com"]
mode:
  yolo_require_confirm: ["delete_dir"]
```

`allow_domains` 为空表示不限制域名；危险工具在 standard 模式始终需要确认，列入
`yolo_require_confirm` 的工具在 YOLO 模式也必须确认。后台 worker 的强制终止与资源级策略仍在完善中。

### 推理投入级别 (`/effort`)

对应 Claude Code 的 `/effort`，控制模型思考深度与自主循环强度：

| 级别 | temperature | 自主循环上限 | 每轮自测 |
| --- | --- | --- | --- |
| `low` | 0.8 | 6 轮 | 关 |
| `medium` | 0.6 | 12 轮 | 开 |
| `high`（默认） | 0.4 | 20 轮 | 开 |

```bash
qxt chat --effort medium          # 启动即指定
/effort high                      # 会话内随时切换 (即时生效, 同步调整温度)
```

切换会即时写回内核视图（temperature 同步到模型适配器），无需重启。

## 接入 OpenCode Zen (免费 deepseek-v4-flash)

OpenCode Zen 提供免费网关，OpenAI 兼容协议，无需绑卡即可调用 `deepseek-v4-flash-free`。

**方式一：用内置预设 profile（推荐）**

```bash
qxt --profile opencode-zen chat
```

**方式二：写入用户配置 + `.env`**

`~/.qingxiaotuan/config.yaml`：

```yaml
model:
  provider: opencode-zen
  base_url: https://opencode.ai/zen/v1
  model: deepseek-v4-flash-free
  api_key_env: OPENCODE_ZEN_API_KEY
```

`~/.qingxiaotuan/.env`（权限 600，不入库）：

```bash
OPENCODE_ZEN_API_KEY=sk-xxxx
```

密钥也可临时用环境变量注入：`export OPENCODE_ZEN_API_KEY=sk-xxxx`。

> 免费层限制（实测）：约 50 次/天请求，每日北京时间早 8 点（UTC 0 点）重置；速率约 1 次/秒、10 次/分钟。
> 触限后服务端返回 429，青小团会自动按 `Retry-After` 退避重试，并在 UI 提示，不会崩溃。

## MCP 协议支持

青小团原生支持 Model Context Protocol，可把任意 MCP Server 暴露的工具桥接为本地工具，对 Agent 完全透明。

在 `~/.qingxiaotuan/config.yaml` 配置：

```yaml
mcp:
  enabled: true
  servers:
    - name: filesystem
      command: npx
      args: ["-y", "@modelcontextprotocol/server-filesystem", "."]
      env: {}
```

重启会话后，该 server 的工具以 `mcp__<server>__<tool>` 命名自动注册，调用方式与本地工具一致；`qxt mcp list` 查看桥接情况。接入失败（如命令不存在）不影响其它功能。

## 代码开发模式 (`qxt dev`)

专为「理解并改造代码库」设计的模式，内置四大引擎：

### 1. CLI 界面（Kimi Code CLI 风格 TUI，青小团专属配色）
界面交互范式采用 Kimi Code CLI 的终端 TUI（无吉祥物、蓝色主调 + 青绿强调、干净的状态栏），身份与视觉完全属于「青小团」：

- **启动 banner**：简洁双线信息盒 `╭─…─╮`，标题 `青小团 CLI vX` + 当前模型 + 模式徽章，一行操作提示，无 ASCII 形象。
- **配色（Kimi dark 调色板）**：主色蓝 `primary #4FA8FF` 用于标题/工具名/强调，强调青绿 `accent #5BC0BE` 用于 `▶` 前缀，正文亮白，dim 灰用于次要信息，绿/琥珀/红表示成功/警告/错误。
- **提示符**：`▶` 后跟空格（accent 青绿色），多行续行以两空格缩进。
- **底部状态栏（footer）**：每轮输入前显示 `─ 模式 · effort=high · <路径>`，贴近 Kimi 的 footer 风格。
- **工具调用展示**：每次调用以 `▶` 前缀 + 树形 `└─` 缩进呈现 ——
  ```
    ▶ read_file(path=...)
       └─ ✓ 读取 120 行 …
  ```
- **思维链**：模型 reasoning 以 dim 灰实时流式显示（如 `⠒ …`）。
- **确认框**：黄色 `?` 提示 + 选项行（`[y] 允许 / [n] 拒绝`），权限询问风格对齐现代 Agent CLI。
- **上下文预算条**：`/context` 以 `━┤42%├━` 直观显示 token 占用与压缩阈值。
- **`/help` 格式**：分组命令清单（带 `───` 分隔线）。
- 基于 `prompt_toolkit` 的多行输入、`Esc+Enter` 换行、命令历史落盘 `~/.qingxiaotuan/history/repl.txt`。

### 2. 大上下文管理 (Context Window)
- `context/indexer.py` 启动时扫描整库：目录树、各语言规模、关键入口文件，生成紧凑代码库地图钉入系统提示。
- `context/manager.py` 智能优先级：system + 钉死的代码库上下文 + 最近 N 条消息 + 中间摘要；超预算自动基于模型做摘要压缩，但保留代码库地图等关键信息不丢失。

### 3. 自主开发 Loop (DevLoop)
```bash
qxt dev "给项目加一个导出 CSV 的功能"
```
Agent 持续自主迭代，每轮循环：**分析代码库 → 规划任务 → 执行修改 → 自测验证 → 核实 → 主动汇报进度**。
- 每轮结束向用户确认或接收反馈（"继续 / 调整方向 / 已完成"）。
- 遇到歧义主动询问，不再盲目猜测。
- 循环直到用户明确说"完成"才停止 —— 不中途放弃。

### 4. 核心代码能力 (`tools/code.py`)
| 工具 | 作用 |
| --- | --- |
| `codebase_map` | 获取整库结构树、语言规模、关键入口 |
| `find_symbol` | 跨文件定位符号定义（file:line） |
| `find_references` | 追踪符号的所有引用（跨文件依赖） |
| `run_tests` | 自动探测并运行测试（pytest/unittest/go test 等） |
| `git_status` | 安全感知仓库改动，不越权提交 |

### 5. 后台自主 ("手") + 并发子 Agent (`core/background.py` + `core/subagents.py`)

青小团不只会在你面前答话——它能**在终端之外自己干活**，还能**同时伸多双手**。

**后台自主模式（"手"）**
- `qxt agent "把本周日志整理成周报"`：提交后终端立刻返回，Agent 在独立会话流里循环推进，复用全部既有工具（shell / web_fetch / filesystem / code）自己联网、读文件、跑命令。进展实时写入 `~/.qingxiaotuan/sessions/` 下的后台会话流，可用 `qxt bg logs <id>` 查看、`qxt bg wait <id>` 等待、`qxt bg cancel <id>` 取消。
- `qxt run "任务" --bg`：headless 任务同样支持后台，不阻塞调用方（CI / 脚本友好）。
- 受 `background.max_turns` 限制防失控；多任务可并行（每个后台任务一个线程 + 独立会话流）。

**并发子 Agent 编排（"多双手"）**
- `core/subagents.py` 的 `SubAgentPool`：把一组**相互独立**的子任务派发给隔离的 Agent 实例——**各自上下文、共享同一个内核工具注册表**——并发或串行执行，每个子任务带独立超时，结果带来源标注汇总回主循环。单点失败不传染其它子任务。
- **进程级沙箱隔离（生产级默认）**：`isolation="process"` 时，每个子 Agent 在**独立子进程**里运行，工作在一个**主工作区的临时副本（沙箱）**上。子进程内的所有写操作（shell / 写文件 / 改仓库）只落在沙箱，**子进程结束即清理，主仓库与世界状态绝不被回写**——这才是真隔离（线程级做不到）。
- `core/sandbox.py` + `core/_sandbox_entry.py`：子进程经临时文件做 JSON 协议往返，主进程收集文本结果；Windows 下用 DEVNULL 规避管道死锁。
- 主 Agent 可直接调用 `dispatch_tasks` 工具并发派活（例如同时调研 N 个来源）；交互模式里用 `/parallel 调研A || 调研B || 调研C` 触发；`DevLoop` 也有 `parallel_research()` 在开始实现前并发摸底。
- 相关配置：`agent.subagent_max_workers`（默认 4）、`agent.subagent_timeout`（默认 180s）、`agent.subagent_isolation`（默认 `process`；`thread` 为线程软隔离，用于受限环境/测试）。

**多 Agent 协作（herdr 式：强模型规划 + 弱模型并发 + 强模型验收）**
- 概念来源：Bilibili《AI超强终端 herdr,让 Agent 互相通信》。核心思想——**不必依赖单一模型**：强模型负责拆解与验收，弱模型（免费档）并发干脏活，整体成本大幅下降，且任一模型挂了不影响其它角色。
- `core/swarm.py` 的 `Swarm` 在 `SubAgentPool` 之上加了一层**角色分工 + 共享黑板**：
  1. **规划（强模型）**：把复杂目标拆成若干独立、可并发的子任务（JSON 输出，容忍模型噪声）；
  2. **执行（弱模型）**：子任务经进程沙箱并发跑，每个子 Agent 用 `model.worker` 指定的便宜模型；
  3. **共享黑板（Blackboard）**：规划结果、各 worker 的产物写在黑板上，有依赖关系的子任务会读到上游黑板结果——子 Agent 之间"读黑板、写黑板"，弱耦合协作；
  4. **验收（强模型）**：强模型综合黑板 + 各产物，产出最终交付物，失败项给出兜底而不是假装成功。
- 交互模式：`/swarm 写三篇开源宣传稿 || 3`（末尾 `|| n` 指定子任务数上限，同时约束弱模型并发度，夹在 2~8）。也可用 `Swarm(kernel, config, workspace, main_agent, n_hint=3).run(goal)` 在代码里调用。
- 角色模型配置（只配 provider 即可，其余从主 model 继承）：
  ```yaml
  model:
    planner: { provider: "" }                       # 留空 = 复用主模型 (强)
    worker:  { provider: opencode-zen }             # 并发执行的"手" (弱, 默认免费档)
  ```

### 6. 缓存命中率工程 + 无感后台压缩（对标 Claude Code）

**目标：在 DeepSeek 上把 prompt cache 命中率做到原生 dsh 的 99% 水平。**

- **系统提示前缀绝对稳定**：`build_system_prompt` 不再接收 `task_hint`——语义召回（记忆/技能）由 `build_task_context()` 生成、贴在**首条 user 消息**前，而非塞进 system。system 在同工作区逐字节一致，前缀可缓存。
- **缓存断点标记**：`openai_compat.py` 给 system 消息与 tools 打 `cache_control: ephemeral`（复制不污染原对象），DeepSeek / OpenCode Zen 稳定前缀命中。
- **无感后台 compact（对标 CC 的 compact）**：`ContextManager.compact_if_needed` 超 `context.compact_trigger` 预算时，用模型把中间段摘要折叠成一条、插在 system 之后，**system 前缀不动**（缓存不击穿），并迭代压缩直到回到预算内（上限 5 次）。
- **命中率可观测 + 实测**：
  - UI 实时显示 `▶ 缓存命中 X%`（解析 DeepSeek 的 `prompt_cache_hit_tokens`）；
  - `qxt bench cache --rounds 8`：用固定长对话脚本跑 N 轮，逐轮打印命中率并给平均，直接告诉你离 dsh 99% 还有多远。
- 相关配置：`context.compact_trigger`（压缩触发阈值，默认 60000）、`model.prompt_cache`（总开关）。

### 内置技能 (Built-in Skills)
首次启动自动 seed 到 `~/.qingxiaotuan/skills/`，并参与系统提示词自动注入：
`frontend-mastery`（前端心法）、`design-aesthetics`（设计审美）、`anthropomorphic-comments`（拟人化注释）、
`code-review-checklist`（代码审查）、`systematic-debugging`（系统化调试）、`test-driven-dev`（TDD 自测）、
`architecture-reading`（架构理解）、`safe-refactor`（安全重构）。

这些技能让基础模型在**前端开发、设计审美、注释风格、代码质量、调试、测试、架构、重构**八个维度直接拔高一大截。

## 配置组合 (Harness 风格)

四层叠加，后者覆盖前者：

```
内置默认  →  ~/.qingxiaotuan/config.yaml  →  profiles/<name>/config.yaml  →  --patch 文件
```

```bash
qxt config dump              # 查看叠加后的最终配置 (排查问题用)
qxt config dump-default      # 查看内置默认配置
qxt config set model.model deepseek-reasoner
qxt --profile work chat                          # 使用 work profile
qxt --patch mypatch.yaml run "任务"              # 一次性覆盖, 不写文件
```

patch 文件使用点分隔路径、**整值替换**语义（与 dsh 一致）：

```yaml
agent.max_iterations: 50
model.model: deepseek-reasoner
```

超时与重试（企业级健壮性）：

```yaml
# 连接与超时 (秒) —— 连接/读分离, 流式场景 read 为分片间最大空闲
model.timeout: 120
model.connect_timeout: 10.0     # 建立 TCP/TLS 连接的最长等待
model.read_timeout: 120.0      # 等待首字节/流式分片的最长空闲
model.max_retries: 3           # 底层 SDK 重试 (已交由 agent 层统一控制, 此处为语义占位)
model.rate_limit:               # 适配免费层限流
  enabled: true
  max_requests_per_minute: 10
  max_concurrent: 1
  respect_429: true

# Agent 层重试策略 (指数退避 + 抖动)
agent.max_retries: 3
agent.retry_backoff: 2.0       # 退避基数, 第 n 次等待 backoff * 2^(n-1) 秒
agent.retry_jitter: 0.3        # 退避抖动比例 (0~1), 避免惊群
agent.retry_on: [408, 409, 429, 500, 502, 503, 504]  # 仅这些状态码重试
```

**错误处理策略**：
- 401/403 鉴权错误 → 立即失败并提示检查 Key，不浪费重试。
- 429 限流 → 优先服从服务端 `Retry-After`；无则按指数退避。
- 5xx/超时 → 指数退避 + 抖动，封顶单次 60s，超过 `max_retries` 才经 `on_error` 回传 UI。
- 所有异常经 `kernel.emit("model.retry")` 记录，可观测。

**输出格式与日志**：
- 终端：rich 着色，INFO 级简洁呈现；ERROR/WARN 带颜色标记。
- 文件：`~/.qingxiaotuan/logs/qxt-YYYYMMDD.log`，DEBUG 级含时间/模块/消息。
- 密钥自动脱敏（日志中 `sk-abc1***`），绝不落明文。
- `logging.audit: true` 时记录完整 prompt/completion（默认关闭）。

**密钥安全管理**：
- 密钥只从环境变量读取：`model.api_key_env` 指定的变量 → 通用 `QXT_API_KEY` → `model.api_key_ref` 引用。
- `qxt setup` 把密钥写入 `~/.qingxiaotuan/.env`（权限 600），绝不进 `config.yaml` 明文。
- `.env` 已在 `.gitignore` 中排除，并提供 `.env.example` 模板。

## 自进化闭环 (Hermes 风格)

```
工具执行 → emit tool.executed 事件 → SelfImprovePlugin 复盘 (Reflector)
    → 经验 (denied/failure/slow/repeated_ok) → RuleGenerator 固化规则
    → SelfImproveRuleStore 落盘即加载 → 下次 ToolRegistry.dispatch 前实时查询
    → 命中 learned 护栏 → 升级为「必须人工确认」(不静默硬阻断, 控制权留给人)
    → SkillGenerator 生成技能草稿 (需人工审阅后激活)
```

闭环特点：**可审计、可重放、不失控**。生成的规则写入 `qingxiaotuan/skills/learned/self_improve.jsonl`（带 `meta.source=self-improve`），下次启动时自动加载生效；技能草稿默认不自动激活，避免 Agent「自己给自己加权限」。

```bash
qxt improve summarize    # 复盘并预览将生成的规则/技能 (dry-run, 不落盘)
qxt improve apply        # 把经验固化为规则 + 技能草稿, 规则立即生效
```

三层记忆：

1. **会话上下文** — 超长自动压缩（折叠中间历史）
2. **持久事实** — `memories/MEMORY.md` + `USER.md`，Agent 用 `memory_write` 主动记；
   带**增长保护**：`MEMORY.md` 超过 2000 条、`USER.md` 超过 500 条时自动裁剪最旧条目，
   防止长期运行无限膨胀（任何写入故障都只记日志、不中断主流程）。
3. **程序性技能** — Markdown 技能文件，FTS5 全文索引可跨会话检索

```bash
qxt skill list / show <name>
qxt memory list / search <关键词>
```

## 家目录结构

```
~/.qingxiaotuan/
├── config.yaml        # 用户层配置
├── SOUL.md            # Agent 身份 (系统提示词第一位)
├── memories/          # MEMORY.md, USER.md, index.db (FTS5)
├── skills/            # 自蒸馏技能 *.md
├── sessions/          # append-only 会话事件流 *.jsonl (可审计/回放/恢复)
├── cron/              # 定时任务 jobs.json
└── profiles/<name>/   # Profile 配置
```

## 定时任务

```bash
qxt cron add "每日总结" "总结工作区今天的变化" --interval 1440
qxt cron list / remove <id>
qxt cron tick                       # 执行所有到期任务 (一次)
qxt cron start                      # 常驻调度守护: 自动执行到期任务, 无需系统计划任务
qxt cron start --detach             # 后台分离运行 (写 PID, 返回终端, 适合开机自启)
```

守护进程每 60s 检查一次到期任务并执行，无人值守时危险操作默认拒绝（`confirm=False`）；
每次执行结果写入独立会话流（`~/.qingxiaotuan/sessions/`），可用 `qxt session list` 回溯。
`--detach` 在 Windows 下以无窗口子进程方式拉起；停止它只需结束对应进程或删除
`~/.qingxiaotuan/cron/daemon.pid`。


## 模型适配 —— 不绑架任何一家模型

青小团的核心卖点之一：**模型是可插拔的，想用哪个"脑子"就用哪个。**
DeepSeek 不强时，直接切到 Claude / Gemini / 本地网关（Ollama / vLLM / LM Studio）即可。

默认走 OpenAI 兼容协议（`chat/completions` + `tools`），一套 `OpenAICompatAdapter` 通吃：
- **48 家开箱即用供应商**：覆盖中国主流（DeepSeek/通义千问/Kimi/GLM/豆包/百度文心等）、国际主流（OpenAI/Anthropic/Gemini/Mistral/xAI等）、聚合网关（OpenRouter/SiliconFlow/Groq等）、云平台（火山引擎/百度千帆/AWS Bedrock/Azure等）、免费层、本地部署（Ollama/LM Studio/vLLM）。
- **任意自定义网关**：未知 provider 会被当作 `openai-compatible` 网关（Claude 中转 / Gemini 网关 / 本地 `/v1`），**只要给 `base_url` 即可**，不再报错拒绝。
- 新协议实现 `models/base.py` 的 `ModelAdapter` 接口即插即用。

```bash
# 交互式挑一家 (48 家, 按分类浏览/搜索/过滤, 自动带好地址/密钥变量)
qxt model

# 查看当前脑子
qxt model current
qxt model list-providers          # 列出全部 48 家供应商 (按分类)

# 切换供应商 (写盘, 下次启动生效)
qxt model set openai-compatible gpt-4o https://api.openai.com/v1 OPENAI_API_KEY
qxt model set claude-gw claude-3-5-sonnet https://my-gateway.example/v1 GATEWAY_KEY

# 运行时热切换 (当前会话立即换脑子, 不写盘)
qxt model switch openai-compatible gpt-4o https://api.openai.com/v1

# 也可在交互会话里热切换:
#   /model                      # 显示当前供应商/base_url/密钥变量 + 多 Agent 角色
#   /model switch <provider> <model> [base_url] [api_key_env]
#   /model planner <provider> <model> [base_url] [api_key_env]   # 配置强模型(规划/验收)角色
#   /model worker  <provider> <model> [base_url] [api_key_env]   # 配置弱模型(并发执行)角色
#   /model planner              # 不带参数则显示当前角色配置

qxt --model deepseek-v4-flash-free        # 单次临时覆盖模型 (顶层 flag)
qxt config profiles                        # 查看内置预设 profile
qxt config set model.temperature 0.3      # 写用户层配置
qxt config set model.worker '{"provider": "opencode-zen"}'   # 嵌套 JSON 也支持 (弱模型层)
```

> 多 Agent 角色（`/model planner` / `/model worker`）与 `model.planner` / `model.worker`
> 配置项一一对应，写盘后在 `/swarm` 中自动生效：planner 负责规划与验收（留空则复用主模型），
> worker 负责并发执行（默认 opencode-zen 免费档）。

切换逻辑在 `qingxiaotuan/models/plugin.py` 的 `ModelPlugin.switch_model()`：写回内核配置视图
→ 重建适配器 → `kernel.unprovide/provide` 重新注册 `model_adapter` 服务，运行中的 Agent 立即生效。

## 开发

```bash
pip install -e ".[dev]"
pytest tests/ -q     # 120+ 离线测试 (内核/配置/记忆/技能/工具/Agent循环/上下文/代码工具/DevLoop/双模式/YOLO/MCP/OpenCode Zen接入/多供应商热切换/缓存命中/沙箱隔离/多Agent协作黑板)
```

插件化扩展：实现 `kernel.Plugin`，在 `activate()` 里 `kernel.provide()` 服务或
向 `tool_registry` 注册工具，挂进 `app.build_kernel()` 即可。

## 路线图

- [x] 微内核 + 插件化工具/模型/记忆/技能
- [x] ReAct 主循环 + 技能自进化蒸馏（Skill）
- [x] 三层记忆（FTS5 跨会话检索）+ append-only 会话事件流 + 断点续聊
- [x] 自主开发循环 DevLoop（分析→规划→实现→自测→核实→汇报）
- [x] 后台自主（"手"）+ 进程级沙箱并发子 Agent（"多双手"）
- [x] 多 Agent 协作（herdr 式：强模型规划/验收 + 弱模型并发执行 + 共享黑板通信）
- [x] 48 家模型供应商开箱即用（DeepSeek / OpenAI / Claude / Gemini / 通义千问 / Kimi / 豆包 / Groq / Ollama 等，按数字选即可）
- [x] 多供应商运行时热切换（一套适配器通吃，任意 OpenAI 兼容网关）
- [x] 对标 Claude Code 的 prompt 缓存工程（稳定 system 前缀，命中率冲 99%）+ 无感后台 compact
- [ ] 原生 MCP 市场一键接入
- [ ] 多模态（图片/文件）工具与回复
- [ ] 会话/技能 Web 看板
- [ ] 分布式子 Agent 调度（跨机）

## 致谢

青小团站在两个开源项目的肩膀上，并做了超越式自研：

- **DeepSeek Harness (dsh)** —— 插件化微内核、Profile 组合式配置、模型中立适配、headless 任务、append-only 会话事件流的设计源头。
- **Hermes Agent** —— 三层记忆、技能自进化闭环、SOUL.md 身份、FTS5 跨会话检索的理念来源。

它们提供了"养分"，但青小团的主体是自研的独立 Agent：后台自主、进程级沙箱并发、多供应商热切换、缓存与 compact 工程，都是围绕"终端里真正好用的 Agent"这一目标重新写就的。

## License

MIT —— 见 [LICENSE](./LICENSE)。
