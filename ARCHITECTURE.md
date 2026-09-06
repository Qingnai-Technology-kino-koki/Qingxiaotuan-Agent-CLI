# 青小团 (qxt) 架构文档

> 目标：让新贡献者 10 分钟看懂「内核怎么转、安全怎么拦、Loop 怎么切、模型怎么路由」，
> 降低上手与贡献门槛。配套命令：`qxt others`（能力目录）、`qxt safe`（安全总入口）、
> `qxt models`（模型与本地 LLM）。

青小团是一个**微内核 + 插件**架构的 Agent CLI。内核本身不携带任何 Agent 能力，
只负责「插件注册 / 服务发现 / 生命周期事件」；所有能力（工具、模型适配器、记忆、
技能、安全、路由、Loop）都以**插件**形式挂载。

公式：**模型负责思考，Harness（青小团）负责让思考可控地运行。**

---

## 1. 五大子系统一览

| 子系统 | 职责 | 核心模块 | 关键类 / 函数 |
| --- | --- | --- | --- |
| **Kernel** | 微内核：插件生命周期、服务注册表、事件总线 | `core/kernel.py` | `Kernel` · `Plugin` · `@plugin` · `ServiceContainer` · `MiddlewareEventBus` |
| **Plugin** | 能力即插件：工具 / 模型 / 记忆 / 安全 / Loop 都注册为服务 | `core/loop_plugin.py` 及各 `@plugin` 类 | `LoopPlugin` · `provide()` / `require()` |
| **Loop** | Agent 主循环策略：ReAct / 规划-执行 / 开发循环，可热切换 | `core/loop_provider.py` · `core/loop_plugin.py` | `LoopRegistry` · `ReActLoop` · `PlannerExecuteLoop` · `DevLoopProvider` · `set_current()` |
| **Security** | 红线拦截、白名单放行、审计落盘、本地黑名单减负 | `ext/safety_engine.py` · `core/whitelist.py` · `core/security_bus.py` · `core/blacklist_override.py` · `ext/security_gate.py` | `is_redline()` · `is_hard_redline()` · `score()` · `WhitelistManager` · `SecurityEventBus` · `emit_command_blocked()` |
| **Router** | 按任务难度自动升级/降级模型，省成本不降质，fail-safe | `models/router.py` · `core/auto_route.py` · `models/provider_catalog.py` | `ModelRouter.decide()` · `AutoRouter` · `RouteSession` · `decide_override()` |

---

## 2. 五大子系统交互图

```
                         ┌──────────────────────────────────────────┐
                         │                Kernel (微内核)              │
                         │  ServiceContainer  ·  MiddlewareEventBus   │
                         │  provide("loop_provider") / require(...)    │
                         └───────────────┬───────────────┬────────────┘
                                         │ 激活插件       │ 激活插件
                       ┌─────────────────┘               └─────────────────┐
                       ▼                                                   ▼
              ┌─────────────────┐                                ┌────────────────────┐
              │  Loop Plugin    │  注册 LoopRegistry             │  其它插件 (工具/模型/ │
              │  (loop_plugin)  │  ── ReAct / Planner / Dev      │   记忆/技能/MCP...)   │
              └────────┬────────┘                                └─────────┬──────────┘
                       │ set_current(name)                                │ 取模型适配器
                       ▼                                                  ▼
              ┌─────────────────┐  每个工具调用前               ┌────────────────────┐
              │  LoopRegistry   │ ───────────────────────────▶ │   Security 子系统    │
              │  get_current()  │   工具护栏 _pre_exec_guard     │  is_redline? / 白名单?│
              └────────┬────────┘                               └─────────┬──────────┘
                       │ 执行工具                                       │ 拦截→emit / 放行
                       ▼                                                  ▼
              ┌─────────────────┐                      ┌──────────────────────────┐
              │   Agent.run()   │ ── 难度/失败观察 ──▶  │  SecurityEventBus          │
              │  (think→tool)   │                      │  emit_command_blocked()    │
              └────────┬────────┘                      │  → security-audit.jsonl    │
                       │ 自动路由                                       └──────────────────────────┘
                       ▼
              ┌─────────────────┐
              │  Router 子系统   │  ModelRouter.decide() / AutoRouter.decide_override()
              │  升级 / 降级模型  │  失败保险: 无凭证绝不切换
              └─────────────────┘
```

