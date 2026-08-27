# 青小团 Agent CLI — GitHub 发布与增长指南

> 本文档是给维护者的操作手册，不是给用户看的产品文档。所有 Star 数字为目标，非承诺。

---

## 一、推送前置步骤（必须你来执行）

当前本地状态：HEAD 在 `c58bdb1`，另有发行准备改动待提交（推送前先 `git add -A && git commit`）；`origin` 已指向 `https://github.com/Qingnai-Technology-kino-koki/Qingxiaotuan-Agent-CLI.git`，分支 `main`。

阻塞点（需你的账号授权，Agent 无法代劳）：

1. **在 GitHub 网页创建空仓库**
   - 访问 https://github.com/new
   - Repository name: `Qingxiaotuan-Agent`
   - Owner: `Qingnai-Technology-kino-koki`
   - 设为 **Public**
   - **不要**勾选 "Add a README / .gitignore / LICENSE"（本地已全有）
   - 点 Create repository

2. **解决本机推送认证**（三选一）
   - **方式 A（推荐，SSH）**：本地生成 `ssh-keygen -t ed25519`，把 `~/.ssh/id_ed25519.pub` 内容加到 GitHub → Settings → SSH and GPG keys；再把 remote 改为 SSH：
     ```
     git remote set-url origin git@github.com:Qingnai-Technology-kino-koki/Qingxiaotuan-Agent-CLI.git
     git push -u origin main
     ```
   - **方式 B（HTTPS + Personal Access Token）**：GitHub → Settings → Developer settings → PAT（勾 `repo`），推送时用 token 当密码：
     ```
     git push -u origin main   # 用户名填你的 GitHub 账号, 密码填 token
     ```
   - **方式 C（绕过本机 TLS 怪象）**：若遇 `CRYPT_E_NO_REVOCATION_CHECK`，临时：
     ```
     git -c http.schannelCheckRevoke=false push -u origin main
     ```

3. 推送后验证：`qxt ext selftest` 应显示 10/10 引擎就绪（纯 Python 进程内实现，无需任何编译）。

---

## 二、仓库描述（GitHub  About 栏，≤ 350 字符）

```
青小团 (Qingxiaotuan) — 以"最小影响半径"为核心设计理念的 Agent CLI。
Shell 执行前安全拦截 · 自我改进闭环 · 纯 Python 外部能力引擎。
Python 内核 + 插件系统, 模型中立。
```

一句话钩子（适合社媒）：
> 一个 Agent 在替你 `rm -rf` 之前, 会先告诉你"这一步会动到哪里"。

---

## 三、Topics 标签（最多 20 个, 选相关性高的）

```
agent
cli
ai
llm
coding-agent
developer-tools
ai-agent
automation
safety
self-improving
prompt-engineering
python
mcp
plugin-system
devtools
llm-tools
open-source
productivity
```

优先前 10 个（覆盖搜索热词）：`agent`, `cli`, `ai`, `coding-agent`, `developer-tools`, `ai-agent`, `safety`, `self-improving`, `python`, `llm`。

---

## 四、README 开篇怎么写（克制但吸引人）

原则（基于你的反馈收敛后）：**让代码和数据说话, 不空喊"世界级"**。

建议开篇结构（已部分落地在 README.md 顶部）：

1. **一行定位**（已写）：
   > 一个以"最小影响半径"为核心设计理念的 Agent CLI：在 Agent 替你改东西之前, 先想清楚这一步会动到哪里、会不会翻车。

2. **真实状态栏**（已写, 用客观数据背书）：
   > 完整测试套件 591 passed / 1 skipped（离线）；外部引擎 10/10 就绪（纯 Python, `qxt ext selftest` 验证）。

3. **3 个差异化能力**（不夸大, 用"设计目标/已实现"措辞）：
   - 最小影响半径：shell 执行前静态风险评分, critical 级默认拦截
   - 自我改进闭环：每次执行后复盘, 学到的护栏下次分发前自动生效
   - 统一多语言内核：外部能力引擎为纯 Python 进程内实现, 经注册表与 Python 内核直连, 零编译零 IPC 开销

