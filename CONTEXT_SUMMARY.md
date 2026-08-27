# Context Summary

更新时间：2026-08-27 (最新)

## 第十三轮: Safety 引擎模式扩充 + PyPI 发布准备 + 推广文案

- **Safety 引擎模式扩充** (`ext/safety_engine.py`): CRITICAL 新增 6 项 — 系统关机/重启 (`shutdown/halt/poweroff/reboot/init 0|6/systemctl poweroff|reboot|halt`)、递归权限全灭 (`chmod -R 000+/`)、递归 root 所有权 (`chown -R root /`); HIGH 新增 6 项 — `git clean -f`、`git checkout -- .`、`docker rm -f`、`docker rmi -f`、`kubectl delete`、`iptables -F`/`ufw disable`; MEDIUM 新增 5 项 — `systemctl stop/disable`、`service stop`、`pkill`、`killall`、`chmod 000`、`chown root`。
- **`is_redline` 红线扩展**: 新增 `has_system_shutdown()` 函数, 覆盖 shutdown/halt/poweroff/reboot/init 0|6/systemctl poweroff|reboot|halt — YOLO 模式下也硬拦截, 穿透子壳/变量/引号间接写法。
- **测试**: `tests/test_shell_safety_guard.py` 新增 13 项 (shutdown/reboot 集合、chmod -R 000 变体、docker rm/rmi、git clean/checkout、kubectl delete、iptables -F、systemctl stop、pkill/killall、chmod 000、归一化穿透 shutdown 间接写法); 全 30 项通过。
- **PyPI 发布配置** (`pyproject.toml`): 新增 `keywords`(11 个 SEO 关键词)、`classifiers`(PyPI 分类)、`project.urls`(首页/仓库/Changelog/Bug Tracker)、英文 `description`; 新建 `MANIFEST.in` 确保 sdist 打包 LICENSE/README/resources; 新建 `.github/workflows/publish.yml` (tag push 自动发布到 PyPI, trusted publishing)。
- **推广文案** (`outputs/promotional_posts.md`): 3 篇帖子 — Hacker News Show HN (技术极客风, 突出 safety engine token 穿透)、Reddit r/Python (社区友好, 功能列表 + 架构说明)、V2EX (中文开发者社区, 对比差异化)。
- **验收**: safety 引擎测试 30/30 通过; 关联测试 (safety_selfimprove + cold_engines + auto_route + parser + kernel) 64/64 通过。

### 后续补充 (同轮)

- **`/impact` 增强** (`cli/commands.py`): 新增文件类型分布 (按扩展名统计 + 柱状图)、工具使用分布 (按工具名统计 + 柱状图)、可视化影响半径报告。
- **`/log` 新命令** (`cli/commands.py`): 展示最近 N 条工具调用历史 (默认 15), 从 session 事件流读取 `tool_call` 事件, 显示时间戳/工具名/参数摘要; `_HELP` 已同步。
- **验收**: 全部 42 项通过 (shell_safety_guard 30 + auto_route 7 + kernel 5)。

## 第十二轮: 代码卫生 + 客户端限流 + 测试补齐

