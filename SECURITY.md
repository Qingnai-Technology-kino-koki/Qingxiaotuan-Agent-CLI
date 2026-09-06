# 青小团 Agent 安全子系统

> 本文档是安全子系统的权威说明：架构、模块边界、决策树、审计闭环、威胁模型与增强路线图。
> 目标：让"允许什么、拒绝什么、为什么"在代码之外也能被快速核对，并给后续增强提供一致性锚点。

---

## 1. 设计原则

- **Fail-closed**：滤网自身故障、确认通道缺失、强隔离后端不可用、参数解析异常 → 一律拒绝，绝不静默放行。
- **可审计**：每条安全裁决（尤其拦截）都应汇入审计流，可离线追责。
- **确定性优先**：意图/信任/规则等静态判定不依赖任何内核/容器即可成立；没有强隔离时用滤网兜底，而不是裸跑。
- **统一入口，杜绝侧门**：所有工具经同一条安全流水线，避免"绕 run_shell 护栏走文件写/MCP"的旁路。

---

## 2. 整体架构

```
                    工具/命令请求
                          │
        ┌─────────────────▼────────────────────┐
        │  L0 意图滤网  IntentFilter             │ 红线/外泄/远程执行
        ├──────────────────────────────────────┤
        │  L1 信任滤网  TrustFilter              │ 工作区信任 / 域名白名单 / 计划模式
        ├──────────────────────────────────────┤
        │  L2 资源滤网  ResourceFilter           │ 网络开关 / 内存超时 / 隔离触发
        ├──────────────────────────────────────┤
        │  L3 强隔离滤网 HardIsolationFilter     │ 后端自动选择 / fail-closed
        └──────────────────────────────────────┘
                          │   Verdict (ALLOW/CONFIRM/ISOLATE/DENY)
                          ▼
             ┌ 允许 ┬ 确认 ┬ 隔离执行 ┬ 拒绝 ┐
             └──────┴──────┴─────┬───┴────┘
                           副本/diff/apply 或 强隔离后端
                          │
                          ▼
               SecurityAuditor / SecurityEventBus
               (AEAD 落盘, 防篡改链, 实时告警)
```

- 主链路位于 [`qingxiaotuan/sandbox/`](qingxiaotuan/sandbox/)（`SandboxManager` 统一入口）。
- 工具执行闸门位于 [`qingxiaotuan/core/tool_executor.py`](qingxiaotuan/core/tool_executor.py)（`_security_gate_check` + `_sandbox_gate_check`）。

---

## 3. 四层沙箱滤网

| 层 | 模块 | 职责 | 裁决落点 |
|----|------|------|----------|
| L0 意图 | `sandbox/filters.py::IntentFilter` | 硬红线(`rm -rf /`/`format c:`)、数据外泄、远程执行 → deny-critical/high | 模型无关、确定性最强 |
| L1 信任 | `sandbox/filters.py::TrustFilter` | 工作区信任分级(trusted/limited/untrusted/unknown) + 域名白名单 + 计划模式 | trusted 放行 / untrusted 拒 / unknown 确认 |
| L2 资源 | `sandbox/filters.py::ResourceFilter` | 默认禁网 → 无网隔离；高危写 → 副本模式 | 决定"上不上 L3" |
| L3 强隔离 | `sandbox/filters.py::HardIsolationFilter` | 后端自动选择；需强隔离但无强后端 → **fail-closed 拒绝** | 真正的容器/OS 隔离 |

**隔离后端自动选择**（强度降序探测）：`landlock → docker → seatbelt → token-acl → jobobject → local`。
见 `sandbox/backends.py`。

**工作区隔离融合模式**：`direct`（限路径直写，低危）+ `copy-diff-apply`（副本 → diff → 确认 → 应用，高危可回滚）。见 `sandbox/isolation.py`。

**决策模型**：`sandbox/verdict.py::Verdict`，动作按严格度 `ALLOW < CONFIRM < ISOLATE < DENY` 逐层收紧。

---

## 4. 安全模块职责与边界

| 模块 | 文件 | 职责边界 |
|------|------|----------|
| 红线引擎 | `ext/safety_engine.py` | 命令/SQL/写操作的静态风险评分；硬红线/软红线/良性判断 |
| 安全闸门 | `ext/security_gate.py` | 统一 `SecurityGate`：shell/MCP 工具/远程 prompt/文件写入/未知 kind |
| 规则引擎 | `ext/rules_engine.py` | YAML 规则加载校验；ReDoS 防护、secret 检测 |
| 安全策略 | `ext/security_policy.py` | 黑名单/策略月度更新提醒 |
| 加密引擎 | `ext/crypto_engine.py`、`harden/crypto_provider.py` | PBKDF2+AES-GCM/CTR+HMAC；可插拔后端，解密认证失败 fail-closed |
| 网络守卫 | `core/network_guard.py` | 网络命令识别；敏感域名/IP/CIDR/单向模式（出口策略权威判定） |
| 审计器 | `core/security_auditor.py` | AEAD 加密落盘、防篡改链、查询/统计/导出 |
| 事件总线 | `core/security_bus.py` | 统一定向事件；含沙箱事件(`security.sandbox.*`)，实时告警 |
| 工作区信任 | `core/workspace_trust.py` | `trusted/limited/untrusted/unknown` 分级与落盘 |
| 白名单 | `core/whitelist.py` | 只读命令白名单、确认码、多阶段确认 |
| MCP 加固 | `tools/mcp/security.py` | 注册描述扫描、参数注入检测（独立威胁类） |
| 权限规则 | `tools/permissions.py` + `permission_fusion.py` | `permissions.rules` 规则表 / shell / network 策略决策 |
| 沙箱统一入口 | `sandbox/manager.py` | 四层滤网评估 + 审计闭环 + 隔离执行选路 |

