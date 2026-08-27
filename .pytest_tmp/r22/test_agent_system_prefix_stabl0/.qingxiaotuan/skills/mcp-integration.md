---
name: MCP 接入外部能力
description: 需要通过 MCP (Model Context Protocol) 接入外部工具/数据源时, 如何配置与调用
updated_at: 0
use_count: 0
---

# MCP 接入外部能力

qxt 原生支持 MCP。接入一个外部 MCP Server 只需在 `~/.qingxiaotuan/config.yaml` 配置:

```yaml
mcp:
  enabled: true
  servers:
    - name: filesystem
      command: npx
      args: ["-y", "@modelcontextprotocol/server-filesystem", "."]
      env: {}
```

重启会话后, 该 server 暴露的工具会以 `mcp__<server>__<tool>` 的名字自动出现在你的工具列表里, 调用方式与本地工具完全一致。

注意:

- 确认命令本机可运行 (如已装 node/npx), 否则该 server 会接入失败 (不影响其它功能)。
- 用 `qxt mcp list` 查看已桥接的工具。
- server 经 stdio 子进程常驻, 退出会话时自动关闭。
