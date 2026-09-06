"""qxt harden —— 安全加固工具集命令行入口。

子命令
------
    qxt harden audit-export [--format cef|jsonl|syslog[,...]] [--from PATH] [--out DIR] [--push URL]
    qxt harden sbom [--out sbom.spdx.json]
    qxt harden crypto-provider
    qxt harden network-check --command "..." [--egress-cidr 10.0.0.0/8] [--one-way]
    qxt harden repro-lock [--out requirements.lock] [--verify]

这些命令不声称"国防级/军工认证"; 它们把代码层「可对接、可验证、可审计」的安全能力做实。
"""
from __future__ import annotations

import sys
from pathlib import Path

from ..harden import (
    AuditExporter,
    EgressRestrictedNetworkGuard,
    get_crypto_provider,
    available_providers,
    write_sbom,
    generate_sbom,
    generate_lock,
    verify_lock,
)
from ..core.security_bus import SecurityEvent


def _resolve_bus_log() -> Path:
    """解析安全事件默认落盘路径: 优先已存在且非空的候选文件, 否则回退到主路径。

    候选顺序 (同一 home 目录下两种文件名都覆盖):
      1. <home>/security-audit.jsonl        (交互式会话默认)
      2. <home>/audit/security_events.jsonl (内核安全插件默认)
    """
    try:
        from ..config import home_dir
        home = Path(home_dir())
    except Exception:
        home = Path.home() / ".qingxiaotuan"
    candidates = [
        home / "security-audit.jsonl",
        home / "audit" / "security_events.jsonl",
    ]
    for p in candidates:
        try:
            if p.exists() and p.stat().st_size > 0:
                return p
        except OSError:
            continue
    # 都没有则回退到主路径, 由调用方给出明确报错
    return candidates[0]


def _audit_export(args) -> int:
    fmts = []
    for part in (args.format or "jsonl").split(","):
        part = part.strip()
        if part:
            fmts.append(part)
    out_dir = args.out if getattr(args, "out", None) else None
    push = getattr(args, "push", None)
    src = getattr(args, "from_", None) or _resolve_bus_log()

    exporter = AuditExporter(formats=fmts, out_dir=out_dir, push_url=push)
    if not Path(src).exists():
        sys.stderr.write(
            f"[harden] 未找到安全事件源: {src}\n"
            f"        可加 --from PATH 指定 bus JSONL, 或先在一次会话中触发安全事件。\n"
        )
        return 1
    n = exporter.export_file(str(src))
    exporter.close()
    dest = out_dir or "stdout"
    print(f"[harden] 已导出 {n} 条安全事件 -> {dest} (格式: {','.join(fmts)})"
          + (f", 推送: {push}" if push else ""))
    return 0


def _sbom(args) -> int:
    out = getattr(args, "out", None) or "sbom.spdx.json"
    data = generate_sbom()
    path = write_sbom(out)
    print(f"[harden] SBOM 已生成: {path}")
    print(f"         组件数: {len(data['packages'])} (含根组件), 依赖关系: {len(data['relationships'])}")
    print(f"         内容指纹: {__import__('qingxiaotuan.harden.sbom', fromlist=['sbom_hash']).sbom_hash(data)}")
    return 0


def _crypto_provider(args) -> int:
    avail = available_providers()
    active = get_crypto_provider("auto").name
    print("[harden] 可用密码学后端:")
    for name in ("software", "gmssl", "hsm"):
        mark = "✓" if name in avail else "✗ (依赖缺失)"
        tag = " <- 默认激活" if name == active else ""
        print(f"  - {name:8s} {mark}{tag}")
    print("\n说明: software=本地 AES-GCM(优先)/CTR-HMAC(回退); gmssl=国密 SM4(需 pip install gmssl);")
    print("      hsm=硬件 HSM/PKCS#11(需实体设备与 python-pkcs11)。以上仅为算法/密钥后端可替换,")
    print("      不等同于国密产品认证或等保资质 —— 那是代码之外的合规流程。")
    return 0