- **代码卫生** (`core/agent.py` / `cli/commands.py` / `ext/skill_market_engine.py`): 删除未使用导入 (`system_prompt_hash`/`PARALLEL_SAFE_TOOLS`); 配置目录引用统一走 `home_dir()` (尊重 `QXT_HOME`) — `cli/commands.py` 硬编码 `~/.qingxiaotuan` 与技能市场目录均改为优先 `QXT_HOME`。
- **异常可观测** (`core/background.py` / `core/ipc_client.py` / `core/ledger.py` / `hooks/manager.py` / `config/loader.py`): 高风险静默吞异常补 debug/warning 日志 (会话流读取失败、进程清理、账本操作、钩子执行、配置操作); 逐行解析/回退默认值等低风险 continue 模式保持原样。
- **客户端限流 `RateLimiter`** (`core/retry.py`): 令牌桶 (每分钟请求数) + 并发信号量, 发送前主动节流 — 与 `RetryPolicy` (被 429 打回来再退避) 互补, 保护免费层端点 (OpenCode Zen: 1 req/s, 10/min)。`sleep`/`clock` 可注入, 测试无需真等待; `from_config` 按 `model.rate_limit` 构造, 默认关闭 (opt-in)。
- **限流接入 Agent** (`core/agent.py`): `_chat_with_retry` 外包 `acquire()`/`release()`, 与重试策略协同; `config/defaults.py` 的 `model.rate_limit.enabled=False` 默认关闭, 付费模型不受影响。
- **测试**: 新增 `tests/test_rate_limiter.py` (11 项: 令牌桶突发/回填/封顶、并发信号量阻塞与释放、配置解析、与 RetryPolicy 协同) + `tests/test_parser.py` (19 项: 子命令 func 注册、嵌套子命令必填、参数解析、`_resolve_func` 模块路由、main 分发/非 TTY 拦截); 适配 `test_opencode_zen_config.py` 两个用 `Agent.__new__` 的最小 Agent 测试补 `_rate_limiter`。
- **文档**: CHANGELOG.md [0.2.0] 补限流/代码卫生/日志三条; TASK_QUEUE.md 记录本轮。
- **验收**: 完整套件全绿 (修复前 624 passed + 2 failed → 修复后全过)。

## 第十一轮: 启动提速 + 彻底无高亮 + Kimi Code 唯一强调色

- **启动提速** (`models/__init__.py` / `models/openai_compat.py` / `cli/parser.py`): 重 SDK 延迟导入 — `AnthropicAdapter` 改在 `create_adapter` 内按需导入; `openai`/`httpx` 改在 `client` 属性首次发请求时导入; 命令函数改字符串注册 + `_resolve_func` 延迟导入 (轻量命令 `--version/--help` 不加载 app 链)。实测 `qxt --help` 启动从 ~4.3s 降到 ~1.7s, import 模块数 1607 → 664。
- **彻底清除残留高亮** (`cli/commands.py` / `ext_cli.py` / `improve_cli.py`): 上一轮清理后 commands.py 曾从备份恢复, 把 215 处 Rich 标记 + KIMI_* 颜色常量带了回来; 本轮重新剥离全部标记 (215+14+13 处), 删除 KIMI_* 常量定义与引用, 修复动态样式 `[{'green' if ok else 'red'}]` 残留。全局扫描确认包内零 Rich 标记 / 零 KIMI_* / 零 ANSI 转义 (仅 `ext/ansi_engine.py` 作为 ANSI 文本处理工具引擎按需产生转义, 属功能保留)。
- **Kimi Code 唯一强调色** (`ui/theme.py`): 删除 12 个未用颜色常量, 只留 `ACCENT_COLOR = "#4FA8FF"`; `blank_pt_style()` 修正 — 标题/徽章/提示符 (`title`/`badge`/`badge-plan`/`badge-yolo`/`prompt`/`text-area.prompt`) 保留浅蓝, 其余 (panel/log/input/bottom-toolbar/补全菜单/滚动条等) 全部显式重置为无样式。此前 title/badge 也被一并重置, 浅蓝从未真正生效。
- **提示符浅蓝** (`ui/repl.py` / `ui/fullscreen.py`): repl 提示符 `│ > ` 经 `FormattedText([("class:prompt", ...)])` 应用浅蓝; fullscreen TextArea 提示符经内置 `class:text-area.prompt` 应用浅蓝 (TextArea 无 `prompt_style` 参数, 改用样式类)。
- **实测**: `qxt --help` / `config validate` / `ext engines` / `model list` 输出全部纯文本; 合并样式解析确认 title/badge/prompt 为 #4FA8FF、panel/completion-menu 等为 default 无颜色无加粗。
- **文档**: README.md / README_zh-CN.md 更新模块数 (119) / 行数 (~21k) / 测试数 (591), 新增纯文本输出与冷启动两条特性; CHANGELOG.md [0.2.0] 补三条; TASK_QUEUE.md 记录本轮。
- **验收**: 完整套件 **591 passed, 1 skipped** (环境相关); 清理一次性脚本与过期备份 (`_strip_markup*.py` / `_*_backup.py`)。