调用链一句话：**Kernel 装载插件 → Loop 决定怎么思考 → Security 在每次工具执行前把关 →
Router 按难度/卡住情况升降级模型 → 所有安全事件落盘审计。**

---

## 3. 请求生命周期（一次 `qxt run "任务"` 的完整旅程）

```
[1] 启动 (fast_start / main)
    └─ load_dotenv() 把 ~/.qingxiaotuan/.env 的密钥载入环境
    └─ build_parser() 解析子命令 (run / dev / agent / safe / models / others ...)

[2] Kernel 启动并激活插件
    └─ Kernel 扫描 @plugin 类, 按 requires 拓扑排序后逐个 activate()
    └─ config 插件先激活 (提供 "config" 服务)
    └─ LoopPlugin.activate(): 建 LoopRegistry, register(ReAct/Planner/Dev)
       按 config.loop.provider 设默认 → provide("loop_registry") / provide("loop_provider")
    └─ 模型 / 工具 / 记忆 / 技能 / 安全 插件各自 provide() 出服务

[3] 构造 Agent
    └─ Agent 从 kernel.require("loop_provider") 拿到当前 Loop
    └─ Agent 持有 model 适配器、工具集、router、auto_route 状态

[4] Loop 驱动主循环 (think → tool → observe → think ...)
    └─ 每轮: 模型产出下一步 (可能是工具调用)

[5] Security 把关 (核心！每次工具执行前)
    └─ tools/shell.py _pre_exec_guard 调用安全引擎:
       · is_hard_redline(cmd) ? → 永不自动执行 (rm -rf / / dd / force push / shutdown ...)
       · is_redline(cmd)       ? → 关键级, 多阶段确认 (含 SQL 破坏性操作)
       · WhitelistManager 命中 ? → 直接放行 (用户在本地自主添加)
       · 通过 → 执行; 拦截 → SecurityEventBus.emit_command_blocked(cmd, reason)
    └─ MCP 调用经 security_gate.decide_mcp_tool(); 文件写入经 decide_file_write()
    └─ 审计事件异步落盘 ~/.qingxiaotuan/security-audit.jsonl

[6] Router 升降级模型 (与 Loop 并行, 每轮决策)
    └─ AutoRouter.observe_turn() 统计「连续失败轮数」
    └─ AutoRouter.decide_override(session) → 难度覆盖值 (0/2/10)
    └─ agent._maybe_route_model 调用 ModelRouter.decide(task, difficulty, available_providers)
       · 简单/卡住救场结束 → 降级到便宜模型 (省钱)
       · 难/卡住 → 升级到强模型 (保质量)
       · 失败保险: 候选模型无配置密钥 → 保持当前, 绝不切到无凭证端点

[7] 收尾
    └─ Loop 返回最终答案; undo/impact 账本记录改动
    └─ session 落盘; 安全审计保留可追溯
```

---

## 4. 各子系统详解

### 4.1 Kernel（微内核）
- 文件：`core/kernel.py`
- 三件事：**插件注册/激活/依赖解析**、**服务注册表**（插件间经服务名发现，不直接 import）、
  **生命周期事件**（append-only 事件总线 + 中间件管线）。
- 关键 API：
  - `@plugin("svc.name", provides=[...], requires=[...])` 一行声明插件元数据。
  - `kernel.provide("name", impl)` / `kernel.require("name")` 服务注册与发现。
  - `kernel.emit("event", payload)` / `MiddlewareEventBus` 支持中间件拦截与改事件。
  - `ServiceContainer`：懒工厂 (`provide_factory`)、生命周期钩子、类型安全 `typed()`。
- 设计要点：**内核零业务**。任何能力想加，都做成插件，不要往内核塞逻辑。

### 4.2 Plugin（能力即插件）
- 模式：子类 `Plugin` + 实现 `activate(kernel)`（在此 `provide` 服务）。
- 示例 `LoopPlugin`（`core/loop_plugin.py`）：声明 `provides=["loop_registry","loop_provider"]`、
  `requires=["config"]`；`activate` 里建 `LoopRegistry` 并注册三种内置 Loop。
- 运行时切换：`LoopPlugin.switch_loop(kernel, name)` → `registry.set_current(name)` +
  更新 `loop_provider` 快捷引用 + `kernel.emit("loop.switched", ...)`（供 `/loop` 命令）。
