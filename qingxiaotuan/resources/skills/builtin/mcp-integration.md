---
name: MCP 接入外部能力
description: 需要通过 MCP (Model Context Protocol) 接入外部工具/数据源时, 如何配置与调用
updated_at: 0
use_count: 0
---

# MCP 接入外部能力

qxt 原生支持 MCP (Model Context Protocol)。接入外部 MCP Server 只需在 `~/.qingxiaotuan/config.yaml` 配置。

## 基本配置

```yaml
mcp:
  enabled: true
  timeout: 30.0
  servers:
    - name: my-server
      command: npx
      args: ["-y", "@package/name", ...]
      env: {}
```

重启会话后, 该 server 暴露的工具会以 `mcp__<server>__<tool>` 的名字自动出现在工具列表里, 调用方式与本地工具完全一致。

命令:
- `qxt mcp list` — 查看已桥接的 server
- `qxt mcp tools` — 查看所有 MCP 工具
- `qxt mcp call <server> <tool> <args>` — 直接调用 MCP 工具
- `qxt mcp add <name> <command> [args...]` — 添加 MCP server

## 主流 MCP Server 接入样例

### 1. 文件系统 (Filesystem)

```yaml
mcp:
  servers:
    - name: filesystem
      command: npx
      args: ["-y", "@modelcontextprotocol/server-filesystem", "."]
```

提供的工具: `read_file`, `write_file`, `list_directory`, `search_files` 等。
适合: 项目文件操作、日志读取。

### 2. GitHub

```yaml
mcp:
  servers:
    - name: github
      command: npx
      args: ["-y", "@modelcontextprotocol/server-github"]
      env:
        GITHUB_TOKEN: "${GITHUB_TOKEN}"  # 你的 GitHub PAT
```

提供的工具: `search_repositories`, `create_issue`, `fork_repository`, `search_code` 等。
适合: GitHub 项目管理、代码搜索、Issue 自动化。

### 3. Brave Search

```yaml
mcp:
  servers:
    - name: brave-search
      command: npx
      args: ["-y", "@modelcontextprotocol/server-brave-search"]
      env:
        BRAVE_API_KEY: "${BRAVE_API_KEY}"
```

提供的工具: `brave_web_search`, `brave_local_search`。
适合: 联网搜索补充项目上下文。

### 4. SQLite 数据库

```yaml
mcp:
  servers:
    - name: sqlite
      command: npx
      args: ["-y", "@modelcontextprotocol/server-sqlite", "--db-path", "./data.db"]
```

提供的工具: `read_query`, `write_query`, `list_tables`, `describe_table` 等。
适合: 项目数据查询、数据迁移脚本生成。

### 5. PostgreSQL

```yaml
mcp:
  servers:
    - name: postgres
      command: npx
      args: ["-y", "@modelcontextprotocol/server-postgres"]
      env:
        POSTGRES_CONNECTION_STRING: "postgresql://user:pass@localhost:5432/mydb"
```

提供的工具: `query` (只读 SQL 查询)。
适合: 生产/测试数据库查询、Schema 分析。

### 6. Fetch (网页抓取)

```yaml
mcp:
  servers:
    - name: fetch
      command: npx
      args: ["-y", "@modelcontextprotocol/server-fetch"]
```

提供的工具: `fetch` (获取网页内容并转为文本)。
适合: API 文档读取、Stack Overflow 搜索。

### 7. Memory (持久记忆)

```yaml
mcp:
  servers:
    - name: memory
      command: npx
      args: ["-y", "@modelcontextprotocol/server-memory"]
```

提供的工具: `create_entities`, `search_nodes`, `open_nodes`。
适合: 跨会话知识存储。

### 8. Puppeteer (浏览器自动化)

```yaml
mcp:
  servers:
    - name: puppeteer
      command: npx
      args: ["-y", "@modelcontextprotocol/server-puppeteer"]
```

提供的工具: `puppeteer_navigate`, `puppeteer_screenshot`, `puppeteer_click`。
适合: Web 应用测试、截图、DOM 检查。

## 注意事项

- 确认 `npx` / `node` 已安装且在 PATH 中, 否则该 server 会接入失败 (不影响其它功能)。
- server 经 stdio 子进程常驻, 退出会话时自动关闭。
- `env` 支持 `${ENV_VAR}` 语法引用环境变量, 避免明文写入配置。
- 部分 server 需要 API Key (如 GitHub, Brave Search), 请先在对应平台申请。
- 首次启动 server 时 npx 会自动下载包, 可能需要几秒等待。
