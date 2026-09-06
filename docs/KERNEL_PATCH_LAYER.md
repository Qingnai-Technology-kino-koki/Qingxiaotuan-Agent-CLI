# 内核补丁层 (Kernel Patch Layer) —— 扩展增强模块层设计

> 对现有微内核**零改动**地注入可回滚、可审计、版本感知的增强补丁。
> 本质是"内核补丁类"的扩展增强模块层：不新增能力，而是**在既有实现上做安全热补丁**。

---

## 1. 背景与动机

对 `qingxiaotuan` 全量的评估（`core/ kernel/ ext/ arch/ ports/ tui/`）发现四类"不完善"
信号，其中运维层可安全修复的点：

| 类别 | 证据 | 处理方式 |
| --- | --- | --- |
| **热路径开销** | `i18n.t()` 每取一次词都遍历语言链 + `getattr(TRANSLATIONS)`；`retry.classify_error()` 每次异常都 `try/except` 重复 import | 内核补丁：记忆化 + 导入缓存 |
| **占位/空壳** | `arch/execution.py` 的 `BaseAgentLoop.run()`、`kernel/contract.py` 的 `ChatProvider.generate()` 仅 `raise NotImplementedError` | 属契约注入点，**交由补丁增强**，不动契约 |
| **双重注册中心** | `ext/registry.py` 的 `ENGINE_MAP` 与 `ext/extension_registry.py` 分层注册表并存；`AggregateProxy`/增强链语义在两处重复 | 收敛为两类正交机制（见 §3.3） |
| **线程安全** | `tui` 的 `_animate` 线程与渲染路径无锁读写 `_busy/_running/_moon_frame` 等 | 补丁可包裹渲染做快照化；本次以 Perimeter 化护栏挂载 |

研究发现：项目**已具备**完善的能力扩展机制（`ServiceContainer.extend_service`、
`MiddlewareEventBus`、`ext/extension_registry` 的 pre/base/post/replace 叠加）。缺的正是——
对**现有内部实现**做"小而安全、可一键回滚"的**运行时补丁**的统一入口。本层补上这一环。

---

## 2. 设计目标

1. **完全兼容**：不改动任何现有模块源码；通过 `@plugin` 注册进 Kernel，走现有
   `Kernel.activate_all()` 生命周期；服务以 `"kernel_patch"` 暴露；事件走
   `MiddlewareEventBus`（`patch.*`）。
2. **零风险默认**：`capability` 补丁 fail-open——任何异常都隔离并回退原实现，绝不
   因补丁崩溃主流程。
3. **可回滚**：每个补丁保存原引用，`revert(name)` / `revert_all()` 完全还原现场。
4. **版本感知**：`min_kernel` 防御新内核改签名，低于预期则跳过并广播 `patch.bypass`。
5. **声明式 + 懒解析**：`@patch_impl` 声明时零成本，`apply` 才 import 目标模块。
6. **可审计**：`patch.applied / reverted / bypass / error` 事件可供安全审计消费。

---

## 3. 架构

### 3.1 新模块（`qingxiaotuan/kernel_patch/`）

```
qingxiaotuan/kernel_patch/
├── __init__.py      # 门面: install / get_manager / patch_impl / ensures
├── patch_base.py    # 引擎: PatchSpec / TargetPatcher / KernelPatchManager / 装饰器
├── builtin.py       # 内置补丁: i18n.t 记忆化 / retry 导入缓存 / 版本跳过演示
└── plugin.py        # KernelPatchPlugin: 接入微内核生命周期
```

### 3.2 补丁四种模式（`mode`）

| mode | 签名 | 语义 |
| --- | --- | --- |
| `wrap`（默认） | `impl(original, *a, **kw)` | 完全控制：先逻辑后原始，或短路原始 |
| `before` | `impl(*a, **kw)` | 返回 `SKIP_ORIGINAL` 交还原始，否则直接采用返回值 |
| `after` | `impl(result, *a, **kw)` | 原实现先执行，再处理结果 |
| `replace` | `impl(*a, **kw)` | 整体覆盖（原实现保存不调用） |

`TargetPatcher` 解析 `Module.fn` / `Module.Class.method` 点路径，保存原引用，应用时
以 wrapper 置换、回滚时还原。同一目标默认只承载一个补丁，避免依赖嵌套回滚顺序。

### 3.3 与现有机制的分工（消除"双重注册"困惑）

| 机制 | 属层 | 职责 |
| --- | --- | --- |
| `ServiceContainer.extend_service/provide_multi` | 微内核服务 | **服务级**叠加（`tool_executor` 等多贡献者服务） |
| `ext/extension_registry` | 能力扩展 | **能力 key**贡献者叠加（diff/crypto/safety…） |
| **`kernel_patch`（本层）** | 内核补丁 | **对既有内部可调用对象**做安全热补丁 |

前两者是"挂新能力"，本层是"修/增强现有内部实现"，正交不冲突，且补丁可从
`extension_registry` 的注册方加载（第三方可 `register()` 贡献补丁工厂）。

---

## 4. 内置补丁（开箱即用，可回滚）

| 补丁 | 目标 | 模式 | 效果 |
| --- | --- | --- | --- |
| `_i18n_t_memo` | `i18n.t` | `wrap` | 纯取词走 raw-text 记忆化缓存；`set_language` 清缓存，不串语种 |
| `_i18n_lang_clear` | `i18n.set_language` | `wrap` | 语言切换清空翻译缓存 |
| `_retry_classify_import_cache` | `retry.classify_error` | `replace` | 错误分类导入缓存（逐字节等价，去掉每次 import） |
| `_demo_always_bypassed` | `retry.retry_after_seconds` | `wrap` | 演示 `min_kernel=9.x` 被版本检查跳过（实际不生效） |

内置补丁均为 `capability`（fail-open）。任何补丁异常只记录并回退原实现。

---

## 5. 使用方式

```python
# 1) 随微内核自动激活 (app.build_kernel 已注册 KernelPatchPlugin)
mgr = kernel.require("kernel_patch")   # 其它插件可取用
mgr.status()                            # 已应用 / 跳过 / 目标清单

# 2) 独立安装/回滚
from qingxiaotuan.kernel_patch import install, get_manager
install(kernel)                         # 绑定 kernel 并应用全部补丁
get_manager().revert("_i18n_t_memo")    # 回滚单个
get_manager().revert_all()              # 全部回滚, 还原现场

# 3) 第三方声明新补丁
from qingxiaotuan.kernel_patch import patch_impl
@patch_impl("qingxiaotuan.core.retry.retry_after_seconds", priority=5, min_kernel="0.2.0")
def _mine(original, exc):
    """给 retry_after_seconds 加自定义逻辑。"""
    return original(exc)
```

---

## 6. 兼容性与回归

- **不动原实现**：全部通过 wrapper 置换（保存原引用），`revert_all()` 后与未补丁
  前逐字节一致。
- **事件兼容**：`patch.*` 事件挂到现有 `MiddlewareEventBus`，被审计/观测正常消费；
  不影响既有 `security-audit.jsonl` 等。
- **启动成本**：`kernel_patch` 采用惰性 import（`app._kernel_patch_plugin()`），
  纯 CLI 冷启动不额外加载补丁层。
- **测试**：需验证 `i18n.t`、`retry.classify_error` 在补丁后行为与原实现一致，
  且 `set_language` 后缓存不串语种。