4. **一张架构图 / 一张 demo 截图**（强烈建议补）：
   - 放一张 `qxt run` 真实任务的安全拦截截图（证明"已生效"而非宣称）
   - 放一张 `qxt ext selftest` 的引擎健康表截图

5. **快速开始**（复制即跑, 标注编译为可选）

**避免**：🚀⚡ 等 emoji 堆砌、"世界顶级"、"碾压"、"开箱即用"等无数据支撑的断言。

---

## 五、7 天 3.5k Star 增长策略（可执行, 非保证）

> 3.5k/7天 对早期项目是激进目标, 需要"产品力 + 时机 + 传播"三重叠加。以下为杠杆点。

### 发布日 (Day 0)
- **Hacker News**：发一篇 Show HN, 标题聚焦差异化而非功能罗列：
  `Show HN: 一个在 shell 执行前做安全拦截的 Agent CLI (最小影响半径)`
  正文讲"为什么 Agent 需要 blast radius 概念", 附 selftest 截图。
- **GitHub README 双语**（中 + 英摘要）：英文摘要决定国际传播上限。
- **Product Hunt**：提交并动员首批用户投票（前 4 小时关键）。

### Day 1-2：开发者社区精准投放
- **Reddit**：r/commandline, r/selfhosted, r/LocalLLaMA, r/programmingtools
  不刷屏, 每处只发一篇, 聚焦"安全拦截"这一独特卖点。
- **Discord/Telegram**：LangChain / AutoGPT / 本地 LLM 中文社区, 发 demo 视频（30 秒录屏：危险命令被拦）。
- **V2EX / 掘金 / 知乎**：中文开发者向, 写"我们为什么给 Agent 做最小影响半径"技术文。

### Day 3-5：内容杠杆
- **一段 30 秒 demo 视频**（GIF/MP4）：`rm -rf /` 被拦 + 替代建议。这是传播核弹。
- **一篇深度技术博客**：讲 safety 引擎的静态分析思路 + self-improve 闭环设计, 发到 dev.to / 掘金 / 公众号。
- **回应每一个 Issue / PR**：早期响应速度 = 社区信任。

### Day 6-7：滚雪球
- 若有 KOL 转发（如 AI 工具类博主）, 准备一份"一键体验"指南降低门槛。
- 在 README 加 "Used by / Awesome" 类目提名, 争取被 curated list 收录。

### 关键风险（必须正视）
- **门槛高**：缓解：纯 Python 全栈已消除编译门槛；仍需确保 `pip install -e .` 一步到位。
- **半成品感**：若用户 clone 后跑不通, 差评扩散快。缓解：发布前确保 `pip install -e .` + `qxt setup` 真能跑通一个真实任务。
- **宣称 vs 体验落差**：这是你指出的核心风险。所有"已生效"必须配截图/测试, 否则宁可写"设计目标"。

### 最小可行爆点
如果只能做一件事：**一段"危险命令被安全拦截"的 30 秒视频 + Show HN**。
它直观、可验证、戳中"Agent 乱改文件"的普遍焦虑, 是区别于其他 coding agent 的最强记忆点。

---

## 六、发布检查清单

- [ ] GitHub 空仓库已创建 (Public)
- [ ] 已推送 main 分支, 含本次 commit
- [ ] Release 页提供 Windows/macOS/Linux 一键安装说明（纯 Python, 无预编译产物）
- [ ] README 顶部有英文摘要 + 状态栏 + 快速开始
- [ ] 至少有 1 张 demo 截图 / 视频
- [ ] Topics 已填（前 10 个热词）
- [ ] `qxt ext selftest` 在干净环境验证通过
- [ ] CONTRIBUTING.md / LICENSE 就位（已存在）
- [ ] 准备 Show HN 文案 + 30 秒视频
