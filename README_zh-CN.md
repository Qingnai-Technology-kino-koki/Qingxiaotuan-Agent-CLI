# 青小团 · Qingxiaotuan Agent CLI

> **「Model + Harness = Agent」** —— 把「会思考」和「靠谱地跑」拆开，两者都交到你手里。
> 一个安全优先、模型无关、纯 Python 的 AI Agent Harness。`v0.2.014` · MIT · Python ≥ 3.10

**语言/Language:** [English](README.md) · **简体中文** · [繁體中文](README_zh-TW.md) · [日本語](README_ja.md) · [한국어](README_ko.md) · [Español](README_es.md) · [Português (Brasil)](README_pt-BR.md) · [Français](README_fr.md) · [Deutsch](README_de.md) · [Русский](README_ru.md)

**翻牌子前先看：** [SECURITY.md](SECURITY.md)（威胁模型）· [CHANGELOG.md](CHANGELOG.md)（版本纪律）· [ARCHITECTURE.md](ARCHITECTURE.md)（架构）· `qxt models list-providers`

---

## 一句话

别人家的是「套壳一个模型」，青小团是「给你一整个能换脑子的躯壳」。模型热切换、48 家供应商、本地离线都能跑；命令出事能撤回、动手前先算影响半径；微内核插件任你插。**模型负责想，它负责稳。**

**它不是什么**：不是某家模型的私生子（热切换、自托管、离线随你）；不是 IDE 的附庸（标准终端工具，可被 ACP/IDE 驱动）；不是一层套娃（面向开发者的微内核插件架构 + 面向普通人的一条 `setup`）。

---

## 什么让它不一样

| 它给的 | 有多顶 |
|---|---|
| 🛡️ **安全优先** | 四道闸：静态评分、影响半径预拦截、YOLO 红线兜底、事务化账本 `/undo` 精确回滚 |
| 🔌 **模型无关** | 48 家供应商 + 本地 Ollama + 运行时热切换 + 自动路由（`router.*`） |
| 🧠 **三种主循环** | ReAct / Planner-Execute / DevLoop，可插拔，"同一个内核跑不同的思考节奏" |
| 🔧 **微内核** | `@plugin` 一行声明，服务注册表、append-only 事件总线、hook 中间件，爱折腾的人有福了 |
| 🗂️ **记忆** | SQLite FTS5 + 会话事件流；三层记忆、`/undo`、checkpoint、replay、Trajectory 导出 |
| 🧩 **生态** | MCP（Model Context Protocol）+ ACP（Agent Client Protocol），能插工具也能被 IDE 驱动 |
| 🌍 **十种语言** | 默认简体中文，界面随机给你换语种，接口也本地化 |
| 🐍 **纯 Python** | ~414 个 `.py` / ~7.7 万行 / 32 包 / 15+ 插件，MIT，想怎么啃怎么啃 |

---

## 快问快答

**Q：它真的是「独立自研」吗？不遮不掩说说。**
内核与绝大部分能力（`kernel/`、`core/`、`acp/`、`tools/`、`ports/`）都是用 Python **从架构到实现一行行自研**的，只对相关交互与协议做接口对齐。唯一例外是终端 TUI：那套 `--tui` 的交互手感与配色是**刻意沿用 Kimi Code 的招牌风格**（`#4FA8FF` 主色、moon 旋转加载、两行状态栏），因为「手感好就不折腾」——这是唯一保留其风格的部分，版权与署名见 [NOTICE](NOTICE)。其余全部是青小团自己的血肉。

**Q：凭什么命令执行前总拦我？**
特性，不是 bug。危险命令默认要确认；YOLO 命中硬红线也一样拒。嫌烦就 `qxt safe allow <cmd>` 显式放行，别关安全。

**Q：/undo 能撤掉啥？**
所有进账本的写操作：单文件、单 step、整段。底层 = 事务化账本 + diff `reverse_transform` + 检查点快照。不是万灵丹，但把「误操作 = 必亏」变成「大概率能捞回来」。

**Q：怕它读我隐私文件？**
权限策略用 domain allow-list 圈住工具范围；网络出口有管控防外带；写出去的输出会对你密钥做脱敏。安全不是口号，是它每天的 KPI。

**Q：想完全离线？**
`qxt models local` 探测，`/offline` 管理 Ollama。没网也照跑。

**Q：能写自己的工具/插件吗？**
当然。`@plugin` 声明元数据，`activate(kernel)` 里 `kernel.provide(...)` / `kernel.require(...)` 注册与取用服务，三步接入：

```python
from ..core.kernel import Kernel, Plugin

@plugin("my.tool", provides=["loop_registry"])
class MyTool(Plugin):
    def activate(self, kernel):
        def _handler(ctx, query: str) -> str:
            return f"echo: {query}"
        kernel.provide("tools.my", _handler)
```

---

## 快速上手

