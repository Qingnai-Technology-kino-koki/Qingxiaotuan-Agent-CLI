"""系统提示词组装 —— SOUL 身份 + 用户画像 + 长期记忆 + 可复用技能。

设计原则 (对标 Claude Code 2.1.214 + 缓存友好):
- **系统提示前缀必须绝对稳定**: 同一工作区、同一会话内, system 字符串逐字节不变,
  才能最大化 DeepSeek / OpenAI 兼容端点的 prompt cache 命中 (对标 dsh 的 99% 水平)。
- 因此「随任务变化的语义召回」(记忆检索 / 技能检索) **不进 system**, 改由
  build_task_context() 生成一段「任务上下文」, 贴在首条 user 消息前。system 只含
  稳定段: SOUL + 环境 + QXT.md 项目指令 + 用户画像 + 长期记忆全量(限长) + 代码库地图 + 准则。
- 工具说明不进 system (function schema 自带), 避免重复。
- 文案极简, 不堆客套话, 每条都有信息量。

缓存命中关键: 本模块的 build_system_prompt 不接收 task_hint —— 见 agent._build_system。

Claude Code 2.1.214 对标:
- QXT.md 项目级指令发现 (对标 CLAUDE.md): 自动发现并合并工作区内的 QXT.md。
- 系统上下文注入: OS / Shell / Python 版本 / Git 状态, 动态注入系统提示。
"""

from __future__ import annotations

import hashlib
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from typing import List, Optional


# 项目级指令文件名 (对标 Claude Code 的 CLAUDE.md)
_INSTRUCTION_FILES = ["QXT.md", "CLAUDE.md", ".qxt.md"]


def _discover_project_instructions(workspace: str) -> str:
    """发现并合并项目级指令文件 (对标 Claude Code 的 CLAUDE.md 发现)。

    搜索顺序: QXT.md > CLAUDE.md > .qxt.md (工作区根目录)
    多个文件按优先级合并, 保证项目特定指令覆盖全局。
    """
    ws = Path(workspace)
    if not ws.is_dir():
        return ""
    parts: List[str] = []
    for name in _INSTRUCTION_FILES:
        path = ws / name
        if path.exists() and path.is_file():
            try:
                content = path.read_text(encoding="utf-8", errors="replace").strip()
                if content:
                    parts.append(f"# 项目指令 ({name})\n{content}")
            except OSError:
                pass
    return "\n\n".join(parts)


def _get_system_context() -> str:
    """获取系统上下文信息 (对标 Claude Code 的 OS/Shell/Git 动态注入)。

    包含: OS、Shell 类型、Python 版本、Git 信息、平台架构。
    这些信息随环境变化, 但在同一会话内保持稳定。
    """
    parts: List[str] = []

    # OS 和平台
    parts.append(f"OS={platform.system()} {platform.release()}")
    parts.append(f"Platform={platform.machine()}")

    # Shell
    shell = "bash"
    if sys.platform == "win32":
        shell = "PowerShell/bash"
    parts.append(f"Shell={shell}")

    # Python 版本
    py_ver = platform.python_version()
    parts.append(f"Python={py_ver}")

    return ", ".join(parts)


def _get_git_context(workspace: str) -> str:
    """获取 Git 上下文 (分支、最近 commit、状态)。"""
    ws = Path(workspace)
    if not (ws / ".git").exists():
        return ""
    parts: List[str] = []
    # 当前分支
    try:
        branch = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=str(ws), capture_output=True, text=True, timeout=5,
            errors="replace",
        ).stdout.strip()
        if branch:
            parts.append(f"Branch={branch}")
    except Exception:
        pass
    # 最近一次 commit
    try:
        log_out = subprocess.run(
            ["git", "log", "-1", "--pretty=%h %s"],
            cwd=str(ws), capture_output=True, text=True, timeout=5,
            errors="replace",
        ).stdout.strip()
        if log_out:
            parts.append(f"LastCommit={log_out}")
    except Exception:
        pass
    # 是否有未提交改动
    try:
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(ws), capture_output=True, text=True, timeout=5,
            errors="replace",
        ).stdout.strip()
        dirty_count = len(status.splitlines()) if status else 0
        if dirty_count > 0:
            parts.append(f"DirtyFiles={dirty_count}")
        else:
            parts.append("Clean=true")
    except Exception:
        pass
    return ", ".join(parts) if parts else ""


