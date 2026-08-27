# 软件工程能力评测基准 (bench)

用一组本地 SWE 任务，量化青小团 CLI 的「真实编程能力」，并可与 Claude Code 横向对比。
框架本身**不依赖外网**：任务声明在 `tasks.yaml`，运行器在临时工作区里启动真实 `qxt run` 子进程，
再执行验收脚本判定通过/耗时/成本。

## 快速开始

```bash
# 1) 仅校验框架与任务定义 (不调用模型)
python bench/run.py --dry

# 2) 真实跑测 (需先 qxt setup 配好模型端点)
python bench/run.py

# 3) 只跑单个任务 / 指定模型 / 指定 qxt 路径
python bench/run.py --task fix_bug
python bench/run.py --model deepseek/deepseek-chat
python bench/run.py --qxt .venv/Scripts/qxt.exe

# 4) 机器可读输出
python bench/run.py --json
```

## 任务定义 (tasks.yaml)

每个任务字段：

| 字段 | 含义 |
| --- | --- |
| `id` | 任务标识 |
| `instruction` | 给 agent 的自然语言指令 |
| `files` | 工作区初始化文件 (`相对路径: 内容`) |
| `check` | 验收 shell 命令，退出码 0 = 通过 |
| `timeout` | 单任务超时（秒）|

四类覆盖：理解 / 修 bug / 补测试 / 多文件重构。

## 对比 Claude Code

1. 用同一组 `tasks.yaml` 在 Claude Code 上跑（指令 + 验收脚本一致）。
2. 两边各记录：通过数、总耗时、估算成本（青小团用 `/cost`，Claude Code 用 `--cost`）。
3. 填入下面的对比表，作为「超越」的量化证据：

| 任务 | 青小团(通过/耗时/成本) | Claude Code(通过/耗时/成本) |
| --- | --- | --- |
| understand_api |  |  |
| fix_bug |  |  |
| add_test |  |  |
| refactor_util |  |  |

## 注意

- 真实跑测需要可用的模型端点（OpenAI 兼容 / Anthropic / 本地）。无端点时请用 `--dry` 校验框架。
- 验收脚本越「行为化」（跑 pytest / 断言真实输出）越能反映真实能力；不要只用「文件存在」做验收。
- `check` 在青小团改完代码后的临时工作区执行，与 CI 语义一致。