- 贡献新能力：写一个 `@plugin(...)` 类，`activate` 里 `provide` 你的服务即可，无需改内核。

### 4.3 Loop（主循环策略）
- 文件：`core/loop_provider.py` · 注册：`core/loop_plugin.py`
- `LoopRegistry`：`register(loop)` / `set_current(name)` / `get_current()` / `current_name` /
  `list_available()`。
- 三种内置实现：
  - `ReActLoop`（默认）：标准 think→tool→observe。
  - `PlannerExecuteLoop`：强模型规划、便宜模型执行。
  - `DevLoopProvider`：包装 DevLoop，自主迭代开发。
- 切换语义：`set_current("planner")` 立即换「脑子」，不影响已加载的服务；Agent 每轮读
  `kernel.require("loop_provider")` 拿当前 Loop。
- **硬红线语义**：Loop 切换是策略切换，不改变 Security 的拦截口径（见 4.4）。

### 4.4 Security（安全子系统）
- 文件：
  - `ext/safety_engine.py` — 红线判定（`is_redline` / `is_hard_redline` / `score`）
    与模式库（`_CRITICAL_PATTERNS` / `_HIGH_PATTERNS` / `_MEDIUM_PATTERNS`）。
  - `core/whitelist.py` — `WhitelistManager`：用户本地自主添加的放行名单。
  - `core/security_bus.py` — `SecurityEventBus`：事件总线 + JSONL 持久化
    （默认落盘 `~/.qingxiaotuan/security-audit.jsonl`）。
  - `ext/security_gate.py` — 统一闸门：`decide_shell` / `decide_mcp_tool` / `decide_file_write`。
  - `core/blacklist_override.py` — **用户本地黑名单减负**（见下）。
  - `tools/shell.py` — `_pre_exec_guard`：每次 shell 工具执行前的实际拦截点。
- 两层判定：
  1. **硬红线**（`is_hard_redline`）：OS/文件系统级毁灭操作（rm -rf /、dd、mkfs、force push、
     shutdown 等 token 化检测）。即使在 YOLO 模式也**永不自动执行**。
  2. **综合红线**（`is_redline`）：在硬红线基础上叠加 `_CRITICAL_PATTERNS` 正则库（含 SQL
     破坏性操作 DROP/DELETE/TRUNCATE，归为「可确认关键级」，经多阶段确认放行）。
- **审计落盘**：拦截/放行事件经 `emit_command_blocked` / `emit_command_allowed` 写入
  `security-audit.jsonl`，可用 `qxt safe status` 查看路径与统计。
- **用户本地黑名单减负（`blacklist_override`）**：某些内置模式在用户环境属误杀或可信。
  用户在 `~/.qingxiaotuan/blacklist-override.json` 声明要抑制的模式 label 关键字，
  `is_redline` / `is_hard_redline` / `score` 即跳过这些模式。
  - 安全边界：**只抑制正则模式库条目**；不可逆/OS 级的 token 化硬红线（rm -rf / force push /
    shutdown …）走独立判定，**永不**受减负影响。
  - 用法：`qxt safe reduce <关键字>`（如 `qxt safe reduce format`）、
    `qxt safe restore <关键字>`、`qxt safe blacklist` 查看清单。
- 白名单：`qxt safe allow <命令>` 把可信命令加入（红线命令拒绝加入）；`qxt safe deny` 移除。
- 多阶段确认：极高风险 5 次、高风险 3 次确认（见 `ext/security_gate.py`），含 TOCTOU 脚本检测。

### 4.5 Router（模型路由）
- 文件：`models/router.py` · `core/auto_route.py` · `models/provider_catalog.py`
- `ModelRouter.decide(task, context, current_provider, current_model, available_providers, …)`
  返回 `{switch, reason, provider, model, capabilities, difficulty}`。
- 决策规则（省钱 + 不降质 + 失败保险）：
  - 自定义模型（不在预设）→ 不切换，尊重用户指定的「脑子」。
  - 难度超出当前模型能力，或缺少必需能力（如挂图需 vision）→ **升级**。
  - 更便宜且仍能胜任 → **降级**省钱。
  - 候选模型**无配置密钥** → 保持当前，绝不切到无凭证端点（核心 fail-safe）。
