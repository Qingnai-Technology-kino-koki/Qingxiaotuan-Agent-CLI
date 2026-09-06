#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""版本政策强制检查器 (docs/VERSION_POLICY.md 的可执行化)。

把"写在文档里"的版本纪律变成 CI 会拦下来的硬约束:

  1. **一致性**: pyproject.toml 的 version 与 qingxiaotuan/__init__.py 的
     __version__ 必须完全相同(当前二者曾长期不同步)。
  2. **递增上限**: 相对基线版本, 增幅不得超过 +0.0.001, 且不允许回退。
     版本形如 MAJOR.MINOR.PPP (补丁段最多三位), 递增单位即 0.0.001。
  3. **CHANGELOG 对齐**: 最新版本号必须与 CHANGELOG.md 顶层条目一致。

用法:
    python scripts/check_version_policy.py                 # 基线取 git HEAD 版本
    python scripts/check_version_policy.py --base 0.2.013  # 显式指定基线
    python scripts/check_version_policy.py --current 0.2.014

退出码: 0 = 通过; 1 = 违反政策; 2 = 无法判定(环境问题)。
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path
from typing import Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = REPO_ROOT / "pyproject.toml"
INIT_PY = REPO_ROOT / "qingxiaotuan" / "__init__.py"
CHANGELOG = REPO_ROOT / "CHANGELOG.md"

# 版本形如 0.2.013 —— 补丁段允许 1~3 位
VERSION_RE = re.compile(r"^\s*(\d+)\.(\d+)\.(\d{1,3})\s*$")
MAX_BUMP_UNITS = 1  # 政策: 每次迭代最多 +0.0.001


class VersionError(Exception):
    """版本政策违反。"""


def parse_version(text: str) -> Tuple[int, int, int]:
    m = VERSION_RE.match(text.strip().strip('"').strip("'"))
    if not m:
        raise VersionError(f"无法解析版本号: {text!r} (期望形如 0.2.014)")
    return int(m.group(1)), int(m.group(2)), int(m.group(3))


def to_units(v: Tuple[int, int, int]) -> int:
    """把版本折算成以 0.0.001 为单位的整数, 便于比较增幅。"""
    major, minor, patch = v
    return major * 1_000_000 + minor * 1_000 + patch


def read_pyproject_version() -> Optional[str]:
    if not PYPROJECT.exists():
        return None
    for line in PYPROJECT.read_text(encoding="utf-8").splitlines():
        m = re.match(r'^\s*version\s*=\s*["\']([^"\']+)["\']', line)
        if m:
            return m.group(1)
    return None


def read_init_version() -> Optional[str]:
    if not INIT_PY.exists():
        return None
    m = re.search(
        r'^__version__\s*=\s*["\']([^"\']+)["\']',
        INIT_PY.read_text(encoding="utf-8"),
        re.M,
    )
    return m.group(1) if m else None


def read_changelog_top_version() -> Optional[str]:
    if not CHANGELOG.exists():
        return None
    m = re.search(
        r"^##\s*\[([0-9]+\.[0-9]+\.[0-9]{1,3})\]",
        CHANGELOG.read_text(encoding="utf-8"),
        re.M,
    )
    return m.group(1) if m else None


def git_show_head_version() -> Optional[str]:
    """取 git HEAD 处 pyproject.toml 里的版本作为基线。"""
    try:
        out = subprocess.run(
            ["git", "show", "HEAD:pyproject.toml"],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=15,
        )
    except Exception:
        return None
    if out.returncode != 0:
        return None
    for line in out.stdout.splitlines():
        m = re.match(r'^\s*version\s*=\s*["\']([^"\']+)["\']', line)
        if m:
            return m.group(1)
    return None


def check(current: str, base: Optional[str], changelog_top: Optional[str]) -> list[str]:
    """返回问题列表(空列表 = 通过)。"""
    problems: list[str] = []
    cur_v = parse_version(current)

    # 规则 2: 递增上限 / 禁止回退
    if base:
        base_v = parse_version(base)
        delta = to_units(cur_v) - to_units(base_v)
        if delta < 0:
            problems.append(
                f"版本回退: {base} -> {current} (政策禁止降级)"
            )
        elif delta == 0:
            problems.append(
                f"版本未递增: 仍为 {current} (每次迭代需 +0.0.001)"
            )
        elif delta > MAX_BUMP_UNITS:
            problems.append(
                f"增幅超限: {base} -> {current} = +0.0.{delta:03d}, "
                f"政策上限为 +0.0.001 (超出 {delta - MAX_BUMP_UNITS} 个单位)"
            )

    # 规则 3: CHANGELOG 对齐
    if changelog_top:
        if parse_version(changelog_top) != cur_v:
            problems.append(
                f"CHANGELOG 顶层条目 [{changelog_top}] 与当前版本 {current} 不一致"
            )

    return problems


def main() -> int:
    ap = argparse.ArgumentParser(description="检查版本政策合规性")
    ap.add_argument("--base", help="基线版本(默认取 git HEAD 的 pyproject 版本)")
    ap.add_argument("--current", help="当前版本(默认取 pyproject.toml)")
    args = ap.parse_args()

    pyproject_v = read_pyproject_version()
    init_v = read_init_version()

    if pyproject_v is None:
        print("[FAIL] 无法从 pyproject.toml 读取版本", file=sys.stderr)
        return 2

    # 规则 1: 两处版本号必须一致
    problems: list[str] = []
    if init_v is None:
        problems.append("无法从 qingxiaotuan/__init__.py 读取 __version__")
    elif parse_version(init_v) != parse_version(pyproject_v):
        problems.append(
            f"版本号不一致: pyproject.toml={pyproject_v} vs "
            f"qingxiaotuan/__init__.py={init_v}"
        )

    current = args.current or pyproject_v
    base = args.base or git_show_head_version()
    problems += check(current, base, read_changelog_top_version())

    print("版本政策检查")
    print(f"  当前版本 : {current}")
    print(f"  基线版本 : {base or '(未提供)'}")
    print(f"  递增上限 : +0.0.001")
    print()

    if problems:
        for p in problems:
            print(f"  [FAIL] {p}", file=sys.stderr)
        print()
        print("版本政策违反, 详见 docs/VERSION_POLICY.md", file=sys.stderr)
        return 1

    print("  [OK] 版本政策合规")
    return 0


if __name__ == "__main__":
    sys.exit(main())
