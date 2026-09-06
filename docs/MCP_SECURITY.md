# MCP 安全配置指南

## 概述

MCP (Model Context Protocol) 安全机制提供多层防护，确保外部工具调用不会对系统造成危害。

## 安全特性

### 1. 工具权限控制

#### 白名单模式
只允许调用指定的工具：

```yaml
mcp:
  servers:
    - name: filesystem
      command: npx
      args: ["-y", "@modelcontextprotocol/server-filesystem", "."]
      security:
        allowed_tools:
          - read_file
          - list_directory
          - get_file_info
```

#### 黑名单模式
禁止调用指定的工具：

```yaml
mcp:
  servers:
    - name: filesystem
      command: npx
      args: ["-y", "@modelcontextprotocol/server-filesystem", "."]
      security:
        denied_tools:
          - delete_file
          - write_file
          - move_file
```

#### 需要确认的工具
某些工具调用需要用户确认：

```yaml
mcp:
  servers:
    - name: filesystem
      command: npx
      args: ["-y", "@modelcontextprotocol/server-filesystem", "."]
      security:
        require_confirm_tools:
          - write_file
          - delete_file
```

### 2. 频率限制

防止恶意或错误的高频调用：

```yaml
mcp:
  security:
    max_calls_per_minute: 60  # 每分钟最多 60 次调用
  servers:
    - name: filesystem
      command: npx
      args: ["-y", "@modelcontextprotocol/server-filesystem", "."]
      security:
        max_calls_per_minute: 30  # 服务器级限制
```

### 3. 审计日志

记录所有工具调用，便于审计和问题排查：

```yaml
mcp:
  security:
    audit_enabled: true  # 启用审计日志
```

查看审计日志：
```
/mcp audit 20  # 显示最近 20 条调用记录
```

### 4. 超时与重试

防止长时间阻塞和处理临时故障：

```yaml
mcp:
  timeout: 30.0  # 全局超时 (秒)
  servers:
    - name: filesystem
      command: npx
      args: ["-y", "@modelcontextprotocol/server-filesystem", "."]
      timeout: 10.0  # 服务器级超时
      max_retries: 3  # 最大重试次数
      retry_delay: 1.0  # 重试延迟 (秒)
```

### 5. 沙箱隔离

对不受信任的 MCP server，启用 `sandbox: true` 后执行**进程级隔离**（**无需 Docker**）：

1. **环境脱敏**：server 子进程的环境变量中抹除密钥类变量（`API_KEY`、`SECRET`、`TOKEN`、`PASSWORD`、`QXT_*`、`OPENAI*`、`AWS_*`、`DATABASE_URL` 等），防止密钥泄漏到 server。
2. **工作目录隔离**：server 子进程运行在临时空目录，无法读写工作区文件。
3. **调用前 fail-closed 安全闸门**：每次工具调用前，参数中的文本字段经 safety 引擎红线检测，命中 `rm -rf`、`DROP TABLE` 等危险操作**直接拒绝**，不发送给 server；闸门异常时同样保守拒绝。

```yaml
mcp:
  servers:
    - name: filesystem
      command: npx
      args: ["-y", "@modelcontextprotocol/server-filesystem", "."]
      security:
        sandbox: true  # 启用沙箱隔离 (环境脱敏 + 临时目录 + 参数红线闸门)
```

> **注**: 本沙箱为「最小隔离」纵深防御：防密钥泄漏、防读写工作区、防危险参数调用。
> 它不是操作系统级/容器级隔离——若需更强隔离，请在操作系统层自行限制（如容器、最小权限用户）。

## 完整配置示例

```yaml
mcp:
  enabled: true
  timeout: 30.0
  security:
    max_calls_per_minute: 60
    audit_enabled: true
  servers:
    # 文件系统服务器 (只读)
    - name: filesystem-read
      command: npx
      args: ["-y", "@modelcontextprotocol/server-filesystem", "."]
      security:
        allowed_tools:
          - read_file
          - list_directory
          - get_file_info
        max_calls_per_minute: 30
        audit_enabled: true
    
    # 数据库服务器 (需要确认)
    - name: database
      command: npx
      args: ["-y", "@modelcontextprotocol/server-postgres"]
      env:
        DATABASE_URL: postgresql://user:pass@localhost/db
      security:
        denied_tools:
          - drop_table
          - delete_all
        require_confirm_tools:
          - execute_query
          - update_records
        sandbox: true
    
    # API 服务器 (无限制)
    - name: api
      command: npx
      args: ["-y", "@modelcontextprotocol/server-api"]
      security:
        allowed_tools: ["*"]  # 允许所有工具
```

## 查看安全状态

```
/mcp security  # 显示所有服务器的安全策略
/mcp list      # 显示已连接的服务器
/mcp tools     # 显示可用工具
/mcp audit     # 显示调用审计日志
```

## 最佳实践

1. **最小权限原则**: 只授予必要的工具权限
2. **启用审计**: 始终启用审计日志以便排查问题
3. **设置频率限制**: 防止意外的高频调用
4. **使用沙箱**: 对不受信任的服务器启用沙箱隔离
5. **敏感工具确认**: 对修改类工具启用用户确认
6. **合理超时**: 设置适当的超时时间避免长时间阻塞

## 故障排查

### 工具调用被拒绝
检查是否在黑名单中，或不在白名单中：
```
/mcp security
```

### 超过频率限制
等待一段时间后重试，或调整限制：
```
qxt config set mcp.security.max_calls_per_minute 120
```

> **注**: `/mcp config` 命令**不存在**。调整 MCP 配置请使用 `qxt config set <key> <value>`（重启后生效），
> 或直接编辑 `~/.qingxiaotuan/config.yaml` 的 `mcp:` 段。

### 审计日志查看
```
/mcp audit 50  # 显示最近 50 条记录
```

### 沙箱执行失败

沙箱是进程级隔离（环境脱敏 + 临时目录 + 参数红线闸门），**不依赖 Docker**。常见失败原因与排查：

1. **server 子进程无法启动** — 检查 `command`/`args` 是否正确、可执行文件是否在 PATH 中，或尝试非沙箱模式复现：
   ```bash
   qxt mcp list   # 查看服务器连接状态
   ```
2. **工具调用被闸门拦截** — 参数中的文本字段命中红线（`rm -rf`、`DROP TABLE` 等）时会被 `fail-closed` 拒绝，这是预期行为；检查 `/mcp audit` 中 `sandbox-gate-denied` 记录。
3. **环境变量缺失导致 server 行为异常** — 沙箱会抹除密钥类变量（`API_KEY`、`SECRET`、`TOKEN`、`PASSWORD` 等），若 server 硬依赖某密钥，请显式在 server 的 `env:` 段声明（仍建议仅给最小必要权限）。
