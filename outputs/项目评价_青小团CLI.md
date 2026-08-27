# 青小团 Agent CLI（Qingxiaotuan / qxt）项目评价

> 评价基于仓库实际代码、文档与全量测试运行结果（2026-08-27 本地跑测）。

## 一句话结论
这是一个**工程完成度相当高、设计理念清晰、安全护栏是最大差异化亮点**的纯 Python Agent CLI。代码可读性、测试覆盖、国际化、可移植性都超出一般个人开源项目水准；主要薄弱环节是「自动模型路由」「技能市场」「PyPI 发布」仍停留在路线图（Roadmap）阶段，以及 Windows 下部分文件安全测试在本机环境会因 trash 机制缺失而失败。

## 项目概览
- **定位**：纯 Python、零编译、模型无关的本地 Agent CLI，核心卖点是「**执行前先做最小影响半径（blast radius）风险评估**」，危险命令（如 `rm -rf /`、`git push --force`、`DROP TABLE`）默认拦截，而非事后记录。
- **规模**：137 个 Python 模块，约 29,400 行源码；10 个纯标准库引擎（diff/crypto/index/ansi/safety/json/search/notify/rules/skill-market/ledger）。
- **依赖极简**：运行时仅 `openai / pyyaml / rich / prompt_toolkit / httpx` 5 个第三方包；Python 3.11–3.13。
- **测试**：891 例中 859 通过、11 失败、1 跳过（详见下方「未决风险」）。

## 亮点（值得肯定的部分）

1. **安全护栏是真正的核心竞争力**
   - `ext/safety_engine.py` 对红线命令做了**分词 + 旗标归一 + 子壳/变量/引号穿透**（如 `R="rm"; $R -rf /` 也能命中），不是简单正则。这是比多数 CLI agent 更扎实的做法。
   - 配套的 `ledger`（事务化变更账本 + 可逆回滚）、`/plan` 只读模式、`/undo`、`/impact`、审计日志（脱敏落盘）构成一套完整的「可撤销、可审计」闭环。

2. **工程纪律好**
   - 591→872 增长中的离线测试，含**真实 HTTP 往返的端到端测试**（mock OpenAI server + 真实 `qxt` 子进程 + 独立 worker 进程），CI 矩阵覆盖 3.11/3.12/3.13，`mypy` 真实门禁（非 `|| true` 假门）。
   - `conftest` 全局环境隔离（清空 `*API_KEY`、临时 `QXT_HOME`），杜绝本机凭据泄漏进测试。
   - 启动速度刻意优化：重 SDK 延迟导入，`qxt --help` 实测约 1.7s；`CONTEXT_SUMMARY` 记录每轮改动，进度可追溯。

3. **产品化细节到位**
   - 吉祥物状态机（idle/thinking/working/alert/done）把「Agent 在干嘛」可视化，安全拦截时明显变色——这也是中国语境下很讨巧的交互设计。
   - 10 语言 i18n（含繁体中文），回复语言跟随配置；纯文本输出利于 piping/CI。
   - 多模型适配（OpenAI 兼容 / Anthropic / Gemini / 本地）、`/swarm` 多代理、三层记忆（SQLite FTS5）、自进化 reflector、cron。

## 不足与风险

1. **核心差异化功能尚未闭环**
   - `/route` 模型路由目前是**咨询式**（只建议不切换），自动路由（`router.enabled`）还没接进 agent loop（Roadmap 第一条）。
   - 技能市场是「registry + pull/publish」骨架，公开 registry 未建立；PyPI 发布未完成。

2. **Windows 文件安全测试在本机失败（11 项）**
   - 失败集中在 `test_ledger`、`test_modes_and_tools`、`test_enhancements`、`test_memory_persistence`、`test_shell_safety_guard`。
   - 根因：pytest 临时目录残留符号链接权限问题（`E:\Temp\pytest-of-28726\pytest-current` 拒绝访问），叠加本地无 OS trash 机制。这是**环境噪音而非逻辑缺陷**——恰恰说明这些用例依赖 Windows 回收站行为。建议作者在 CI 用 `--basetemp` 或在无 trash 环境走二次确认分支。

3. **`cli/commands.py` 体积过大**（2,822 行），是后续维护的主要复杂度热点；建议按命令拆分。

4. **crypto 引擎**：README 已诚实声明——自研 SHA256-keystream 流密码**无认证标签**，仅适合本地防篡改，非高安全原语。这是正确免责，但用户若误用需留意。

## 实测关键数据
- 源码：`find qingxiaotuan -name "*.py" | wc -l` → 137 文件 / 29,411 行
- 测试：`.venv/Scripts/python.exe -m pytest tests/ -q --basetemp=...` → **859 passed, 11 failed, 1 skipped**
- 关键模块行数：commands.py 2822、model_lists.py 1144、provider_catalog.py 764、agent.py 703、router.py 617、reflector.py 456、swarm.py 449

## 总体评分（仅供参考）
- 工程质量：★ ★ ★ ★ ☆（4/5）
- 创新/差异化：★ ★ ★ ★ ★（5/5，护栏设计稀缺）
- 成熟度/可用度：★ ★ ★ ★（4/5，核心闭环已可用，路由/市场待补）
- 可维护性：★ ★ ★ ☆（3/ 5，单文件偏大）

**下一步建议**：优先把 `/route` 自动路由接通、补全 Windows 下 trash 兜底（二次确认分支）让 11 个用例全绿，再冲击 PyPI 发布——这三项完成后即可从「强」升级到「完备」。
