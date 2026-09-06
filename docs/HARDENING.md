# 青小团安全加固指南 (Hardening)

本文档说明 `qingxiaotuan.harden` 安全加固工具集，以及如何在运行时真正启用它们。

加固模块把"代码层可对接、可验证、可审计"的安全能力做实。**它们不声称"国防级/军工认证"**——国密产品认证、HSM 实体、等保三级测评、供应链国产化是代码之外的合规流程，见 [README](../../README.md) 与 [QXT.md](../../QXT.md)。

---

## 0. 运行时默认强制（安全不是可选项）

安全加固**不是可选开关**——它已默认编入运行时主链路，无需任何配置即可生效：

- **每个工具调用都过统一安全闸门** (`core/tool_executor.py`)：内置工具 / shell / 文件写 / MCP 工具统一经 `SecurityGate` 裁决（fail-closed），危险操作（致命红线、MCP 参数注入、文件写入危险内容等）默认拦截，不依赖任何显式开关。
- **审计溯源默认开启**：所有工具调用的安全决策（允许/拒绝）写入 `security_auditor` 审计日志（AEAD 加密 + 防篡改链），落盘到 `~/.qingxiaotuan/security-audit.jsonl`（或 `audit/security_events.jsonl`），任何会话均可追溯。
- **网络出口与入站监听** 的门控在 shell 执行路径默认生效（见 §3）。
- **MCP 工具** 的描述注入扫描与参数注入扫描默认生效。

如需更强的边界（国密 SM4、CIDR 出口白名单、单向光闸模式、MCP 频率限制），在 `config.yaml` 的 `security:` 段配置即可（见末尾速查）。关闭这些增强边界**不会**关闭基础红线拦截与审计——基础安全永远是 fail-closed 的。

---

## 1. 可插拔密码学后端 (CryptoProvider)

模块：`qingxiaotuan/harden/crypto_provider.py`

让审计日志的加密算法在 **软件 / 国密 SM4 / 硬件 HSM** 之间可替换。所有后端 fail-closed：解密认证失败一律抛错，绝不静默返回篡改后的明文。

| 后端 | 名称 | 算法 | 依赖 | 适用场景 |
|------|------|------|------|----------|
| `SoftwareProvider` | `software` | AES-GCM 优先，否则 CTR+HMAC 回退 | 零强制依赖 | 默认，所有环境可用 |
| `GmsslProvider` | `gmssl` | SM4-CTR + SM3-HMAC | `gmssl` | 等保/商密算法合规 |
| `HsmProvider` | `hsm` | 预留 PKCS#11 接口 | `python-pkcs11` + 实体 HSM | 密钥不出卡的硬加密（**不适用于流式审计日志**，选它时自动降级 software 并告警）|

`SecurityAuditor` 已接入该后端。通过配置选择：

```yaml
security:
  crypto:
    provider: software   # software / gmssl / hsm
```

`SoftwareProvider` 的二进制格式与旧版 `_AeadCrypto` 字节级兼容，既有审计日志无需迁移即可继续解密。

> 注意：`gmssl`/`hsm` 后端在依赖缺失或设备不可用时**明确报错**，而非假装可用。HSM 的真实接入（密钥托管、PKCS#11 会话）需要实体设备与对应库，不归代码管。

查看可用后端：

```bash
qxt harden crypto-provider
```

---

## 2. 审计日志标准化导出 (AuditExporter)

模块：`qingxiaotuan/harden/audit_export.py`

订阅 `SecurityEventBus`，把安全事件导出为三种标准格式，便于对接 SIEM / 日志平台：

- **CEF**（Common Event Format，ArcSight/Splunk 等）
- **JSONL**（逐行 JSON）
- **RFC5424 Syslog**（标准 syslog，可经采集器转发）
- 可选 **HTTP 推送**（带超时，失败绝不反作用于安全裁决）

```bash
# 重放已落盘的安全事件总线日志
qxt harden audit-export --format cef,jsonl --from ~/.qingxiaotuan/security-audit.jsonl --out ./export
# 可选推送到 SIEM
qxt harden audit-export --format cef --push https://siem.example.com/ingest
```

`audit-export` 默认会按候选路径（`security-audit.jsonl` / `audit/security_events.jsonl`）自动寻找最近的落盘文件。

---

## 3. 网络出口加固 (EgressRestrictedNetworkGuard)

模块：`qingxiaotuan/harden/network_policy.py` + 内核 `qingxiaotuan/core/network_guard.py`

在既有域名白/黑名单之上新增：

- **CIDR 出口白名单**：命令含显式 IP 字面量且不在白名单内 → 拒绝；纯域名则降级为确认。
- **单向模式 (one-way)**：禁止 `nc -l` / `socat LISTEN` / `python -m http.server` / `ssh -D` / `sshd` 等入站监听类命令。

该守卫已下沉到内核 `NetworkGuard`，并通过 `security_plugin` 注入 `tools.shell` 执行路径使用的全局单例，**运行时真正生效**。配置启用：

```yaml
security:
  network:
    egress_cidr_allow: ["10.0.0.0/8"]   # 仅允许出网到该 CIDR
    one_way_mode: true                  # 禁止入站监听
```

命令行单独评估一条命令：

```bash
qxt harden network-check --command "curl http://192.168.1.1/secret" --egress-cidr 10.0.0.0/8
qxt harden network-check --command "python -m http.server 8000" --one-way
```

---

## 4. SBOM 与可复现构建锁

模块：`qingxiaotuan/harden/sbom.py` + `qingxiaotuan/harden/repro_build.py`

- **SBOM**：纯标准库生成 SPDX 2.3 软件物料清单（优先 `cyclonedx-python` 若存在），用于供应链可视化与验签基线。

  ```bash
  qxt harden sbom --out sbom.spdx.json
  ```

- **可复现构建锁**：基于 `pip freeze` 钉死全部依赖版本并带哈希，`verify_lock` 做依赖漂移检测（不联网）。

  ```bash
  qxt harden repro-lock --out requirements.lock    # 生成
  qxt harden repro-lock --verify --out requirements.lock   # 校验是否漂移
  ```

---

## 5. 安全 CI

文件：`.github/workflows/security-ci.yml`

在常规 CI 之外独立跑：

- `bandit`：静态安全扫描
- `pip-audit`：依赖 CVE 阻断
- `tests/test_harden_*.py` + `tests/test_security_integration.py`：加固模块与集成测试

---

## 6. 一键查看加固态势

```bash
qxt harden status
```

输出当前加密后端、审计落盘状态、SBOM 可用性、网络策略配置。

---

## 配置速查 (config.yaml 的 `security:` 段)

```yaml
security:
  crypto:
    provider: software          # software / gmssl / hsm
  network:
    allowed_domains: []         # 出网域名白名单 (空=不限制域名)
    blocked_domains: []         # 额外禁止域名 (叠加内置敏感域名)
    deny_remote_exec: true      # 禁止 wget/curl | sh 等远程执行
    deny_data_exfil: true       # 禁止向外上传/外泄数据
    egress_cidr_allow: []       # 出口 IP CIDR 白名单 (空=不限制)
    one_way_mode: false         # 单向模式: 禁止入站监听
  mcp:
    max_description_length: 10000
    audit_enabled: true
    block_on_injection: true
```

> 这些项此前仅靠 `security_plugin` 内联默认值读取，现已集中到 `qingxiaotuan/config/defaults.py` 的顶层 `security` 块，便于发现与覆盖。