---

## 5. 关键决策树

### 5.1 网络出口（权威判定 = `NetworkGuard.check`）
- **L0 意图滤网**：消费 `guard.check().action == "deny"`（外泄/远程执行/敏感域名/端口扫描/CIDR 越界）→ 沙箱 deny high。**不负责**白名单强制。
- **L1 信任滤网**：负责**域名白名单强制**（`payload.meta.allowed_domains` ← `sandbox.allowed_domains` / `permissions.network.allow_domains`）。
- **L2 资源滤网**：负责**存在性 + 无网开关**：含网络请求且默认禁网 → `network=False + isolate=True`（气隙执行）。本地命令不受影响。

> 一致性契约：被 `NetworkGuard` 判 deny 的命令，沙箱 L0 必须 deny 且严重度一致（high）。

### 5.2 信任分级
- `unknown` → 确认；`untrusted` → 拒绝；`limited` → 白名单外确认；`trusted` → 放行。
- 计划模式（plan_mode）一律降级为"只读确认"。

### 5.3 MCP 校验点（两类互补威胁）
- **红线层**（命令级致命）：`SecurityGate.decide_mcp_tool`；无 gate 兜底 `_is_mcp_dangerous` → 两者**全字段递归红线，覆盖一致**。
- **注入层**（指令覆盖/角色劫持）：`MCPSecurityGuard.scan_tool_params` → 独立拦截。
- 两类不互相吞，是纵深防御的分工，不是重复。

---

## 6. 审计闭环

- 每条沙箱裁决（DENY 全量；CONFIRM/ISOLATE 仅高危以上）经 `SandboxManager._emit_audit` 汇入：
  - `SecurityAuditor`：AEAD 加密落盘 + 防篡改链（含 layer/tool/trust/backend 等上下文）。
  - `SecurityEventBus`：`security.sandbox.blocked/isolated/confirmed` → `security-audit.jsonl` + critical/high 实时告警 + 导出。
- 审计是补充保障：任何审计异常都被吞掉，**绝不反噬沙箱主流程**。
- 导出格式：JSONL / CEF / RFC5424 Syslog（`harden/audit_export.py`）。

---

## 7. 威胁模型（已覆盖）

| 威胁 | 防线 | 状态 |
|------|------|------|
| 致命命令破坏主机 | L0 意图滤网 / 红线引擎 / 强隔离后端 | ✅ |
| 工作区外不可信代码 | 工作区信任分级 + 路径限界 + 副本模式 | ✅ |
| 数据外泄 / 远程执行 | NetworkGuard + L0 + L2 无网隔离 | ✅ |
| MCP 恶意 server 参数注入 | 注册描述扫描 + 参数注入检测 + 全字段红线 | ✅ |
| 提示词注入(指令覆盖) | 注入检测层（独立于红线） | ✅ |
| 审计被篡改 / 明文泄露 | AEAD 加密 + 防篡改链 | ✅ |
| 滤网/后端故障被绕过 | 全栈 fail-closed | ✅ |

---

## 8. 增强路线图（按价值排序）

| # | 模块 | 增强方向 |
|---|------|----------|
| E1 | `tools/mcp/policy.py` + `security.py` | MCP **server 信任分级**：把每台 server 的权限域(只读/工具白名单/允许的域名)落到持久化策略；新增"调用前工具级授权 + 会话内可回撤"，取代散落的临时确认 |
| E2 | `core/workspace_trust.py` | 信任**自动衰减与重校验**：trusted 在目录变更/新敏感文件(.env/.git/config/密钥)出现时自动降级到 limited 并提示复议 |
| E3 | `sandbox/backends.py` + `arch/platform.py` | **Job Object 资源配额落地**：CPU 配额、内存上限、进程树击杀超时兜底(当前仅存在性探测) |
| E4 | `sp core/security_auditor.py` + `harden/audit_export.py` | 审计**关联合并 + 跨会话异常检测**：同源多次红线(如 MCP 反复注入)聚合告警；导出支持轮转与加密 |
| E5 | `core/tool_executor.py` | **写操作风暴检测**：单会话内高频率写/删除触发节流与告警，防误删批量任务 |
| E6 | `tools/permissions.py` | `permissions.rules` 增强：**负向 glob**(`!bin/**`)、MCP 工具名规则、会话内临时授权提升 |
| E7 | `sandbox/isolation.py` | 副本模式的 **diff 可视化确认**：以 CLI/交互卡片展示变更后再 apply |
| E8 | `config` | `sandbox`/`permissions` 配置的 **schema 强校验 + fail-closed 可观测性**（开启项/降级项可视化） |

> 判定"先做哪些"：优先级高且可独立演进 → **E2、E3、E5**；依赖交互设计 → **E1、E7**；属加固打磨 → **E4、E6、E8**。

---

## 9. 测试与回归基准

核心安全回归文件（改动后必须全绿）：
`test_sandbox_system.py` / `test_mcp_checkpoint_convergence.py` / `test_security_network_ownership.py`
`test_permission_rules.py` / `test_security_integration.py` / `test_security_integration_tool_executor.py`
`test_security_hardening.py` / `test_security_auditor_provider.py` / `test_harden_network_policy.py`

> 注：`test_security_v2.py::TestSymlinkResolution` 为 Windows 环境相关、与安全逻辑无关的既有用例，不作为回归门槛。