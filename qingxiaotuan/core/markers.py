"""共享的「任务完成」判定标记 —— 让 DevLoop / 后台运行 / 多 Agent 协作等

所有自主循环对"模型何时算收尾"有一致的语义, 避免某处漏判导致任务提前假结束

或永不收敛。

判定策略: 模型在回复里显式写出下列任一标记即视为"声明完成"。这些标记是中文

环境下与模型约定好的交付信号, 不依赖特定 provider。

"""

from __future__ import annotations



# 模型声明"我完成了"的标记集合 (统一来源, 各处引用同一份)

DONE_MARKERS = (

    "【已完成】",

    "✅ 完成",

    "功能已完成",

    "任务完成",

    "DONE:",

    "TASK_DONE",

)





def is_done(text: str) -> bool:

    """给定一个模型回复, 判断是否包含完成标记。空文本安全返回 False。"""

    if not text:

        return False

    return any(m in text for m in DONE_MARKERS)
