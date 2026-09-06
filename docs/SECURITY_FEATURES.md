# 青小团安全系统完整功能说明

## 概述

青小团采用三层防御架构，确保 AI Agent 执行的每个操作都是安全可控的。

## 三层防御架构

```
┌─────────────────────────────────────────────────────┐
│ 第1层: 黑名单 (Always Block)                         │
│   is_redline() 命中 → 无论如何都不执行               │
│   YOLO 也不豁免                                      │
├─────────────────────────────────────────────────────┤
│ 第2层: 白名单 (Trae 模式)                           │
│   用户同意后自动执行                                  │
│   优先级永远低于黑名单                                │
├─────────────────────────────────────────────────────┤
│ 第3层: 多阶段确认                                    │
│   极高风险 → 5次警告 (不同位置按键)                   │
│   高风险 → 3次警告 (不同位置按键)                    │
│   普通命令 → 直接执行                                 │
└─────────────────────────────────────────────────────┘
```

## 核心安全特性

### 1. 黑名单 (红线命令)

以下命令**无论如何都不执行**，即使 YOLO 模式：

| 类型 | 示例 |
|------|------|
| 递归强删 | `rm -rf /`, `rm -fr /`, `del /s /q`, `rmdir /s` |
| 磁盘写入 | `dd if=... of=/dev/sdX`, `mkfs.*`, `format C:` |
| 系统关机 | `shutdown`, `halt`, `poweroff`, `reboot`, `init 0/6` |
| 权限全灭 | `chmod -R 000 /`, `chown -R root /` |
| 强制推送 | `git push --force`, `git push -f`, `git push +refspec` |
| PowerShell | `Remove-Item -Recurse -Force`, `Stop-Computer`, `Restart-Computer` |
| 系统服务 | `systemctl poweroff/reboot/halt` |
| 磁盘擦除 | `wipefs`, `shred`, `> /dev/sdX`, `diskpart`, `cipher /w` |

> **注**: SQL 破坏性操作（`DROP TABLE`、`DELETE FROM ...`、`TRUNCATE TABLE`）**不属于**硬红线——
> 它们归类为「可确认关键级」，不会自动执行，但存在确认通道时允许用户经**极端 5 次确认**人工放行
> （仅本次生效，不会自动加入白名单）。

**红线判定通过 `is_redline()` 函数实现（单一来源）**，覆盖：
- Token 化判定（穿透子壳/变量/引号/解释器间接写法）
- 正则模式库（_CRITICAL_PATTERNS）
- 递归间接调用展开（最多32层）
- PowerShell Base64 解码
- Unicode NFKC 归一化

### 2. 白名单 (Trae 模式)

白名单中的命令自动执行，无需重复确认。

**白名单约束：**
- 白名单通过 CLI 手动维护：`qxt whitelist list / add <command> / remove <cmd> / clear`（Trae 模式）
- 黑名单/红线命令**永远无法加入白名单**
- 白名单中的命令如果包含危险子模式，仍需多阶段确认（防御纵深）
- 白名单存储在 `~/.qingxiaotuan/whitelist.json`
- 注意：用户在某次确认通道中「放行」的命令**仅本次生效**，不会自动写入白名单——白名单只由上述 CLI 手动维护

**管理命令：**
```bash
qxt whitelist list          # 列出白名单
qxt whitelist add <command> # 添加白名单规则
qxt whitelist remove <cmd>  # 移除白名单规则
qxt whitelist clear         # 重置为默认值
```

### 3. 多阶段确认

**极高风险命令（5次警告）：**

```
第1次: 右下角 [确认执行]
第2次: 左上角 [确认执行]
第3次: 中间底部 [确认执行]
第4次: 右上角 [确认执行]
第5次: 左下角 [确认执行]
```

**高风险命令（3次警告）：**

```
第1次: 右下角 [确认执行]
第2次: 左上角 [确认执行]
第3次: 中间底部 [确认执行]
```

每次警告之间至少间隔2秒，防止程序自动连续点击绕过。

### 4. 警告级别定义

#### Level 5 (极高风险)

> **两级语义（重要）**：Level 5 里大部分命令命中**硬红线**（`is_hard_redline`），
> 无论在什么模式、有无确认通道都**直接拦截，连 5 次警告都不会弹**；
> 只有 SQL 破坏性操作属于**可确认关键级**——非 YOLO 且存在确认通道时弹 **5 次警告**人工放行。

**硬红线（直接拦截，无确认通道）：**
- `rm -rf /`、`rm -fr /`
- `dd if=... of=/dev/sdX`
- `mkfs.*`、`format C:`
- `shutdown`、`halt`、`poweroff`、`reboot`
- `init 0/6`、`systemctl poweroff/reboot/halt`
- `chmod -R 000 /`、`chown -R root /`
- `Remove-Item -Recurse -Force`、`Stop-Computer`、`Restart-Computer`
- `git push --force`、`git push -f`