```bash
# 1) 装（自带 [dev] 全套餐；只要 API 可省 [openai] [mcp]）
git clone <此仓库> && cd qingxiaotuan-agent-cli
python -m venv .venv && source .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -e ".[dev]"

# 2) 配置一个模型（deepseek / groq-free / siliconflow-free / github-models / ollama …14 个内置 profile）
qxt setup
# 或手动：编辑 ~/.qingxiaotuan/config.yaml
#   model.provider: deepseek
#   model.model: deepseek-chat
#   model.api_key_env: DEEPSEEK_API_KEY      # 密钥从不写明文

# 3) 开聊
qxt
# 或一句话冒烟：
qxt --print run "你好，一句话介绍你自己"
```

---

## 命令行面：25 个子命令 + 36 个斜杠命令

上手常用的几个：

| 命令 | 干啥 |
|---|---|
| `qxt` | 交互式 TUI（Kimi Code 皮肤） |
| `qxt setup` / `qxt models` | 配供应商 / 列出模型（48 家） |
| `qxt agent` | 命名 Agents（`.claude/agents` 兼容 + 三层发现） |
| `qxt acp` | 启动 ACP server，让 VS Code / Zed / JetBrains 来驱动你 |
| `qxt cron` | 后台定时任务 |
| `qxt doctor` / `qxt bench` | 体检 / 跑分 |
| `qxt arch demo` | 一键验证五层架构插件是否就位 |

历史最常用的斜杠命令：`/plan`（只读模式）· `/model`（切模型）· `/undo`·`/impact`（回滚+影响半径）· `/swarm`（多 Agent 协作）· `/log`·`/stats`·`/cost`·`/budget`·`/goal`·`/sandbox`·`/offline`·`/verify`·`/audit`·`/more`·`/help`。完整列表在 TUI 里按 `Ctrl-G`。

---

## 三种主循环，同一种稳

- **ReActLoop**：想→做→看，默认节奏。
- **PlannerExecuteLoop**：先让强模型拆计划、再用便宜模型打执行（`router.*` 支持 plan/execute 分舱）。省钱大师。
- **DevLoop**：自动写→测→验的自找bug闭环（`/verify`，识别 Python/Node/Rust/Go 自动推断测试命令并自愈，最多 N 轮）。

---

## 安全模型：四道闸 + 记账撤销

1. **闸一 · 静态评分**：每个 shell 命令执行前用 `safety_engine.score()` 判风险（none→critical），含间接调用展开（IFS、`$VAR`、命令替换、ANSI-C/八/十六进转义、PowerShell Base64、Unicode NFKC，递归 ≤32 层）。
2. **闸二 · 影响半径预拦截**：危险命令在**执行前**就告诉你它会碰到啥，`--impact` 可视化。
3. **闸三 · YOLO 红线兜底**：YOLO 可关闭逐条确认，但**关不掉**硬红线——文件系统/OS 级破坏（递归 `rm`、force-push、`chmod -R 000 /`…）永不可自动执行。
4. **闸四 · 事务化账本**：一切写操作记账，`/undo` 用 diff `reverse_transform` + 快照精确还原。

规则优先级恒为 `deny > ask > allow`。要更顺手就用 `qxt safe allow <cmd>` 白名单，而不是一键关安全。

---

## 大开脑洞的地方

- **记忆**：三层（用户级/项目级/会话级）+ SQLite FTS5（trigram），自动检测不可用就降级纯文本。
- **子代理**：类型化委派（general-purpose / explore / plan / coder）+ 隔离子代理 + 并发 Worker + Swarm 切碎长任务。
- **Cron & 后台**：`qxt cron start --detach`；headless 也能自己跑。
- **Hooks**：`PreToolUse` 能 `block` 或改写 `args`（`hooks.allow_edit_args`），给二开留了极大的缝。
- **可观测**：`/stats`·`/audit`·`/impact`·`/bench`·`/cost` 齐全，成本摆上桌面。
- **crypto**：v2 已经够硬——装了 `cryptography` 走 AES-GCM(AEAD)，否则 HMAC-SHA256 流密码，`open()` 缺 MAC/被篡改一律 fail-closed。

---

## 开发 & 契约

```bash
python -m pytest tests/ -q                 # 2000+ 用例
python -m mypy qingxiaotuan                # 类型门禁
qxt --print run "你好，一句话介绍你自己"     # 冒烟
```

- **多语言文档纪律**：以 `README_zh-CN.md`（本文件）为权威母本，新增内容同步到全部 10 份，对译求「生动、零漂移」，**禁机械直译**。
- **规模**：~414 `.py` / ~7.7 万行 / 32 包 / 插件 15+。
- **版本**：`v0.2.014`（0.x/Beta），破坏性变更会在小版本预告并提供迁移提示。
- **深挖**：附录实现参考（九大引擎签名、新增工具走查）在原文末尾，插桩/二开/调试党请直接翻 `README_zh-CN.md` 尾部。

---

## License & 关联阅读

- **License**：MIT（自由使用/修改/分发，保留版权声明）
- **必读**：[SECURITY.md](SECURITY.md) · [CHANGELOG.md](CHANGELOG.md) · [ARCHITECTURE.md](ARCHITECTURE.md) · [NOTICE](NOTICE)（TUI 风格署名）

> 要「开箱即用的闭源体验」有别的选择；要「跑在自己的模型上、拿得到控制权、出事了能撤回」——钥匙就在这。