- `AutoRouter`（规划-执行 + 卡住升级）：
  - `RouteSession` 跨 turn 记录 `stuck`（连续失败轮数）。
  - `plan_execute`：规划首轮用强模型（难度 10），执行阶段用便宜模型（难度 2）。
  - `escalate_on_stuck`：便宜模型连续失败达到阈值 → 升级强模型救场，救完降回。
- 本地模型：`models/provider_catalog.py` 预置 `ollama`（:11434）、`llamacpp`（:8080/v1）等
  自托管供应商；`models/openai_compat.py` 对 localhost 端点放通空 key（本地 LLM 无需密钥）；
  `models/offline.py` 的 `detect_local_models()` 探测本机 Ollama + llama.cpp 模型
  （`qxt models local` 调用）。

---

## 5. 关键数据流：安全拦截 → 审计记录 → Loop 切换 → 模型路由升降级

这是 E2E 测试基线（`tests/test_e2e_security_loop_routing.py`）覆盖的全链路：

```
命令字符串
  │
  ▼  tools/shell._pre_exec_guard
is_redline(cmd) / is_hard_redline(cmd)
  ├─ 命中硬红线              → 拦截 (YOLO 也不放行)
  ├─ 命中综合红线            → 多阶段确认
  ├─ 命中白名单              → 放行
  └─ 通过                    → 执行
  │
  ▼  SecurityEventBus.emit_command_blocked(cmd, reason)
persist → ~/.qingxiaotuan/security-audit.jsonl   ← 审计记录
  │
  ▼  LoopRegistry.set_current(name)              ← Loop 切换 (策略热换)
Agent 用新 Loop 跑下一轮
  │
  ▼  AutoRouter.observe_turn / decide_override
ModelRouter.decide(task, difficulty, available_providers)
  ├─ switch=True, reason=升级  → 换强模型
  └─ switch=True, reason=降级  → 换便宜模型    ← 模型路由升降级
```

四个阶段各自可独立单测，组合即「安全→审计→Loop→路由」端到端闭环。

---

## 6. 扩展点（贡献指南）

| 想加什么 | 怎么做 | 入口文件 |
| --- | --- | --- |
| 新 Loop 策略 | 继承 `LoopProvider`，在 `LoopPlugin.activate` 里 `register` | `core/loop_provider.py` |
| 新安全责任/模式 | 在 `_CRITICAL/_HIGH/_MEDIUM_PATTERNS` 加 `(regex, label)`；红线命令拒绝加白名单 | `ext/safety_engine.py` |
| 本地黑名单减负 | 用户侧 `qxt safe reduce <label关键字>`，无需改代码 | `core/blacklist_override.py` |
| 新模型供应商 | 在 `provider_catalog.py` 加 `ProviderPreset`（含 `category="本地部署"` 可标本地） | `models/provider_catalog.py` |
| 新插件能力 | `@plugin("svc", provides=[...], requires=[...])` + `activate` 里 `provide` | `core/kernel.py` |
| 新 CLI 子命令 | 在 `cli/parser.py` 加 subparser + `_resolve_func` 分支 + `cli/cmd_*.py` | `qingxiaotuan/cli/` |

---

## 7. 本地化与配置

- 主目录：`~/.qingxiaotuan`（可用环境变量 `QXT_HOME` 覆盖，便于测试隔离）。
- 关键文件：
  - `security-audit.jsonl` — 安全审计落盘（可通过 `qxt safe status` 查看）。
  - `whitelist.json` — 白名单。
  - `blacklist-override.json` — 本地黑名单减负声明。
  - `.env` — 密钥集中管理（权限应 600，不进版本库）。
- 本地模型：安装 Ollama（:11434）或 llama.cpp（:8080/v1）后，`qxt models local` 探测，
  `qxt models set ollama <模型>` / `qxt models set llamacpp <模型>` 接入。

---

## 8. 相关文档
- `qxt help` — 完整子命令与参数。
- `qxt others` — 友好能力目录（新手入口）。
- `qxt safe` — 安全总入口（状态 / 白名单 / 黑名单减负 / 更新）。
- `qxt models` — 模型与本地 LLM 管理。
- `SECURITY.md` · `CONTRIBUTING.md` · `CHANGELOG.md` — 安全策略 / 贡献规范 / 变更记录。