**可确认关键级（弹 5 次警告，可人工放行，仅本次生效）：**
- `DROP TABLE`、`DROP DATABASE`、`DELETE FROM ...`、`TRUNCATE TABLE`

#### Level 3 (高风险)

命中以下任一模式的命令：
- `chmod 777`
- `git reset --hard`
- `git clean -f`
- `git checkout -- .`
- `docker rm -f`、`docker rmi -f`
- `kubectl delete`
- `iptables -F`
- `ALTER TABLE ... DROP`
- `kill -9`、`pkill`、`killall`

#### Level 0 (普通)

无危险模式的命令，直接执行。

## CLI 命令

### 安全系统状态

```bash
qxt security status    # 查看安全系统状态
```

### 白名单管理

```bash
qxt whitelist list           # 列出白名单
qxt whitelist add <command>  # 添加白名单规则
qxt whitelist remove <cmd>   # 移除白名单规则
qxt whitelist clear          # 重置为默认值
```

### 安全策略更新

```bash
qxt security update    # 更新安全策略
```

## 配置路径

- 安全策略: `~/.qingxiaotuan/security/`
- 白名单: `~/.qingxiaotuan/whitelist.json`
- 安全日志: `~/.qingxiaotuan/audit/audit.log`

## 月度更新流程

1. 每月第一个工作日检查新发现的攻击模式
2. 更新 `_CRITICAL_PATTERNS` / `_HIGH_PATTERNS` / `_MEDIUM_PATTERNS`
3. 更新 `_EXTREME_RISK_PATTERNS` / `_HIGH_RISK_PATTERNS`
4. 运行测试确认无回归
5. 更新 CHANGELOG.md 和 SECURITY.md

## 安全架构图

```
用户输入
    │
    ▼
┌─────────────────────────────────────────────────────┐
│                  安全引擎                            │
│  ┌─────────────────────────────────────────────────┐│
│  │ is_redline() 检查                                ││
│  │ - Token 化判定                                   ││
│  │ - 正则模式库                                     ││
│  │ - 递归间接调用展开                               ││
│  │ - PowerShell Base64 解码                         ││
│  │ - Unicode NFKC 归一化                            ││
│  └─────────────────────────────────────────────────┘│
│                      │                              │
│                      ▼                              │
│  ┌─────────────────────────────────────────────────┐│
│  │ 黑名单命中?                                      ││
│  │ 是 → 立即拦截 (返回错误)                         ││
│  │ 否 → 继续                                        ││
│  └─────────────────────────────────────────────────┘│
│                      │                              │
│                      ▼                              │
│  ┌─────────────────────────────────────────────────┐│
│  │ 白名单检查 (Trae 模式)                           ││
│  │ 命中 → 检查警告级别                              ││
│  │       高危 → 多阶段确认                          ││
│  │       普通 → 放行                                ││
│  │ 未命中 → 继续                                    ││
│  └─────────────────────────────────────────────────┘│
│                      │                              │
│                      ▼                              │
│  ┌─────────────────────────────────────────────────┐│
│  │ 多阶段确认                                       ││
│  │ Level 5: 5次警告 (不同位置按键)                  ││
│  │ Level 3: 3次警告 (不同位置按键)                  ││
│  │ Level 0: 直接执行                                ││
│  └─────────────────────────────────────────────────┘│
│                      │                              │
│                      ▼                              │
│               执行命令 / 拦截                        │
└─────────────────────────────────────────────────────┘
```

## 与 Claude Code 的安全对比

| 安全特性 | Claude Code | 青小团 |
|---------|-------------|--------|
| 黑名单机制 | ❌ 无 | ✅ 红线命令永远拦截 |
| 白名单机制 | ❌ 无 | ✅ Trae 模式 |
| 多阶段确认 | ❌ 无 | ✅ 5次/3次警告 |
| 不同位置按键 | ❌ 无 | ✅ 防肌肉记忆 |
| 间接调用展开 | ❌ 无 | ✅ 32层递归 |
| PowerShell 保护 | ❌ 无 | ✅ Base64 解码 |
| 月度更新 | ❌ 无 | ✅ 自动提醒 |

## 最佳实践

1. **理解风险级别**：了解哪些命令是极高风险/高风险
2. **合理使用白名单**：只添加真正信任的命令
3. **定期更新**：关注每月安全策略更新
4. **审计日志**：定期检查安全审计日志
5. **报告问题**：发现新的攻击模式及时报告

## 故障排查

### 命令被误拦截

检查是否命中黑名单模式：
```bash
qxt security status
```

### 白名单不生效

检查白名单配置：
```bash
qxt whitelist list
```

### 安全策略过期

更新安全策略：
```bash
qxt security update
```

---

*最后修订：2026-08-29*
*适用于：Qingxiaotuan Agent CLI*
