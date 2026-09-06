# 青小团 Agent CLI（Qingxiaotuan Agent CLI）代码实现评价报告

> 评审对象：`E:\Qingxiaotuan Agent CLI`（版本 0.2.011, MIT）
> 评审方式：对 `qingxiaotuan/` 下 139 个 `.py` 源文件（约 30,200 行）逐模块静态阅读 + 跨模块集成核对，并直接复读关键源码确认若干重大结论。
> 评审范围：`core/`、`models/`、`cli/`、`ext/`（10 引擎）、`tools/`、`skills/`、`self_improve/`、`i18n/`、`ui/`、`config/`、`hooks/`、`context/`、`memory/`、`cron/`、`audit/`、`vision/`。
> 结论先行：**架构水准高、子系统工程扎实、测试覆盖广；但"安全优先"这一核心卖点的若干实现存在真实缺陷（尤其是 crypto 与 safety 引擎），须在对外宣称"生产可用"前修复。**

---

## 1. 总体结论

| 维度 | 评分（10） | 说明 |
|---|---|---|
| 架构设计 | 8 | 微内核 + 事件总线 + 插件化，分层清晰，扩展点合理 |
| 旗舰功能正确性 | 5 | safety / crypto 引擎存在可构造绕过与密码学缺陷 |
| 工程健壮性 | 8 | 异常隔离、原子写、崩溃恢复、降级路径普遍到位 |
| 测试 | 8 | 88 个测试文件、400+ 离线用例、mock server 端到端 |
| 可维护性 | 6 | `commands.py` 3057 行巨模块、少量 God 函数与重复代码 |
| 文档诚实度 | 7 | 多数模块 docstring 坦诚（含已知边界），但有 3 处文档/实现脱节 |

**一句话**：这是一个野心很大、底层功底扎实的项目，许多子系统（ledger 事务回滚、MCP 桥接、记忆、上下文压缩、i18n）达到"世界级"水准；但恰恰是最被强调的"安全护栏"与"文件加密"两块存在会直接削弱核心价值的 bug，属于"架构优秀、关键实现欠打磨"。

---

## 2. 架构速览

```
qingxiaotuan/
├── cli/          命令入口与 20 个斜杠命令（commands.py 为巨模块）
├── core/         微内核(kernel)、Agent 主循环、重试/限流、后台、账本、验证闭环
├── models/       模型路由(router)、供应商目录、OpenAI 兼容/Anthropic 适配器
├── ext/          10 个纯 Python 引擎（diff/crypto/index/ansi/safety/json/search/notify/rules/skill-market）
├── tools/        工具注册/分发/权限、外部引擎桥接、MCP 桥接
├── skills/       技能管理与插件 SDK
├── self_improve/ 自反思→规则/技能闭环
├── i18n/         10 语言（零漂移）
├── ui/           REPL + 全屏 TUI + 主题
├── config/       分层配置加载
├── hooks/        命令/URL/Prompt/子代理钩子
├── context/      上下文压缩 + 代码索引
├── memory/        本地记忆（FTS5）
├── cron/         定时任务
├── audit/        审计落盘 + 脱敏
└── vision/       多模态图片门控
```

运行时仅 5 个第三方依赖（`openai`、`pyyaml`、`rich`、`prompt_toolkit`、`httpx`），纯 Python、模型无关，这是正确的工程取舍。

---

## 3. 亮点（值得保留并推广）