## 第十轮: 审计导出与统计修正 + ansi 显示宽度 (冷门模块第二轮)

- **audit 查询去重** (`audit/store.py` `query()`): 缓冲与落盘合并改为 seq 集合一次性判重 —— 此前逐条 `any(b.seq == ev.seq ...)` 是 O(n²) 且在缓冲+磁盘重叠时同一事件会重复计入; 新增 `AuditQuery.limit` (>0 返回按 ts 排序后最早的前 N 条)。
- **audit 统计诚实化** (`stats()`): 此前只统计内存环形缓冲, 落盘后 (尤其超 2000 条被挤出缓冲或跨进程) 数字失真; 现合并落盘全量 (同样按 seq 去重), 新增 `oldest_iso`/`newest_iso` 时间范围字段。
- **audit 导出** (`export(path, fmt, query)`): 支持 jsonl (一行一条) / json (数组) / csv (表头 `seq,ts,iso,type,payload_json`) 三格式; 只写脱敏后的 payload, raw 原文绝不落盘; 未知格式抛 ValueError。
- **ansi 显示宽度** (`ext/ansi_engine.py` v1.1.0): 新增 CJK 感知计宽内核 (`unicodedata.east_asian_width` W/F 记 2、组合字符记 0、先剥转义序列) 与三方法 — `width` (显示列数)、`truncate` (按显示宽度截断, marker 预留宽度, 预算为负返回空串)、`pad` (left/right/center 补齐到显示宽度, 超宽原样返回)。
- **ansi 真 render**: 原实现是假渲染 (原样返回); 现支持 SGR — fg 调色板 16 色 (black..white → 30-37, bright_* → 90-97) + bold, 输出 `\x1b[...m...\x1b[0m`; 未提供样式时保持旧版原样返回 (向后兼容, `strip` 不受影响)。
- **测试**: `tests/test_audit.py` 追加 6 项 (缓冲+磁盘去重回归含 close 后新实例重读、limit 取最早 N 条、clear_buffer 后 stats.total 仍算落盘、三格式导出全程无明文密钥、非法 fmt 抛错); `tests/test_cold_engines.py` 追加 ansi 节 5 项 (CJK 宽度/色码剥离等价/CJK 边界截断与 marker 预算/三种对齐/SGR 渲染与裸渲染兼容/handle 信封)。`ExternalEngineManager` 真实 JSONL 子进程冒烟 width(青小团abc)=9、truncate→"青小团…"(7)、pad 右对齐、render=`\x1b[1;31merr\x1b[0m` 全通过。
- 回归: `test_audit + test_cold_engines + test_external_engines` = **49 passed, 0 failed**; `mypy qingxiaotuan` exit 0 (119 文件)。边界: 未触碰并发会话占用的 commands.py/parser.py/provider_catalog.py/i18n locales 及其测试。

## 第九轮: 冷门引擎实用性增强 (ext 四引擎)

