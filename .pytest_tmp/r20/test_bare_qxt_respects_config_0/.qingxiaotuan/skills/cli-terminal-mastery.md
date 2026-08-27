---
name: 终端工具高效使用
description: 在 CLI 环境下用 qxt 内置工具 (shell/filesystem/code) 高效完成文件操作、命令执行与代码探索时遵循的纪律
updated_at: 0
use_count: 0
---

# 终端工具高效使用

你是运行在终端里的 Agent, 手边有这些工具:

- 文件: `read_file`(可 offset/limit 分段) · `write_file`(整文件覆盖) · `edit_file`(精确替换, old_string 必须唯一) · `glob`(批量定位) · `search_files`(正则) · `move_file` · `delete_file`(危险)
- 命令: `run_shell`(危险, 需确认) · `run_tests`(自动探测框架)
- 代码: `codebase_map` · `find_symbol` · `find_references` · `git_status`

纪律:

1. 大文件用 `read_file` + `offset/limit` 分段, 不要一次喂全量。
2. 改代码优先 `edit_file`(保留上下文、diff 小); 新建文件才 `write_file`。
3. `old_string` 不唯一时, 多带几行上下文使其唯一, 不要盲目整文件覆盖。
4. 探索未知仓库先 `codebase_map` 看全貌, 再 `find_symbol`/`find_references` 定位, 改前 `git_status` 看清现状。
5. 验证改动用 `run_tests`; 失败先读报错、定位、修、再跑, 直到绿。
6. `run_shell` 是危险操作: 涉及删除/网络写入/权限变更时, 标准模式会让你确认, 如实说明后果。
