"""子 Agent 进程级沙箱 worker 入口 (内部模块, 由 SubAgentPool 以子进程方式拉起)。

协议 (全部走临时文件, 避免 stdout 与模型流式输出混流):
- 命令行: python -m qingxiaotuan.core._sandbox_entry <req_path> <resp_path>
- req_path: JSON, 含 {profile, task, workspace(沙箱目录), exclude_tools, timeout,
            yolo, max_iterations, model_overrides, qxt_home}
- worker: 在沙箱目录里 build_kernel + create_agent + run(task),
  把 SubResult 序列化为 JSON 写 resp_path, 并以退出码 0 结束 (异常则写错误 JSON, 退出码 1)。

设计意图: 子 Agent 的工具调用 (shell/写文件/web) 全发生在沙箱子进程内,
沙箱是主工作区的临时副本, 子进程结束后沙箱可被清理, **主仓库与世界状态不被回写**。
"""

from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path
from typing import Any, Dict


def _main() -> int:
    if len(sys.argv) < 3:
        sys.stderr.write("usage: _sandbox_entry <req_path> <resp_path>\n")
        return 2
    req_path = Path(sys.argv[1])
    resp_path = Path(sys.argv[2])

    req: Dict[str, Any] = {}
    try:
        req = json.loads(req_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE401
        resp_path.write_text(json.dumps({"ok": False, "error": f"读取请求失败: {exc}"}, ensure_ascii=False), encoding="utf-8")
        return 1

    # 把 qxt_home 注入环境, 让子进程复用与主进程相同的家目录/配置
    qxt_home = req.get("qxt_home")
    if qxt_home:
        import os
        os.environ["QXT_HOME"] = qxt_home

    # 延迟导入, 避免在主进程 import 该模块时就触发重型依赖
    try:
        from ..app import build_kernel, create_agent
        from .agent import Agent  # noqa: F401 - 确保 Agent 可用
    except Exception as exc:  # noqa: BLE401
        resp_path.write_text(json.dumps(
            {"ok": False, "error": f"导入失败: {exc}\n{traceback.format_exc()}"}, ensure_ascii=False
        ), encoding="utf-8")
        return 1

    try:
        profile = req.get("profile", "default")
        kernel = build_kernel(profile=profile)
        config = kernel.require("config")
        # 模型覆盖 (若主进程指定)
        for k, v in (req.get("model_overrides") or {}).items():
            config.set_user(k, v)
        workspace = req["workspace"]
        yolo = bool(req.get("yolo", False))
        confirm = (lambda _p: True) if yolo else (lambda _p: False)
        agent = create_agent(
            kernel, workspace, confirm=confirm,
            exclude_tools=tuple(req.get("exclude_tools", [])),
        )
        if yolo and agent.ctx.on_auto_approve is None:
            agent.ctx.on_auto_approve = lambda _n: None

        out = agent.run(req["task"], stream=False)
        captured: list = []
        run_err = None
        ok = True
        if (out or "").startswith("[模型错误]"):
            ok = False
            run_err = out
        result = {
            "ok": ok,
            "output": out or "",
            "turns": agent.turn_count,
            "error": run_err,
            "tool_calls": captured,
        }
        resp_path.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
        return 0
    except Exception as exc:  # noqa: BLE401
        resp_path.write_text(json.dumps(
            {"ok": False, "error": f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}"},
            ensure_ascii=False,
        ), encoding="utf-8")
        return 1


if __name__ == "__main__":
    raise SystemExit(_main())