- **search** (`ext/search_engine.py` v1.1.0): 新增 `include_globs`/`exclude_globs` 相对路径 glob 过滤、`ignore_case`、`context` 上下文行 (0-10, 每条命中带前后文与行号)、结果截断标记 `truncated`; 新方法 `count` 按文件统计匹配数 (不返回内容行, 快速概览代码分布, `max_files` 封顶)。
- **index** (`ext/index_engine.py` v1.1.0): 符号提取补盲区 — Python 支持缩进 def (类方法) 与 `async def`, 新增 Go (`func`/`type struct|interface`)、Rust (`fn`/`struct|enum|trait`)、Java (`class|interface|enum`) 提取; 每个符号带 `kind` 与行号 (`symbol_details: [{name,kind,line}]` 可直接定位跳转), 原 `symbols` 名字列表保持向后兼容。
- **notify** (`ext/notify_engine.py` v1.1.0): Windows Toast 失败自动回退 WinForms 气球通知并在结果中标注 `fallback_used`/`first_error` 诊断; 新方法 `beep` 终端提示音 (Windows winsound / 其他平台 stderr 铃符, 不污染 stdout IPC 协议); `notify`/`beep` 均支持 `dry_run` 只报告将使用的后端不实际执行; 三平台发送统一走 `_run_cmd` 带 stderr 片段诊断。
- **crypto** (`ext/crypto_engine.py` v1.1.0): 新方法 `hmac_sign`/`hmac_verify` (HMAC-SHA256, key_b64 或 passphrase+salt 派生, 恒定时间比较)、`random_token` (secrets URL 安全令牌); 加固 — `iterations` 强制正整数且 ≥1000 (防弱化配置), `open()` 无 MAC 且密码错误时给出 "非有效 UTF-8" 明确报错而非 UnicodeDecodeError 裸异常。
- **测试**: 新增 `tests/test_cold_engines.py` 19 项 (直接实例化不经 IPC 子进程; notify 回退链用 monkeypatch 脚本化 `_run_cmd`, UTF-8 容错用引擎内部函数反向生成必然非法字节, 全确定性无弹窗); 另经 `ExternalEngineManager` 真实 JSONL 子进程冒烟四引擎新方法全部通过。回归: `test_cold_engines + test_external_engines + test_json_engine` = **36 passed, 0 failed**; `mypy qingxiaotuan` exit 0 (119 文件)。
- 边界: 本轮全部改动位于 `qingxiaotuan/ext/*` 与新增测试文件, 未触碰并发会话占用的 commands.py/parser.py/provider_catalog.py/i18n locales 及其测试。

## 第八轮: 路由失败保险补强 + 测试环境隔离 + mypy 真门禁

- **路由二次失败保险** (`models/router.py` `decide()`): 新增校验「切换目标必须 ∈ available_providers」。此前 `select_model()` 候选为空时会回退默认模型 (如 deepseek/deepseek-chat), 该默认可能不在已配置密钥的供应商列表内 —— 例如仅配置 ARK_API_KEY 时, 难度 8 的任务会把 doubao 切到无密钥的 deepseek, 请求打到无凭证端点后重试三次炸掉。回归测试: `tests/test_router.py::test_decide_never_switches_outside_available_providers`。
- **conftest 全局环境隔离** (`tests/conftest.py` `_isolated_environ` autouse): QXT_HOME 默认指向临时目录 (不再读真实 `~/.qingxiaotuan/.env`, 杜绝本机凭据泄漏进测试进程), 清空所有 `*API_KEY*` 变量, monkeypatch teardown 自动还原 —— 本机测试与 CI 行为一致。此前曾因真实凭据泄漏导致全量 13 failed (单独跑却绿) 的机器相关漂移就此根治。
- **mypy 门禁真实化**: CI 原 `mypy ... || true` 是假门禁。实测存量 151 错误 → 修复 `models/router.py` 全部 6 处 (float 标注/Optional 迭代/TYPE_CHECKING 导入 ToolContext/str 包裹 Any 返回) 与 `i18n/__init__.py` 3 处; 其余 35 个债务模块在 `[tool.mypy.overrides]` 逐模块 `ignore_errors = true` 显式隔离 (warn_unused_configs 保证名单不腐烂)。现 `mypy qingxiaotuan` 本地 exit 0, CI 步骤去掉 `|| true` 后门禁真实生效。
- **仓库卫生**: 一次性工具链 `_fetch_models.py`/`_gen_model_lists.py`/三个模型 JSON/`fix_install.ps1`/`regression.log` 移入 `.scratch/` (已被 gitignore), 根目录只剩发布物。
- **并发会话提示**: 本轮期间另一会话在持续改造 `/model` 交互选择器 (`_pick_model_interactive` 改为返回 `(provider, model)` 元组, commands.py/provider_catalog.py 于 11:53~11:59 更新)。其关联测试 (`test_model_catalog`/`test_cli_simplify`/`test_bench_config_session`) 的红绿状态属该会话进行中工作, 以其收敛结果为准。