def _network_check(args) -> int:
    command = getattr(args, "command", None)
    if not command:
        sys.stderr.write("[harden] 请通过 --command \"...\" 传入待评估命令\n")
        return 1
    cidrs = list(getattr(args, "egress_cidr", None) or [])
    one_way = bool(getattr(args, "one_way", False))
    guard = EgressRestrictedNetworkGuard(egress_cidr_allow=cidrs, one_way_mode=one_way)
    decision = guard.check(command)
    print("[harden] 网络策略:")
    print("  " + str(guard.describe_policy()).replace(", ", ",\n   "))
    print(f"\n[harden] 命令: {command}")
    print(f"  裁决: action={decision.action} risk={decision.risk_level}")
    for r in decision.reasons:
        print(f"   - {r}")
    return 0 if decision.action != "deny" else 0  # 仅展示, 退出码 0


def _repro_lock(args) -> int:
    if getattr(args, "verify", False):
        ok, diff = verify_lock(getattr(args, "out", None) or "requirements.lock")
        if ok:
            print("[harden] 依赖与锁文件一致, 无漂移。")
            return 0
        print("[harden] 检测到依赖漂移:")
        for d in diff:
            print("  - " + d)
        return 1
    path = generate_lock(getattr(args, "out", None) or "requirements.lock")
    print(f"[harden] 可复现锁已生成: {path}")
    return 0


def _status(args) -> int:
    """展示当前加固态势: 加密后端 / 审计落盘 / SBOM / 网络策略。"""
    from ..harden import available_providers, get_crypto_provider
    from ..harden.sbom import generate_sbom

    avail = available_providers()
    active = get_crypto_provider("auto").name
    print("[harden] 加密后端:")
    print(f"  默认激活: {active}")
    for name in ("software", "gmssl", "hsm"):
        mark = "✓ 可用" if name in avail else "✗ 依赖缺失/不适用"
        print(f"  - {name:8s} {mark}")
    print("  (注: hsm 不适用于流式审计日志, 需实体设备且密钥不出卡; 选 hsm 时自动降级 software 并告警)")

    # 审计事件落盘
    try:
        from ..config import home_dir
        home = Path(home_dir())
    except Exception:
        home = Path.home() / ".qingxiaotuan"
    for cand in (home / "security-audit.jsonl", home / "audit" / "security_events.jsonl"):
        if cand.exists():
            print(f"[harden] 安全事件落盘: {cand} ({cand.stat().st_size} 字节)")
            break
    else:
        print(f"[harden] 安全事件落盘: 暂无 (默认路径: {home}/security-audit.jsonl 或 {home}/audit/security_events.jsonl)")

    # SBOM
    try:
        data = generate_sbom()
        print(f"[harden] SBOM: 可生成, 组件 {len(data['packages'])} 个")
    except Exception as exc:  # noqa: BLE001
        print(f"[harden] SBOM: 生成失败 ({exc})")

    # 网络策略 (尽力读取配置, 失败不阻塞)
    try:
        from ..config import Config
        cfg = Config()
        net = cfg.get("security.network", {}) or {}
        cidrs = net.get("egress_cidr_allow", []) or []
        one_way = bool(net.get("one_way_mode", False))
        print(f"[harden] 网络策略: 出口CIDR白名单={cidrs or '未配置(不限制)'}, 单向模式={one_way}")
    except Exception:
        print("[harden] 网络策略: 未加载到配置 (可用 qxt harden network-check 单独评估)")
    return 0


def cmd_harden(args) -> int:
    cmd = getattr(args, "harden_cmd", None)
    if cmd == "audit-export":
        return _audit_export(args)
    if cmd == "sbom":
        return _sbom(args)
    if cmd == "crypto-provider":
        return _crypto_provider(args)
    if cmd == "network-check":
        return _network_check(args)
    if cmd == "repro-lock":
        return _repro_lock(args)
    if cmd == "status":
        return _status(args)
    sys.stderr.write(
        "用法: qxt harden <audit-export|sbom|crypto-provider|network-check|repro-lock|status>\n"
    )
    return 1
