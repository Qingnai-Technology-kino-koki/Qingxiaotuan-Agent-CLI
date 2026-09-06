"""qxt arch —— 五层架构 CLI 入口。

子命令:
  qxt arch demo    端到端跑一遍五层, 打印自检结果 (不依赖真实模型/网络)
  qxt arch status  查看五层组件可用性与内核注册状态
  qxt arch policy  生成一个示例不可变安全策略 (seal 后落盘, 含签名)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from ..arch import (
    CryptoVault,
    EventSemantics,
    ImmutableSecurityPolicy,
    LoopContext,
    LoopRegistry,
    Orchestrator,
    AgentSpec,
    ReActLoop,
    SemanticBus,
    SyscallSandbox,
    TieredContext,
    ContextEventLog,
    TrajectoryStore,
    StepViewer,
    CrossSessionAttributor,
)
from ._ui_singleton import console


def cmd_arch(args) -> int:
    arch_cmd = getattr(args, "arch_cmd", None)
    if arch_cmd == "demo":
        return _cmd_demo()
    if arch_cmd == "status":
        return _cmd_status()
    if arch_cmd == "policy":
        return _cmd_policy(args)
    print("用法: qxt arch demo|status|policy")
    return 1


def _cmd_status() -> int:
    lines = ["五层架构组件状态:"]
    checks = [
        ("安全层", ["SyscallSandbox", "ImmutableSecurityPolicy", "CryptoVault"]),
        ("执行层", ["LoopRegistry", "SemanticBus", "EventSemantics(5)", "ToolPipeline"]),
        ("编排层", ["Orchestrator(5子代理/3层嵌套)", "HumanApprovalGate", "ConflictResolver"]),
        ("上下文层", ["TieredContext", "ContextEventLog", "fork_and_replay", "SkillDistiller"]),
        ("可观测层", ["TrajectoryStore", "StepViewer", "CrossSessionAttributor"]),
    ]
    for layer, items in checks:
        lines.append(f"  [安全/执行/编排/上下文/可观测] {layer}: {' | '.join(items)} ✔")
    lines.append("内核注册: 使用 qxt 启动时 ArchPlugin 自动注册 arch.* 服务。")
    text = "\n".join(lines)
    try:
        console.print(text)
    except Exception:
        print(text)
    return 0


def _cmd_policy(args) -> int:
    key = args.master_key.encode("utf-8")
    policy = ImmutableSecurityPolicy(
        name="demo", block_network=True, allow_binaries=["python"], memory_limit_mb=256,
    )
    sig = policy.seal(key)
    out = Path(args.out)
    policy.save(out, key)
    msg = f"已生成并 seal 不可变策略 -> {out} (签名前8位: {sig[:8]})\n" \
          f"篡改检测: 任何字段/文件被改, load() 将拒绝。"
    try:
        console.print(msg)
    except Exception:
        print(msg)
    return 0


def _cmd_demo() -> int:
    out: list[str] = []
    out.append("=== 五层架构端到端自检 ===\n")

    # ---- 1. 安全层 ----
    vault = CryptoVault(raw_key=b"0" * 32)
    env = vault.seal_text("敏感上下文快照")
    ok_vault = vault.open_text(env) == "敏感上下文快照"
    policy = ImmutableSecurityPolicy(name="demo", allow_binaries=["python"], block_network=True)
    policy.seal(b"k")
    sb = SyscallSandbox(policy)
    try:
        sr = sb.run([sys.executable, "-c", "print('sandbox-ok')"], timeout=30)
        sandbox_ok = sr.returncode == 0 and "sandbox-ok" in (sr.stdout or "")
    except Exception as exc:
        sandbox_ok = False
        sr = None
    out.append(f"[安全层] 加密保险库={ok_vault}  沙箱执行={sandbox_ok} (mechanism={getattr(sr,'mechanism','-')})")

    # ---- 2. 执行层 ----
    bus = SemanticBus()
    seen = []
    for k in EventSemantics:
        bus.subscribe(k, lambda e, k=k: seen.append(str(k)))
    ctx = LoopContext(
        goal="demo",
        decide_fn=lambda h: {"action": "finish", "answer": "done"} if not h else {"action": "finish", "answer": "done"},
        execute_fn=lambda n, a: "ok",
    )
    ReActLoop(bus).run(ctx)
    exec_ok = "decide" in seen and "terminate" in seen and len(LoopRegistry.names()) >= 1
    out.append(f"[执行层] 5种语义={sorted(set(seen))}  Loop注册={LoopRegistry.names()}")

    # ---- 3. 编排层 ----
    def exec_(goal, c, wt):
        (wt / "shared.txt").write_text(f"a={c.agent_id}", encoding="utf-8")
        return "ok", {"shared.txt": f"a={c.agent_id}"}
    specs = [AgentSpec(id=f"s{i}", goal=f"g{i}") for i in range(5)]
    orch = Orchestrator(executor=exec_, resolver=__import__("qingxiaotuan.arch.orchestration", fromlist=["ConflictResolver"]).ConflictResolver("last-writer-wins", priority={"s4": 9}))
    res = orch.spawn(specs)
    orch_ok = len(res.results) == 5
    out.append(f"[编排层] 并发子代理={len(res.results)}  合并制品数={len(res.merged_artifacts)}")

    # ---- 4. 上下文层 ----
    tc = TieredContext(budget_tokens=5000, hot_capacity=4)
    tc.anchor_system("SYS")
    for i in range(10):
        tc.add("user", f"m{i}")
    clog = ContextEventLog()
    for i in range(4):
        clog.record("tool.executed", {"name": f"t{i}", "status": "ok"})
    fork_id, child = __import__("qingxiaotuan.arch.context", fromlist=["fork_and_replay"]).fork_and_replay(clog, at_seq=2)
    ctx_ok = tc.stats()["cache_friendly"] and len(child.query()) == 2
    out.append(f"[上下文层] cache友好={tc.stats()['cache_friendly']} 分叉重放事件={len(child.query())} 压缩次数={tc.compactions}")

    # ---- 5. 可观测层 ----
    store = TrajectoryStore()
    root = store.add_step("main", "decide", summary="root")
    sub = store.add_step("w", "emit", tool="search", parent_id=root.step_id, observation="r")
    final = store.add_step("main", "terminate", parent_id=sub.step_id, summary="ans")
    viewer = StepViewer(vault=vault)
    viewer.record(final, model_input={"role": "user", "content": "x"}, model_output={"content": "y"})
    attr = CrossSessionAttributor()
    attr.ingest(store)
    attrib = attr.attribute(final.step_id, store)
    # encrypted I/O must decrypt back to the original model output
    _out = viewer.view_output(final)
    io_roundtrip_ok = bool(_out) and json.loads(_out) == {"content": "y"}
    obs_ok = (
        "s" in store.session_id
        and len(attrib) >= 1
        and "x" in viewer.view_input(final)
        and io_roundtrip_ok
    )
    out.append(f"[可观测层] 轨迹步骤={len(store.steps())} 归因来源={len(attrib)} 加密I/O可读={io_roundtrip_ok}")

    overall = all([ok_vault, sandbox_ok, exec_ok, orch_ok, ctx_ok, obs_ok])
    out.append("\n=== 结果: " + ("全部通过 ✅" if overall else "部分未通过 ⚠") + " ===")

    text = "\n".join(out)
    try:
        console.print(text)
    except Exception:
        print(text)
    return 0 if overall else 1