## 第七轮及之前 (节选)


- `qingxiaotuan/i18n/` 包: 10 locale 模块 (zh-CN/zh-TW/en/ja/ko/es/pt-BR/fr/de/ru) × 90 键全部对齐 (与 en 集合零差); `t(key, **kw)` 回退链 当前→zh-CN→en→key 本身, 缺译不抛异常; 懒加载 (`_locale_module` import 失败缓存 None); `normalize_language` 别名归一 (`zh_CN`/`chs`/`zh-hans`→zh-CN、`jp`→ja、`pt-br`→pt-BR、BCP-47 主子标签兜底)。
- `ensure_language(config)`: 已配置直接应用; 未配置且双向 TTY → `choose_language_interactive()` 编号菜单 (标题四语并排硬编码, 选定前不能 t()) 并写盘 `language`; 非交互 → zh-CN 绝不阻塞。
- 接线: `cmd_chat` 在 build_kernel 后调 ensure_language; `cmd_setup` 向导开头即选语言 (安装即选), 全部文案走 setup.* 键; REPL 横幅/状态栏/bottom toolbar/fold 提示/快捷键面板 (`_KEY_GROUPS` 类属性改为 `_key_groups()` 方法, desc 走 t(), 键名不翻); 全屏 TUI 全部状态/提示/侧栏文案走 fs.*/tui.* 键。
- Agent 回复语言: `build_system_prompt(..., reply_language="zh-CN")` 末位参数, 准则第 7 条由 `_reply_rule()` 生成 —— zh-CN 保持历史原文逐字节不变 (prompt cache 友好), 其他语言注入 "Always reply in {english} ({native})"; `agent._build_system` 从 config 读 language 传入。
- defaults.py 顶层新增 `"language": ""`; zh_CN 值与既有硬编码逐字一致, 既有中文断言不受影响。
- 测试: `tests/test_i18n.py` 33 项 (十语言键对齐/插值/回退链 pop 还原法/别名参数化/set_language 无效回落/ensure_language 三分支 stub/choose 菜单 monkeypatch/system prompt 注入)。适配: `test_ui_folding.py` 的 `_KEY_GROUPS` → `_key_groups()`。
- 收尾修复: `commands.py` `/tools` 分支循环变量 `t` → `tool` (遮蔽 i18n `t()` 导致 UnboundLocalError); `repl.py` 横幅标签改为 `(label + ':').ljust(w + 1)` 冒号紧跟标签再对齐。全量回归 **484 passed, 1 skipped** (206s)。
- 边界: 深层引擎日志、工具原始输出、`_pick_model_interactive` 等次级交互仍为中文; 其余 slash 命令输出未全量 t() 化 (后续轮次)。

## 新增: README 十语言国际化

- 主 `README.md` 重写为英文 (全球流量入口), 定位语从"融合开源项目二次开发"重构为差异化叙事 (最小影响半径 + 吉祥物状态机 + 纯 Python 引擎), 融合故事移至文末致谢节。
- 10 语言全套: `README.md`(en) / `README_zh-CN.md` / `README_zh-TW.md` / `README_ja.md` / `README_ko.md` / `README_es.md` / `README_pt-BR.md` / `README_fr.md` / `README_de.md` / `README_ru.md`, 每份顶部有语言导航条 (当前语言粗体无链接)。
- 全部内容以真实代码为背书: 20 斜杠命令、10 引擎、409 离线测试、5 依赖、crypto 如实描述 (PBKDF2 + SHA256-keystream 流式加密, 非 AES); 各翻译文件经代码块逐字节比对与违禁词扫描验证。
- 待办占位: 徽章/clone URL 的 `YOUR_ORG` 已于第六轮全局替换为 `Qingnai-Technology-kino-koki/Qingxiaotuan-Agent-CLI`; demo GIF 占位注释仍待录屏填充。