1. **事务化操作账本 `ledger.py`（含 `tools/base.py` 集成）** —— 写类工具执行前对目标做字节级快照，成功记凭证、异常自动回滚，journal 落盘可跨进程。集成细节到位（PreToolUse 改写参数后重算目标并重做快照）。这是项目真正的差异化能力，设计完整、实现正确。
2. **微内核事件总线 `kernel.py`** —— handler 逐个异常隔离、环形缓冲上限 + 溢出回调、锁内快照锁外派发避免递归 emit 与迭代器失效、通配符订阅。生产级稳健。
3. **重试/限流 `retry.py`** —— `RetryPolicy` 与 `RateLimiter` 职责互补（发送前节流 vs 被 429 后退避），`sleep/rng/clock` 全部可注入，便于单测；auth 错误立即抛不重试。
4. **OpenAI 兼容适配器 `openai_compat.py`** —— 懒加载 `openai/httpx`、`max_retries=0` 避免与业务层双重重试、prompt cache 标记复制对象不改原对象、流式 tool_calls 按 index 正确归并。
5. **MCP 桥接 `tools/mcp/`** —— stdio + JSON-RPC 2.0，独立事件循环线程，握手/关闭/孤儿进程回收完整；远端工具对 Agent 透明注册为 `mcp__<server>__<tool>`。
6. **i18n `i18n/__init__.py` + 10 locale** —— 懒加载、回退链 `当前→zh-CN→en→key`（永不抛异常）、经脚本比对 10 个 locale 各 100 键、与 en/zh-CN **双向零漂移**，质量极高。
7. **记忆 `memory/`、上下文压缩 `context/`、审计 `audit/`、cron/** —— 普遍具备异常隔离、原子写、`fsync`、优雅降级，记忆层 FTS5 trigram 对中文友好。
8. **Windows 进程存活探测**（`background_store.py` 用 `OpenProcess` 而非 `os.kill(pid,0)`，避开 NT 上 Ctrl+C 误发），显示踩过跨平台坑并正确规避。

---

## 4. 关键缺陷（按严重度排序）

### 🔴 P0 — 直接削弱核心卖点 / 安全

**4.1 `ext/crypto_engine.py` 密钥流复用（IV 未参与密钥流）—— 加密原语不可用**
`_xor_stream`（37–46 行）的密钥流为 `SHA256(key + counter)`，而 `seal()` 生成的随机 `iv` **从未传入** `_xor_stream`（仅传 `key`）。后果：
- 同一口令派生的 key 固定 → 每次加密密钥流完全相同 → 加密是确定性的（无语义安全）；
- **已知明文攻击**：获知任意等长明文即可 XOR 出密钥流，解密同 key 下其它密文（流密码"密钥流复用"经典灾难）；
- IV 形同虚设，文件头注释自称"CTR 风格替代 AES-GCM"名不副实（真正 CTR 必须 `nonce‖counter` 且 nonce 入密钥流）。
- 完整性默认缺失：`open()` 仅在调用方传入 `mac_b64` 时才校验 HMAC，否则无完整性保护，篡改可能静默产生乱码。

> 若该引擎用于保护真实凭据，必须改为"随机 nonce 真正并入密钥流"（`SHA256(key‖nonce‖counter)`），或直接用 `cryptography` 的 AES-GCM / ChaCha20-Poly1305。

**4.2 `ext/safety_engine.py` `is_redline` 与 `score` 覆盖面不一致 + 多类间接调用漏判**
- `is_redline`（162–167 行）被注释宣称为"致命操作红线单一来源"，但**只调用 4 个 token 化函数**（`has_recursive_rm/has_force_push/has_win_recursive_delete/has_system_shutdown`），**完全不 consult 丰富的 `CRITICAL_PATTERNS`**。
- 而 `CRITICAL_PATTERNS`（仅 `score()` 内使用，且未归一化）覆盖 `dd`、`mkfs`、`chmod -R 000`、`chown -R root`、`DROP/DELETE`、Windows `format` 等——这些在 `is_redline` 下**一律放行**。即"单一来源"的安全覆盖面远窄于 `score()`，直接违背设计声明。
- 间接写法绕过（即便在 `score()`/`is_redline` 共用的 token 判定中也存在）：
  - `sudo -u root rm -rf /`：`_strip_sudo` 只剥最前连续 `sudo`/`doas`，遇到 `-u` 停止 → 漏判；
  - `sh -c "rm -rf /"` / `bash -c` / `eval "rm -rf /"`：`parts[0]=="sh"` 而非 `rm` → 漏判（而 `has_force_push` 用位置无关判断，能命中 `sh -c "git push -f"`，两判定口径不一致正是根因）；
  - `rm $(echo -rf) /`：替换体被抽到 `extra` 段且 `echo` 不命中 → 漏判；
  - `export R=rm; $R -rf /`：`export` 不被识别为变量定义 → 漏判。
  - 嵌套 `$( )` 未被展开（`[^()]*` 正则）。

> 旗舰"安全优先"能力的可信度高度依赖此引擎。建议 `is_redline` 直接复用 `score()` 的 critical 判定，并对 `sh -c`/`bash -c`/`eval`/`python -c` 内容做递归展开。

**4.3 `core/background.py` `submit_background()` 把 detached 失败静默降级为 daemon 线程 + `submit_detached` 不校验 worker 存活**
`submit_background()`（394–406 行）确实优先调 `submit_detached()`（manifest + 独立 worker 进程 + Windows `DETACHED_PROCESS`，方向正确），但 `except Exception: return runner.submit(task).job_id` 把**任何** detached 失败都吞掉、降级为进程内 daemon 线程——该线程随 CLI 退出被强杀，任务跑不完，与"任务在 CLI 退出后仍能完成"的 docstring 直接矛盾。更隐蔽的是：`submit_detached` 在 `Popen` 成功后**不校验 worker 是否真的起来了**（如 `background_worker` 模块 import 失败会瞬间退出），manifest 会停在 `running` 而进程早已死，任务永远跑不完、CLI 还以为后台在干活。

**4.4 `models/anthropic.py` 流式 `tool_calls` 参数被污染（非法 JSON）**
`_stream()`（144–189 行）确实累加了 `calls` 并在末行 `return ModelResponse(..., tool_calls=calls, ...)` 传出，故"漏传 calls"并非问题；真正的 bug 在 `content_block_start`（179 行）：对 `tool_use` 块把 `tool_input` 预置为 `json.dumps(block.get("input", {}))`——而 Anthropic 在 start 时 `input` 恒为 `{}`，真正的参数靠后续 `input_json_delta.partial_json` 流式拼接。于是 `tool_input` 变成 `"{}"` 再追加 partial_json，得到 `{}{...}` 这种**非法 JSON**。`agent.run` 把 `tc.arguments` 直接喂给 `_execute_tools`，JSON 解析失败 → 流式 Claude 的工具参数被损坏/丢失，工具循环断裂。

### 🟠 P1 — 正确性 / 文档脱节

- **`cli/commands.py:398`** `cmd_run` 预算报告只读不存在的 `_cost_usd` 键 → 永远显示 `$0.00`（实际预算循环在 `agent.run` 内是用 `_estimate_total_cost` 正确停的，只是给用户看的报告错）。
- **`ext/verify_loop.py`** 模块 docstring 声称"写工具后自动触发（需 agent 主循环配合）"，但 `agent.run()` 中**无任何调用**——"auto" 事实未接入，目前只能手动 `/verify`。
- **两套模型目录漂移**：`router.MODEL_PRESETS`（15 个，路由/定价只基于它）vs `provider_catalog.ALL_PROVIDERS`（去重后 < 48 个，UI 用）。路由永远只能在 15 个里切；手动切到目录其它模型时 `estimate_cost` 用粗略默认价，成本统计失真。
- **`config/managed.py:62`** `from .defaults import DEFAULTS` —— `defaults.py` 中并不存在 `DEFAULTS`（真名为 `DEFAULT_CONFIG`），`ImportError` 被 `except` 吞掉 → `ManagedSettings` 默认基线变 `{}`，合并无内置兜底。属真实 bug。
- **`tools/base.py:180`** 事务回滚 `_MUTATION_EXTRACTORS` 只覆盖 5 个本地文件工具，**不含最危险的 `run_shell`/MCP/子代理**，"最小影响半径"对 shell 是空头支票。
- **`tools/base.py:196`** YOLO 模式下 self-improve 的 `learned` 护栏（"必须确认"）被 `decide().action=="allow" and (yolo or explicit)` 直接自动批准忽略 → 安全退化。
- **`ext/rules_engine.py`** `matches`/`regex_contains` 已坏（`args[0]` 是字符串却调 `.search` → 必抛 `AttributeError` 记为 violation）；算术 token（`+ - * /`）被接受却静默丢弃，规则语义被悄悄改变。
- **`ext/diff_engine.py`** `merge3()` 几乎完全失效（结果恒等于 ours，从不并入 theirs）；`patch()` 无视 `@@` 行号、无上下文校验，源不匹配也静默返回 `applied:True`；`diff()` 的 `changed` 恒为 True。
- **`ext/notify_engine.py`** macOS 分支把字符串传给 `shell=False` 的 `subprocess.run` → 通知根本不执行（`code=-1`）；即便改 `shell=True` 也未转义消息内容，存在 osascript 注入风险。
- **`ext/registry.py:58`** `engine_healthcheck` 调 `instance.list_methods()`，但 `RuleEngine` 无此方法 → 对 rules 永远返回 `ok:False`（selftest 误报）。
- **`ext/external.py:20`** `_INDEX_CACHE` 跨 workspace 共享，切换工作区后可能查到旧索引（stale）。

### 🟡 P2 — 可维护性 / 性能 / 次要

- **`cli/commands.py` 3057 行巨模块**，应仿 `ext_cli.py`/`improve_cli.py` 拆包；`tools/base.py` 的 `dispatch_result` ~145 行 God 函数；`skills/universal_format.py` 668 行单体。
- **`ui/repl.py:169`** 每轮渲染同步 `git rev-parse` + `git status --porcelain` → 高频开销，应缓存 + TTL。
- **`ui/fullscreen.py`** 多 worker 线程直接调 `invalidate()`（依赖实现细节），动画线程每 0.28s 无条件重绘浪费 CPU。
- **`skills/plugin_sdk.py`** 插件 hook / `HeadersHelper` 用 `shell=True` 执行插件提供命令（信任边界，应明示"插件=信任"或加白名单/沙箱）。
- **`audit/store.py:58`** 过度脱敏：`*key`/`*session` 会误伤 `monkey`/`turkey`/`session` 等非敏感字段。
- **`ext/skill_market_engine.py`（规划中，当前未实现/未注册）** 若落地，路径拼接 `out_dir = output / slug` 需补规范化校验，否则存在路径遍历隐患（中低，当前不适用）。
- 大量 `except Exception: pass/log.debug` 吞异常（`ipc_client.py:99`、`tools/base.py:251/272`、`index_engine.py:110` 等），降低可调式性。

---

## 5. 安全模型评估

项目安全取向整体**正确且克制**：
- 工具分发核心集中了 plan 模式拦截、权限策略、learned 护栏升级、事务回滚、只读缓存、审计事件，闭环完整；
- `permissions.py` 对齐 Claude Code 2.1.214 加固（域名白名单、shell 重定向 fail-closed、PowerShell `IEX/Set-ExecutionPolicy` 拦截、deny>ask>allow）；
- `hooks/manager.py` 强制 `command` 钩子用 `list(argv)` 拒绝裸字符串（防 shell 注入）、超时强杀、`blocking/allow_edit_args` 全局 fail-safe 开关；
- `vision/` 模型不支持视觉时绝不发图片字节（防静默失明）。

但**安全护栏存在"静默失效点"**（见 4.2/4.3/4.9/4.10/4.11），且 crypto 引擎的密码学缺陷会让"加密文件"这一安全特性名不副实。安全模型的方向对，但 flagship 实现需修到与声明一致才敢宣称"安全优先"。

---

## 6. 测试评估

- **广度好**：88 个测试文件、`tests/conftest.py` 提供 `MockOpenAIServer` 真实 HTTP 往返；端到端覆盖非流式/流式/流式工具分片、真实 `qxt` 子进程 headless、独立 `background_worker`、全屏 TUI。
- **环境隔离好**：`_isolated_environ` autouse 把 `QXT_HOME` 指向临时目录、清空 `*API_KEY*`、monkeypatch teardown 还原，根治本机凭据泄漏漂移。
- **CI 真实门禁**：第八轮把 `mypy ... || true` 假门禁改为真实（存量 151 错误修复 + 35 债务模块显式 `ignore_errors` 隔离），`mypy qingxiaotuan` 本地 exit 0。
- **缺口**：`retry.py`/`ledger.py` 明显为可单测而设计（注入 sleep/clock）但评审范围内未见对应单测文件；若干 bug（4.1–4.4、4.7–4.9）未被现有测试覆盖，说明测试集中在"正常路径 + 已修回归"，对**未接入路径与引擎内部逻辑**覆盖不足。

---

## 7. 优先修复清单（建议顺序）

1. **crypto_engine**：nonce 真正并入密钥流，或换 AEAD；`open()` 默认校验 HMAC。
2. **safety_engine**：`is_redline` 复用 `score()` critical 判定；递归展开 `sh -c`/`bash -c`/`eval`；修正 `sudo -u`/变量/命令替换绕过。
3. **background.py**：`submit_background` 改调 `submit_detached`。
4. **anthropic.py**：`_stream` 传回 `calls`。
5. **commands.py:398 / 173**：修正预算与成本读取键。
6. **verify_loop**：要么接入 agent 主循环，要么改文档为"仅手动"。
7. **config/managed.py:62**：`DEFAULT_CONFIG` 修正。
8. **tools/base.py**：把 `run_shell`/MCP 纳入回滚或显式声明不回滚；YOLO 下纳入 learned 红线。
9. **rules_engine / diff_engine / notify_engine / registry**：修复 `matches` 求值、算术解析、`merge3`、`patch` 校验、macOS 分支、rules `list_methods`。
10. **可维护性**：拆分 `commands.py`、抽 `BaseEngine` 消除 ~300 行 `handle/run` 重复、缓存 git 状态、跨 workspace 缓存键。

---

## 8. 总评

青小团是一个**"底层很能打、顶层有几处没打磨完"**的项目。微内核、事务账本、MCP、记忆、i18n、上下文压缩这些子系统证明作者具备扎实的工程能力，测试与 CI 门禁也到位。但它把自己定位为"安全优先的 AI Agent CLI"，而最被看见的两个能力——危险命令护栏与文件加密——目前都有可构造的绕过 / 密码学缺陷，这会直接反噬核心卖点。

**建议**：在对外发布与宣称“生产可用 / 安全优先”前，必须至少修掉第 7 节 P0 四项（crypto、safety、background、anthropic 流式），并补对应单测。修完后，它是一个在“最小影响半径 + 纯 Python 引擎 + 模型无关”上确有差异化的优秀 CLI。

---

## 9. 已修复记录（2026-08-28）

P0 四项已全部修复并补回归测试（另含第 1–2 轮会话已完成的 crypto / safety 加固）：

| # | 文件 | 修复 | 新增测试 |
|---|---|---|---|
| 4.1 | `ext/crypto_engine.py` | `_xor_stream` 的 IV 真正并入密钥流（`sha256(key‖nonce‖counter)`），同一明文+口令每次密文不同，杜绝已知明文破解；补域分隔符加固 | `tests/test_crypto_engine.py` |
| 4.2 | `ext/safety_engine.py` | `is_redline` 复用 `CRITICAL_PATTERNS` 全文（与 `score()` 的 critical 判定一致），并对 `sh -c`/`bash -c`/`sudo -u`/`eval`/`xargs`/`find -exec`/`python -c` 等间接写法做递归展开；`find -delete` 升为 critical | `tests/test_safety_redline_fix.py` |
| 4.3 | `core/background.py` | `submit_detached` 启动后轮询 manifest+Pid 存活，worker 没起来则标 `failed` 并抛错（不再记录 phantom `running`）；`submit_background` 仅在 `background.allow_thread_fallback=true` 时降级为进程内线程，否则让 detached 失败上浮 | `tests/test_background_detached.py` |
| 4.4 | `models/anthropic.py` | `content_block_start` 对 `tool_use` 块把 `tool_input` 初始化为 `""`（参数只来自 `input_json_delta` 拼接），修复 `{}{...}` 非法 JSON | `tests/test_anthropic_stream_toolcalls.py` |

修复后验证（45 passed / 1 skipped）：`chmod -R 000 /`、`mkfs`、`dd`、`DROP TABLE`、`sh -c "rm -rf /"`、`sudo -u alice rm -rf /`、`eval`、`xargs rm`、`find -exec rm -rf {}` 等全部被红线下拦截且零误报；同一明文加密密文唯一且错误口令被 HMAC 拒绝；流式 Claude 工具参数可正确还原为合法 JSON；detached worker 启动失败会被如实标 failed 而非静默挂 running。

## 10. 已修复记录（2026-08-29）

本轮在独立复评基础上，修复了自评未覆盖的真实缺陷，并扩充了安全覆盖面：

| # | 文件 | 修复 | 新增/改动测试 |
|---|---|---|---|
| D1 | `ext/safety_engine.py` `_expand_globs` | 改用引号感知分词器 `_tokenize_shell`，修复含空格路径（如项目目录名）被 `text.split()` 拆碎导致 glob 无法展开 | `tests/test_security_v2.py::TestGlobExpansion`（改为引号路径） |
| D2 | `ext/diff_engine.py` `merge3` | 旧实现先应用 theirs 再应用 ours 且第二段把 `offset` 重置为 0，两侧都有非冲突改动时错位；改为按 base 坐标升序单遍合并、offset 单调递增 | `tests/test_diff_engine.py`（新建，含双侧/交错/冲突用例） |
| D3 | `ext/diff_engine.py` `diff` | `changed` 字段旧实现用 `len(ndiff_lines) > 0`（ndiff 恒含未变行）导致恒为 True；改为按实际增删判定 | 同上 |
| D4 | `tools/shell.py` + `ext/safety_engine.py` | 护栏与 locale 解耦：新增结构化 `ctx.safety_severity`（`critical`/`high`/`medium`/`none`），测试改断言该字段而非本地化文案，CI 不再因非英文 locale 翻红；并修复 high 级在无确认通道时被错误硬拦（改为落到 step4 仅附建议，与设计「high 仅提示」一致） | `tests/test_shell_safety_guard.py`（断言改为 severity） |
| D5 | 安全两层级模型 | 新增 `is_hard_redline`（文件系统/OS 毁灭操作，永不自动执行）与 `is_redline`（综合危险检测器，仍供 MCP 守卫/脚本检查使用）分离；SQL 破坏性操作（DROP/DELETE/TRUNCATE）降为「可确认关键级」（极端 5 次确认，YOLO/无通道 fail-closed），打通此前不可达的确认放行路径 | `tests/test_shell_safety_guard.py::test_sql_destructive_is_confirmable_critical` 等 |
| D6 | `ext/safety_engine.py` 红线覆盖 | 新增 critical：`wipefs`/`shred`/分叉炸弹/`diskpart`/`cipher /w`/`takeown /f /r`/`bcdedit`/`reg delete`/裸盘重定向/卷分区销毁；新增 high：`crontab -r`/`truncate -s 0`/`systemctl mask`；`_normalize` 增加对 ANSI-C 引号 `$'...'`/`$"...` 的剥壳（对抗 `$'rm -rf /'` 混淆） | `tests/test_shell_safety_guard.py::test_redline_covers_new_critical_patterns` / `test_redline_covers_ansi_c_quoting` / `test_high_new_patterns_advice_only` |

**修复前状态**：本环境跑回归测试曾有 10 个失败——其中 8 个来自 `test_shell_safety_guard`（护栏功能正常但测试断言英文 `"critical"` 子串，而拦截文案是中文；另有 high 级被错误硬拦）；2 个来自 `test_security_v2`（`test_symlink_to_root_resolves` 的 Windows 语义差异、`test_glob_expands_existing_files` 命中 D1 的空格路径 bug）。**修复后**：相关 67 个用例全部通过（1 个因本平台软链限制跳过）。

**残留/已知局限**：静态红线分析对极端对抗性混淆（如嵌套 ANSI-C 转义、多字节同形字组合）仍非绝对；`_expand_globs` 仅对引号包裹或不含空格的 glob 可靠，未加引号的含空格路径在 shell 语义下本身无法构成单一 token。护栏定位仍是纵深防御，须经人工复核。

## 11. 已修复记录（2026-08-29 续）

本轮在 §4 复核基础上，逐条比对源码后确认：§4 中标注的 `config/managed.py`（`DEFAULT_CONFIG`）、`rules_engine`（`matches`/`regex_contains`）、`registry`（`list_methods` 兜底）、`commands` 预算键（`_cost_usd`）等项**已于此前回合修复**，源码已正确，故本轮不再重复改动。真正仍存在的缺陷已修复并补回归测试：

| # | 文件 | 修复 | 新增/改动测试 |
|---|---|---|---|
| E1 | `ext/diff_engine.py` `patch` | 旧实现假设上下文行与源一致、源不匹配也静默 `applied: True`（与"最小影响半径"相悖）；现逐行校验上下文，不匹配或源过短则 fail-closed 返回 `applied: False` 并给出精确行号错误 | `tests/test_diff_engine.py`（`test_patch_applies_matching_context` / `test_patch_rejects_context_mismatch` / `test_patch_rejects_short_source` / `test_patch_roundtrip_via_diff`） |
| E2 | `ext/notify_engine.py` macOS 分支 | `osascript` AppleScript 字符串补齐反斜杠与双引号转义（调用本身已是 `shell=False` 的 list argv），关闭字符串注入/截断 | `tests/test_cold_engines.py::test_notify_macos_escapes_quotes` |
| E3 | `tools/external.py` 索引缓存 | `_INDEX_CACHE` 由单一全局 `"data"` 槽改为按 `ctx.workspace` 隔离，切换工作区不再查到旧索引（stale） | `tests/test_external_engines.py::test_index_cache_scoped_by_workspace` |
| E4 | `tools/cache.py` + `tools/base.py` 工具结果缓存 | `ToolResultCache` 键纳入 `ctx.workspace`，所有可缓存只读工具（`read_file`/`search_files`/`ext_index_query`/…）不再跨工作区返回陈旧缓存 | `tests/test_tool_cache.py`（新建） |
| E5 | `tests/test_modes_and_tools.py` | `FakeClient` 补 `security_policy` 桩，对齐真实 `MCPClient` 契约（修复前该测试因 `AttributeError` 失败，属测试/代码漂移，与本轮其它改动无关） | — |

**验证**：受影响文件及 dispatch/缓存相关用例全绿（约 146 项中 145 通过 + E5 修复后全绿）；`ext_diff` / `ext_patch` / `ext_index_*` / 工具缓存路径均经回归。

**残留/已知局限（本轮仍存）**：
- `rules_engine` 表达式求值器对算术 token（`+ - * /`）仅分词、未实现中缀运算，含算术的规则（如 `len(x) > 5 + 3`）会静默丢弃多余 token；属 P2 轻微项，未改动以免引入回归。
- 全量测试套件（1019 项）在 Windows 本机下 `pytest` 临时目录清理阶段偶发 `PermissionError`（`E:\Temp\pytest-of-28726` 软链无权限），系 pytest 平台层问题而非项目缺陷；以项目内 `--basetemp` 运行可规避，CI 不受影响。
- 工具结果缓存仍有 TTL（默认 60s）内的同工作区时效窗口，属设计权衡，非缺陷。

## 12. 已修复记录（2026-08-29 续二：四项专项）

本轮按用户四项指令执行：**清理 .env* 配置 / 打通核心链路并降误杀 / 新增版本策略 FAQ / openai 懒加载与依赖边界**。逐条比对源码后落地的改动如下：

| # | 文件 | 改动 | 验证 |
|---|---|---|---|
| F1 | 仓库根 `.env`（删除）、`.gitignore`、`.env.example` | 删除无引用的全注释占位 `.env`（代码实际加载 `~/.qingxiaotuan/.env`）；`.env.example` 补全 `OPENAI_API_KEY`、`QXT_HOME`，统一 `<PROVIDER>_API_KEY` + `QXT_API_KEY` 回退命名；确认 `.gitignore` 忽略 `.env*` 但 `!.env.example` 进版本库 | 仓库内除 `.pytest_tmp`（已 gitignore）与 `.env.example` 外无 `.env*`；`OPENAI_API_KEY` 与代码 `api_key_env` 读取一致 |
| F2 | `ext/safety_engine.py` `score()`、`core/whitelist.py` `get_warning_level()` | 接入 `is_benign_dev_command`：文件读写 / git 常规操作 / 包管理 / 测试运行 / lint 等良性命令判定为 `none`/`0`，不再被升级或反复确认；系统路径写入（`/etc`/`/usr/lib`/`/dev`）与不可逆操作（`rm -rf`、force push、`dd`、`mkfs`）被排除出良性集合，仍由硬红线拦截 | `tests/test_safety_benign.py`（新建，16 例） |
| F3 | `tests/test_e2e_mock_server.py` | 扩展为 provider-agnostic 端到端：驱动 `run_shell` 走完 `chat → 工具 → 安全评估` 全链路（良性 `echo` 穿透护栏）；新增 5xx 重试用例（mock 前 2 次 503 后 200，`RetryPolicy` 退避重试成功） | 与既有 e2e 用例同文件，新增 2 例 |
| F4 | `models/openai_compat.py`、`pyproject.toml`、`README.md`/`README_zh-CN.md` | `openai` 仅在真正发请求时延迟导入；`pyproject` 将其移入可选 `openai` extra；README「依赖边界」说明 openai 仅作 HTTP 传输层、核心逻辑不依赖、可替换、不装也能跑非 OpenAI 场景；入口 `qingxiaotuan.cli:main` → `cli/__init__.py` → `parser.main` 经测试确认可解析（用户已说明此结构反直觉但正确，无需改代码） | `tests/test_entrypoint_and_lazy_openai.py`（新建） |
| F5 | `VERSION_FAQ.md`、`SUPPORTED_MODELS.md`、`README*` | 新增版本策略 FAQ（0.x 语义、发版时机、破坏性变更处理）与 Provider/模型实测兼容矩阵；两文档均在 README 中英双语插入链接 | — |

**核心链路验证结论**：Agent 韧性层（`RetryPolicy` 指数退避+抖动、`RateLimiter` 令牌桶、`CircuitBreaker`）与 `OpenAICompatAdapter`（`classify_error` 错误分类、httpx 连接/读超时、`max_retries=0` 交由 Agent 层统一控制）已具备完整超时与重试；本轮以离线 mock 端点固化「任意 OpenAI 兼容 Provider 传输层 + 安全降误杀 + 5xx 重试」全链路。各具体模型的工具调用质量属模型能力范畴，需用户自带 Key 经 `qxt /model` 连通性测试实跑（见 `SUPPORTED_MODELS.md`）。

**回归**：benign/entrypoint/e2e 16 例全过；既有 `test_shell_safety_guard`/`test_security_v2`/`test_new_modules`/`test_run_shell_impact`/`test_safety_redline_fix`/`test_safety_selfimprove` 98 例通过、1 跳过，无回归。

**残留/已知局限（本轮仍存）**：
- 全量 1019 项套件在 Windows 本机下 `pytest` 临时目录清理阶段偶发 `PermissionError`（`E:\Temp\pytest-of-28726`），系 pytest 平台层问题；项目内 `--basetemp` 运行可规避，CI 不受影响。
- `rules_engine` 算术 token 中缀运算仍为 P2 轻微项（见 §11），本轮未动以免引入回归。
