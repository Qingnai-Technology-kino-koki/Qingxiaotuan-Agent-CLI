# 安全策略文档 (Security Policy)

> **必读文件** — 青奈科技 Qingxiaotuan Agent CLI 开发者/用户/PR提交者必读。
> 本文件定义了系统的安全模型，违反本策略的PR将被拒绝合并。

---

## 核心原则：安全优先

> 青奈科技的招牌：**安全优先（Safety First）。**

我们的安全模型建立在三个层次上：

1. **白名单** — 用户同意后自动执行的日常操作
2. **黑名单** — 绝对禁止的操作（无论什么模式）
3. **极高风险防护** — 最高级别的命令需要多重人工确认

**优先级关系：白名单 < 黑名单** — 即使一个命令在白名单中，如果命中黑名单模式，也绝不自动执行。

---

## 第一层：黑名单（硬红线，永不自动执行）

### 极高风险命令（CRITICAL · 硬红线）

以下命令**无论什么模式**（YOLO / Plan / 普通）、**无论是否有确认通道**，都**绝不自动执行**——
`is_hard_redline()` 命中即直接拦截，不存在「放行」通道：

| 命令模式 | 描述 |
|---------|------|
| `rm -rf /` 或 `rm -r -f /` | 递归强制删除根目录 |
| `rm -rf /path/*` 含通配符递归删除 | 递归删除大量文件 |
| `dd if=... of=/dev/sda` | 直接写入磁盘设备 |
| `mkfs.*` | 创建文件系统（格式化） |
| `format C:` 等 | 格式化磁盘 |
| `wipefs` / `shred` / `> /dev/sdX` | 擦除磁盘数据 |
| `diskpart` / `cipher /w` / `bcdedit` / `reg delete` | Windows 磁盘/注册表级破坏 |
| `shutdown` / `halt` / `poweroff` / `reboot` | 系统关机/重启 |
| `init 0` / `init 6` | 系统关机/重启 |
| `systemctl poweroff` / `reboot` / `halt` | systemd关机/重启 |
| `chmod -R 000 /` | 递归移除所有权限 |
| `chown -R root /` | 递归变更root所有权 |
| `git push --force` / `git push -f` / `+refspec` | 强制推送 |
| `find / -delete` | 递归删除文件 |
| `Remove-Item -Recurse -Force` | PowerShell递归强删 |
| `Stop-Computer` / `Restart-Computer` | PowerShell关机/重启 |

> **注**: SQL 破坏性操作（`DROP TABLE`、`DELETE FROM ...`、`TRUNCATE TABLE`）**不属于**硬红线——
> 它们归类为「可确认关键级」，不会自动执行，但存在确认通道时允许用户经**极端 5 次确认**人工放行
> （仅本次生效，不会自动加入白名单）。YOLO 模式或无确认通道时同样 fail-closed 硬拦截。

### 可确认关键级（SQL 破坏性操作）

以下 SQL 破坏性操作**不会自动执行**，但在非 YOLO 模式且存在确认通道时，允许用户经 **5 次警告**人工放行：

| 命令模式 | 描述 |
|---------|------|
| `DROP TABLE` | 删除数据表 |
| `DROP DATABASE` | 删除数据库 |
| `DELETE FROM table` 无 WHERE | 删除全部数据 |
| `TRUNCATE TABLE` | 清空数据表 |

**极高风险警告机制（可确认关键级）：**
- 弹窗5次，每次确认按钮在不同位置
- 第1次：右下角
- 第2次：左上角
- 第3次：中间底部
- 第4次：右上角
- 第5次：左下角
- 每次弹窗间隔 ≥ 2 秒（防程序自动连续点击绕过）
- 任何一次取消 → 操作立即终止
- YOLO 模式或当前无确认通道 → 直接硬拦截（fail-closed）

### 高风险命令（HIGH）

以下命令**在YOLO模式下也不自动执行**，弹**3次警告**：