## 当前项目

青小团 CLI (Qingxiaotuan Agent CLI) — 纯 Python 全栈版本

## 完整测试套件

`pytest tests/ -q` → **484 passed, 1 skipped** (环境相关, 第七轮 i18n 收官后); 此前 web_fetch 的 6 个失败 (另一会话 `tools/web.py` 的 SSRF 检查 WIP) 已被其修复, 现全套件零失败。

## 新增: /route 智能模型路由建议 (咨询式)

- `/route <任务描述>` — 调用 `models/router.py` 的 `ModelRouter.route()` 估算任务难度 (1~10) 并建议 provider/model, 展示理由与预估成本; 只建议不切换 (`router.enabled` 自动接线尚未启用)。
- 无参数时显示用法提示; `_HELP` 帮助文本已同步。
- 测试: `tests/test_slash_compact_cost.py` 新增 2 项 (用法提示 + 建议输出)。

## 新增: /compact 与 /cost 斜杠命令 (对标 Claude Code)

- `/compact` — 手动上下文压缩: `ContextManager.compact_force()` (忽略预算阈值折叠旧历史) + `Agent.compact()` + 斜杠接线。
- `/cost` — 成本估算: `router.estimate_cost()` 按内置定价表估算; 展示 prompt/completion token、缓存命中/未命中与命中率、估算费用。
- 合并了重复的 `/context` 分支, 更新 `/help` 文本。
- 测试: `tests/test_slash_compact_cost.py` (6 项) + `test_context.py` 新增 compact_force 测试。

## 修复: IpcClient 线程泄漏

- `IpcClient.close()` 现在会终止子进程、关闭管道并 join `_read_loop` 线程, 不再残留跨测试的 IPC 句柄/线程。
- `test_fullscreen_tui_close_leaves_no_threads` 改为断言"无新增线程" (更符合 M2 验收语义, 不受其他组件线程退出影响)。
- 新增 `test_ipc_client_close_reclaims_read_thread` 回归测试。

## M8 发布质量完成 (286 passed)

完整测试套件 `pytest tests/ -q` → **286 passed, 1 skipped** (环境相关)。

新增端到端测试 (全部离线, 不访问外网):
- `tests/test_e2e_mock_server.py` — 本地 OpenAI 兼容 mock 端点, 真实 HTTP 往返驱动 Agent 工具循环 (非流式/流式/流式工具调用分片累积)。
- `tests/test_cli_e2e.py` — 真实 `qxt` 子进程连 mock server 跑 headless 任务 (非流式 + 流式)。
- `tests/test_worker_e2e.py` — 真实 `background_worker` 独立进程跑任务到 done / 模型错误快速失败。
- `tests/test_fullscreen_tui.py` — 扩展到 9 项 (状态栏 token/context、日志上限、焦点循环、状态钳制)。

共享设施: `tests/conftest.py` 提供 `MockOpenAIServer` + `mock_server` fixture。

CI 质量门禁: `.github/workflows/ci.yml` — py_compile + pytest (Python 3.11/3.12/3.13)。

修复: worker 对持续模型错误空转到 max_turns 的缺陷, 改为快速失败标记 failed。

## Python 全栈改造完成

已将原先的 C + TypeScript 外部引擎全部替换为纯 Python 实现：
- `qingxiaotuan/ext/` 包含 10 个引擎: diff, crypto, index, ansi, safety, json, search, notify, rules, skill-market
- `qingxiaotuan/ext/registry.py` 引擎注册中心 + 健康检查
- `qingxiaotuan/core/ipc_client.py` 重写: 包含 `ExternalEngineManager` 和 `IpcError`
- `qingxiaotuan/tools/external.py` 重写: 使用正确的 `Tool(handler=...)` 接口注册 17 个外部工具
- 版本升级到 0.2.0 (依赖保持 openai/pyyaml/rich/prompt_toolkit/httpx; crypto_engine 纯标准库实现)

