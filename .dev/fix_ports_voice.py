#!/usr/bin/env python3
"""将 qingxiaotuan/ports/ 互操作层的 "Port of upstream X.ts" 导语改写为
青小团自研实现（协议/接口对齐）风格 —— 语义不变, 去掉 "移植/copy" 自贬口吻。

仅重写模块 docstring 首段的移植措辞, 保留技术事实与 NOTICE 归属引用。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TARGETS = list((ROOT / "qingxiaotuan" / "ports").rglob("*.py"))

# 顺序执行的首行/首段改写规则: (匹配子串, 替换子串, 计数)
RULES = [
    # ---- 模块级导语 ----
    ('Port of the upstream ``kaos`` package (attribution: see NOTICE) (pure-logic, dependency-light subset).',
     '自研实现 (对齐上游 ``kaos`` 的接口语义; 归属见 NOTICE) —— 纯逻辑、零依赖子集。'),
    ('Port of the upstream transcript package to idiomatic Python 3.11 (stdlib only).',
     '自研实现 (对齐上游 transcript 协议; 纯 stdlib, Python 3.11 惯用写法)。'),
    ('Python port of the Kimi OAuth package\'s pure-logic, dependency-light surface.',
     '自研实现 (对齐上游 OAuth 的纯逻辑、轻依赖接口面)。'),
    ('Port of the legacy migration utilities (Python stdlib).',
     '自研实现 (对齐上游 legacy 迁移工具; 纯 Python stdlib)。'),
    ('Idiomatic Python 3.11 port of the upstream telemetry package (see NOTICE).',
     '自研实现 (对齐上游 telemetry 协议; Python 3.11 惯用写法, 归属见 NOTICE)。'),
    # ---- 逐文件 "port of X.ts" 短导语 ----
    ('port of view/registry.ts)', '对齐 view/registry.ts 接口)'),
    ('(port of store/agentTranscript.ts + store/transcriptStore.ts)',
     '(对齐上游 store/agentTranscript + store/transcriptStore 的数据格式)'),
    ('port of contract/schema.ts)', '对齐上游 contract/schema 的数据格式)'),
    ('Turn pagination (port of pagination/paginate.ts).',
     'Turn 分页 (对齐上游 paginate 的分页语义)。'),
    ('port of ops/operation.ts)', '对齐上游 ops/operation 的语义)'),
    ('port of model/*)', '对齐上游 model/* 的类型/序列化)'),
    ('port of contract/mediaRef.ts)', '对齐上游 contract/mediaRef 的语义)'),
    ('port of model/ids.ts)', '对齐上游 model/ids 的类型)'),
    ('port of history/groupTurns.ts + history/foldFacts.ts)',
     '对齐上游 history 分组/折叠语义)'),
    ('port of granularity/*)', '对齐上游 granularity 分级语义)'),
    ('(port of contract/events.ts)', '(对齐上游 contract/events 的线协议)'),
    ('port of ops/apply.ts)', '对齐上游 ops/apply 的语义)'),
    # ---- telemetry 各文件 "reference implementation" 措辞 ----
    ('Pure, dependency-free port of the upstream TypeScript reference implementation.',
     '自研实现 (对齐上游线协议; 零依赖, 纯逻辑)。'),
    ('Pure, dependency-free port of the node-independent helpers in',
     '自研实现 (node 无关的纯逻辑辅助, 对齐上游线协议; 零依赖。'),
    ('Dependency-free port of the upstream TypeScript reference implementation.',
     '自研实现 (对齐上游线协议; 零依赖)。'),
    # ---- kaos 各文件 "Port of ``X.ts``" ----
    ('Port of ``shell-path-bridge.ts``.', '自研实现 (对齐上游 shell-path-bridge 语义)。'),
    ('Port of ``login-shell-path.ts``.', '自研实现 (对齐上游 login-shell-path 语义)。'),
    ('Port of ``kaos.ts``.', '自研实现 (对齐上游 kaos 接口语义)。'),
    ('Port of ``environment.ts``.', '自研实现 (对齐上游 environment 探测语义)。'),
    ('Port of ``current.ts``.', '自研实现 (对齐上游 current 语义)。'),
]

# 兜底的正则: 通用 "port of / Port of X.ts / port of the upstream ... 实现类" 措辞
GENERIC = [
    (re.compile(r'\b(Port of|port of)\b', re.IGNORECASE), '自研实现 (接口对齐)'),
    (re.compile(r'Python port of the '), '自研实现 (接口对齐上游 '),
    (re.compile(r'port of the upstream TypeScript reference implementation'), '自研实现 (接口对齐上游线协议)'),
]


def main() -> int:
    changed = 0
    unresolved = []
    for path in TARGETS:
        text = path.read_text(encoding="utf-8")
        orig = text
        for old, new in RULES:
            text = text.replace(old, new)
        # 兜底正则只作用于仍含 "port of / Port of" 的残留语句
        if re.search(r'\b[pP]ort of\b', text):
            tmp = text
            for pat, rep in GENERIC:
                tmp = pat.sub(rep, tmp)
            if tmp != text:
                text = tmp
        if text != orig:
            path.write_text(text, encoding="utf-8")
            changed += 1
            # 报告中仍残留的 small-port 词 (纯技术性 .ts 文件名引用保留)
            rest = [ln.strip() for ln in text.splitlines() if re.search(r'\b[pP]ort of\b', ln)]
            if rest:
                unresolved.append((str(path.relative_to(ROOT)), rest[:2]))
        else:
            if re.search(r'\b[pP]ort of\b', text):
                unresolved.append((str(path.relative_to(ROOT)), []))
    print(f"改写文件数: {changed}")
    if unresolved:
        print("仍含 'port of' 的行 (请人工确认是否为必要的 TS 文件名技术引用):")
        for f, lines in unresolved:
            print(f"  {f}:")
            for ln in lines:
                print(f"      {ln}")
    return 0 if not unresolved else 1


if __name__ == "__main__":
    sys.exit(main())