| 命令模式 | 描述 |
|---------|------|
| `chmod 777` | 全权限修改 |
| `git reset --hard` | 硬重置 |
| `git clean -f` | 删除未跟踪文件 |
| `git checkout -- .` | 丢弃所有工作区变更 |
| `docker rm -f` / `docker rmi -f` | 强制删除容器/镜像 |
| `kubectl delete` | 删除K8s资源 |
| `iptables -F` | 清空防火墙规则 |
| `ALTER TABLE ... DROP` | 修改表结构删除列 |
| 管道到 shell | `| sh` / `| bash` / `| zsh` |

**高风险警告机制：**
- 弹窗3次，每次确认按钮在不同位置
- 任何一次取消 → 操作立即终止

---

## 第二层：白名单（Trae模式）

### 工作原理

借鉴 Trae 的授权模式：

1. **手动授权**：用户通过 `qxt whitelist add <command>` 将命令加入白名单
2. **白名单生效后**：同类操作**自动执行**，不再弹出确认（前缀匹配，如 `git status` 匹配 `git status --short`）
3. **用户随时可撤销**：通过 `qxt whitelist remove <cmd>` 或 `qxt whitelist clear` 撤销白名单
4. **白名单永远低于黑名单**：即使命令在白名单中，如果命中红线模式，**仍然拦截**
5. **红线命令无法加入白名单**：`is_redline` 命中的命令，`qxt whitelist add` 会直接拒绝

> **注意**：用户在某次确认通道中「放行」的命令**仅本次生效**，不会自动写入白名单。

### 白名单示例

```json
// ~/.qingxiaotuan/whitelist.json
{
  "version": 1,
  "entries": [
    { "command": "ls", "description": "列出目录内容" },
    { "command": "git status", "description": "查看Git状态" },
    { "command": "python -m pytest", "description": "运行测试" }
  ]
}
```

### 白名单管理命令

| 命令 | 功能 |
|------|------|
| `qxt whitelist list` | 查看当前白名单 |
| `qxt whitelist add <command>` | 添加白名单规则（红线命令会被拒绝） |
| `qxt whitelist remove <cmd>` | 移除白名单规则 |
| `qxt whitelist clear` | 重置为默认白名单 |
| `qxt security status` | 查看安全系统状态（含白名单条目数与存储路径） |

---

## 第三层：月度更新

### 黑名单更新机制

**每月第一个工作日**，团队必须review并更新黑名单：

1. 检查过去一个月发现的**新攻击模式**
2. 检查是否有**新的绕过方式**被社区报告
3. 更新 `_CRITICAL_PATTERNS` / `_HIGH_PATTERNS` 正则库
4. 更新 `SECURITY_POLICY.md` 中的命令列表
5. 记录变更到 `CHANGELOG.md`

### 更新流程

```
每月1日 → 收集威胁情报 → 更新黑名单模式 → 测试验证 → 发布补丁
```

---

## 验证机制

### 检查命令是否安全

调用 safety 引擎对命令做风险评分（**实时可用，非虚构接口**）：

```bash
qxt ext call safety score '{"command":"rm -rf /"}'
# 输出: { "risk": "critical", "score": 100, "block": true, "reasons": ["recursive force delete (递归强制删除)"], ... }

qxt ext call safety score '{"command":"ls -la"}'
# 输出: { "risk": "none", "score": 0, "block": false, "reasons": [], ... }

qxt ext call safety score '{"command":"DROP TABLE users"}'
# 输出: { "risk": "critical", "score": 100, "block": true, "reasons": ["drop table (删除数据表)"], ... }
```

> **注**: 旧文档曾出现 `qxt safety check` 命令，该命令**不存在**（未实现），请使用上述 `qxt ext call safety score` 替代。

### 查看安全系统状态与白名单

```bash
qxt security status      # 查看安全系统状态 (含白名单条目数、存储路径、引擎健康)
qxt whitelist list       # 查看当前白名单
```

---

## 安全承诺

> 我们承诺：**任何情况下**，极高风险命令都不可能被自动执行。
> 即使 YOLO 模式、即使白名单包含、即使用户授权过——**5次警告是底线，不可绕过。**

---

*最后修订：2026-08-29*
*适用于：Qingxiaotuan Agent CLI 所有版本*