### 验证结果 (全部通过)
```
Tool OK
ipc_client OK
external OK
builtin_tool_plugins OK: 11
ENGINES: ['diff', 'crypto', 'index', 'ansi', 'safety', 'json', 'search', 'notify', 'rules', 'skill-market']
  diff: OK
  crypto: OK
  index: OK
  ansi: OK
  safety: OK
  json: OK
  search: OK
  notify: OK
  rules: OK
  skill-market: OK
ALL DONE
```

## TUI 界面

已按用户要求采用 DeepSeek 风格双层框终端工作台 (`qingxiaotuan/ui/repl.py`):
- 顶部双线欢迎框 (╭─╮ + ▐█▛█▛█▌ logo + 目录/会话/模型/版本)
- 中间提示行 (✦ Try ...)
- 底部双线输入框 (╭─╮ > 用户输入 ╰─╯)
- 最底部状态栏: 目录 分支 | 快捷键提示 | context%
- 已实测: `qxt` 启动后界面正常渲染

## TUI 风格统一 (已完成)

新增共享主题模块 `qingxiaotuan/ui/theme.py` (hex 色为唯一事实来源):
- `C` -> Rich 颜色名 (repl.py 用), `PT_STYLE` -> prompt_toolkit 样式表 (fullscreen.py 用)
- `MASCOT_ICONS` 状态图标、`context_bar()` 占用条、`context_style()` 语义级 (ok/warn/err)
- repl.py 与 fullscreen.py 均已改为从 theme 导入, 不再各自维护配色

全屏版新增实时 token/context 面板:
- `set_context_info(estimated, budget)` / `context_bar(info)` (与 repl 同签名)
- 侧栏渲染 `上下文 62% [████░░░░]` + `tok 6,200 / 10,000`, 高占用变红

## 下一步

1. ~~运行完整测试套件~~ 已完成: **409 passed, 1 skipped**, 零失败 (web_fetch 已由并发会话修复)
2. 继续 M1~M3 收尾验证

## 第六轮: 发行准备包 (进行中)

- **事实修正**: TASK_QUEUE.md 三处错误 — "8 个引擎"→10 (rules/skill_market 补入), 删除虚构的 "pyproject.toml 添加 cryptography>=42.0" (实际依赖纯 Python 五件套: openai/pyyaml/rich/prompt_toolkit/httpx), 测试计数 362/368→409; REPO_LAUNCH_GUIDE.md 状态栏 215 passed / 3 failed → 409 passed / 1 skipped。
- **CONTRIBUTING.md**: venv 激活命令修复 (`.venv/Scripts/activate` 缺 `source`); 架构速览对齐现实 — 补 ext/(10 引擎)/skills/self_improve/cron/audit/context, models 补 router/provider_catalog/anthropic, ui 补全屏 TUI+吉祥物+theme; 开头定位去"超越式自研"浮夸措辞。
- **scripts/push.sh 安全修复**: 移除 `http.sslVerify=false` (完全关闭 TLS 证书校验, 中间人风险); schannel 吊销绕过改为推送失败后的提示而非默认注入; origin 设置改幂等 (get-url 判断, 不再破坏性 remove/add)。
- **社区文件**: 新增 SECURITY.md (私密漏洞报告渠道 + 已知边界如实声明) 与 `.github/ISSUE_TEMPLATE/{bug_report,feature_request}.yml` 表单; CODE_OF_CONDUCT.md 执行节补举报渠道 (@Qingnai-Technology-kino-koki)。
- **CHANGELOG.md**: Keep a Changelog 格式, [0.2.0] Unreleased 节汇总全栈能力与 push.sh 安全修复, 附仓库内 C/TS 构建链移除记录。
- **全量回归**: `pytest tests/ -q` → **409 passed, 1 skipped** (166s), 零失败 — 此前 web_fetch 的 6 项失败已被并发会话修复; scripts/push.sh 过 bash -n, issue 模板 YAML 解析通过。