def build_system_prompt(
    home: Path,
    workspace: str,
    memory_store=None,
    skill_manager=None,
    skill_limit: int = 3,
    codebase_map: str = "",
) -> str:
    """组装「绝对稳定」的系统提示 —— 不含任何随任务变化的语义召回。

    同工作区同 home 下, 多次调用产出逐字节一致的字符串 (便于 prompt cache 命中)。
    """
    parts: List[str] = []

    # 1. SOUL (最稳定, 放最前, 便于缓存)
    soul_path = home / "SOUL.md"
    if soul_path.exists():
        parts.append(soul_path.read_text(encoding="utf-8").strip())
    else:
        parts.append(
            "你是「青小团」, 用户终端里的 Agent。少客套多做事, 先查再问, "
            "可复用方法蒸馏成 Skill, 不确定就明说, 做错就承认。"
        )

    # 2. 运行环境 (系统上下文注入, 对标 Claude Code)
    sys_ctx = _get_system_context()
    parts.append(
        f"环境: {sys_ctx} | 工作区={workspace}\n"
        "能力: 读写文件/执行命令/抓网页/读写记忆/蒸馏技能/理解代码库。"
    )

    # 3. Git 上下文 (动态注入)
    git_ctx = _get_git_context(workspace)
    if git_ctx:
        parts.append(f"Git: {git_ctx}")

    # 4. QXT.md 项目级指令 (对标 Claude Code 的 CLAUDE.md 发现)
    project_instructions = _discover_project_instructions(workspace)
    if project_instructions:
        parts.append(project_instructions)

    # 5. 用户画像 (仅在有效内容时注入: 跳过空模板)
    if memory_store:
        user = memory_store.read_user().strip()
        if user and not _is_empty_user_template(user):
            parts.append(f"用户: {user}")

    # 6. 长期记忆: 读全量但限长 (稳定, 不随任务语义召回)
    #    记忆文件通常很小, 这里直接注入全量, 保证 system 前缀稳定可缓存。
    if memory_store and hasattr(memory_store, "read_memory"):
        raw = memory_store.read_memory().strip()
        if raw:
            if len(raw) > 1500:
                raw = raw[:1500] + "\n…(详见 memory_read)"
            parts.append(f"长期记忆:\n{raw}")

    # 7. 代码库地图 (大上下文: 钉死的全貌, 由 indexer 保证同内容同字符串)
    if codebase_map:
        parts.append(codebase_map)

    # 8. 技能: Top-N 随航 (稳定列表, 不做任务语义召回, 避免 system 抖动)
    if skill_manager:
        skills = skill_manager.list_all()[:skill_limit]
        rendered = skill_manager.render_for_prompt(skills)
        if rendered:
            parts.append(rendered)

    # 9. 行为准则 (极简, 每条有信息量)
    parts.append(
        "准则:\n"
        "1. 思考→拆解→调工具→观察→继续, 直到完成。\n"
        "2. 改代码前先摸清结构 (codebase_map/find_symbol/read_file/git_status), 注意跨文件依赖。\n"
        "3. 改完自测: 有测试就 run_tests, 红了自己修到绿。\n"
        "4. 值得长期记的事实用 memory_write 存。\n"
        "5. 可复用方法用 skill_save 蒸馏; 有相似技能先复用改进。\n"
        "6. 中文回答, 简洁直接; 不确定如实说。\n"
        "7. 破坏性操作前必须确认 (YOLO 模式除外)。\n"
        "8. mcp__ 前缀工具来自外部 MCP Server, 像本地工具一样调用。\n"
        "9. plan 模式下只分析不修改文件, 等用户确认后再执行。"
    )
    return "\n\n".join(parts)


def build_task_context(
    task_hint: str,
    memory_store=None,
    skill_manager=None,
    skill_limit: int = 3,
) -> str:
    """生成「随任务变化的上下文」, 贴在首条 user 消息前 (不进 system, 不破坏缓存)。

    包含: 与当前任务相关的长期记忆语义召回 + 相关技能召回。
    无相关内容时返回空串, 调用方据此决定是否追加。
    """
    blocks: List[str] = []
    if memory_store and hasattr(memory_store, "search") and task_hint:
        try:
            hits = memory_store.search(task_hint, limit=5)
        except Exception:
            hits = []
        if hits:
            mem = "\n".join(f"- {h['content']}" for h in hits)
            blocks.append(f"[与任务相关的记忆]\n{mem}")
    if skill_manager and task_hint:
        try:
            skills = skill_manager.search(task_hint, limit=skill_limit)
        except Exception:
            skills = []
        rendered = skill_manager.render_for_prompt(skills) if skills else ""
        if rendered:
            blocks.append(rendered)
    return "\n\n".join(blocks)


def is_short_task(task_hint: str, max_chars: int = 160) -> bool:
    """判断任务是否适合跳过额外语义上下文。"""
    text = "".join(task_hint.split())
    return 0 < len(text) <= max_chars and not any(
        marker in text.lower()
        for marker in ("实现", "修复", "重构", "测试", "implement", "refactor", "fix")
    )


def system_prompt_hash(system_prompt: str) -> str:
    """返回 system 提示的短哈希, 用于断言「同会话前缀稳定」(缓存命中自检)。"""
    return hashlib.sha1(system_prompt.encode("utf-8")).hexdigest()[:16]


def _is_empty_user_template(user_text: str) -> bool:
    """判断 USER.md 是否仍是未填写的空模板 (避免把空模板当有效画像注入)。

    用「去空行/去标记后无实质内容」判定, 不依赖硬编码的魔法字符串,
    即使模板文案微调也不会失效。
    """
    import re

    stripped = re.sub(r"[#\-\*\s:]", "", user_text)
    # 去掉模板里常见的示例字段名
    stripped = re.sub(r"(Name|City|Notes|关于用户)", "", stripped, flags=re.IGNORECASE)
    return stripped.strip() == ""
