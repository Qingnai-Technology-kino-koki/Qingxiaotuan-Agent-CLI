# 安全策略 (Security Policy)

## 支持的版本

| 版本  | 支持情况 |
| ----- | -------- |
| 0.2.x | ✅       |
| < 0.2 | ❌       |

## 报告漏洞

**请勿通过公开 Issue 报告安全漏洞。**

推荐使用 GitHub 的私密漏洞报告（仓库 **Security** 标签页 → *Report a vulnerability*），或通过 GitHub 私信维护者组织 [@Qingnai-Technology-kino-koki](https://github.com/Qingnai-Technology-kino-koki)。

请在报告中尽量包含：

- 影响的版本与运行环境（OS / Python 版本）
- 复现步骤或概念验证（PoC）
- 影响评估（能读到什么 / 能改什么）

我们会在 **72 小时内**确认收到，修复后随下一个 patch 版本发布；报告者可选择是否在发布说明中致谢。

## 已知边界（非漏洞，但请知悉）

- `.env` 存放 API 密钥，已被 `.gitignore` 排除；请勿在 Issue、截图或日志中粘贴真实密钥。
- `crypto` 外部引擎是轻量流式加密（PBKDF2-HMAC-SHA256 派生密钥 + SHA256-keystream），**无认证标签**，适用于本地草稿混淆，不适用于生产级机密保护。
- shell 工具的安全拦截是静态风险评分 + 红线规则，属于纵深防御的一层，不能替代操作系统级权限隔离。
