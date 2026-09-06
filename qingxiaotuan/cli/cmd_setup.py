"""初始化向导 + 基准测试 CLI 命令。

拆分自 commands.py:
- cmd_setup: 交互式初始化向导
- cmd_bench: 基准测试 (cache/latency)
- cmd_open: 精确引用跳转
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

from ..config import Config, home_dir
from ..models import PROVIDER_PRESETS
from ..models.provider_catalog import (
    ALL_PROVIDERS, get_provider,
)
from ..logging_conf import log
from ..i18n import ensure_language, t
from ._ui_singleton import console


# ---- app 链惰性加载: 仅真正执行 setup/bench 时才构建内核 ----
def build_kernel(*a, **k):
    from ..app import build_kernel as _f
    return _f(*a, **k)


def create_agent(*a, **k):
    from ..app import create_agent as _f
    return _f(*a, **k)


def seed_builtin_skills(*a, **k):
    from ..app import seed_builtin_skills as _f
    return _f(*a, **k)





# ===================================================================== cmd_setup

def cmd_setup(args) -> int:
    """初始化向导。"""
    kernel = build_kernel()
    config = kernel.require("config")
    ensure_language(config)

    console.print(f"{t('setup.title')}")
    console.print(f"  1. {t('setup.opt_quick')}")
    console.print(f"  2. {t('setup.opt_full')}")
    console.print(f"  3. {t('setup.opt_blank')}")
    try:
        choice = input(t("setup.choose")).strip() or "1"
    except (EOFError, KeyboardInterrupt):
        choice = "1"

    if choice == "1":
        return _setup_quick(config)
    if choice == "2":
        return _setup_full(config)
    if choice == "3":
        return _setup_blank(config)
    console.print(f"{t('setup.wip')}")
    return 0


def _setup_quick(config) -> int:
    """快速设置: 供应商 + 模型 + API Key。"""
    console.print(f"\n{t('setup.quick_title')}")
    console.print(f"  {t('setup.providers')}")
    for i, name in enumerate(list(PROVIDER_PRESETS.keys())[:12], 1):
        p = PROVIDER_PRESETS[name]
        console.print(f"    {i:2d}. {name:<20s} {p.get('desc','')[:40]}")
    try:
        idx = int(input(t("setup.pick_number")).strip() or "1") - 1
    except (ValueError, EOFError, KeyboardInterrupt):
        idx = 0
    names = list(PROVIDER_PRESETS.keys())
    provider = names[min(idx, len(names) - 1)]
    from .cmd_chat import _pick_model_interactive, _pick_api_key_interactive
    preset = get_provider(provider) or ALL_PROVIDERS[0]
    console.print(f"\n  {t('setup.chosen', provider=provider)}")
    console.print(f"  {t('setup.gateway', url=preset.base_url)}")
    provider, model_name = _pick_model_interactive(preset)
    preset = get_provider(provider) or preset
    api_key_env = preset.api_key_env
    if api_key_env:
        try:
            key = input(t("setup.api_key_prompt", env=api_key_env)).strip()
        except (EOFError, KeyboardInterrupt):
            key = ""
        if key:
            _save_api_key(api_key_env, key)

    config.set_user("model.provider", provider)
    config.set_user("model.model", model_name)
    if preset.base_url:
        config.set_user("model.base_url", preset.base_url)
    if api_key_env:
        config.set_user("model.api_key_env", api_key_env)
    console.print(f"\n{t('setup.done', model=f'{provider}/{model_name}')}")
    return 0


def _save_api_key(env_name: str, key: str) -> None:
    """把密钥追加写入 ~/.qingxiaotuan/.env。"""
    env_path = home_dir() / ".env"
    env_path.parent.mkdir(parents=True, exist_ok=True)
    with open(env_path, "a", encoding="utf-8") as f:
        f.write(f"\n{env_name}={key}\n")
    console.print(f"  {t('setup.key_saved', path=env_path)}")


def _ask_choice(prompt: str, options: List[str], default: str) -> str:
    """通用枚举选择。"""
    try:
        raw = input(prompt).strip().lower()
    except (EOFError, KeyboardInterrupt):
        return default
    if not raw:
        return default
    return raw if raw in options else default


def _setup_full(config) -> int:
    """完整设置: 供应商/模型/API Key + 运行模式/推理投入/温度/输出上限。"""
    console.print(f"\n{t('setup.full_title')}")
    console.print(f"  {t('setup.providers')}")
    for i, name in enumerate(list(PROVIDER_PRESETS.keys())[:12], 1):
        p = PROVIDER_PRESETS[name]
        console.print(f"    {i:2d}. {name:<20s} {p.get('desc','')[:40]}")
    try:
        idx = int(input(t("setup.pick_number")).strip() or "1") - 1
    except (ValueError, EOFError, KeyboardInterrupt):
        idx = 0
    names = list(PROVIDER_PRESETS.keys())
    provider = names[min(idx, len(names) - 1)]
    from .cmd_chat import _pick_model_interactive, _pick_api_key_interactive
    preset = get_provider(provider) or ALL_PROVIDERS[0]
    console.print(f"\n  {t('setup.chosen', provider=provider)}")
    console.print(f"  {t('setup.gateway', url=preset.base_url)}")
    provider, model_name = _pick_model_interactive(preset)
    preset = get_provider(provider) or preset
    api_key_env = preset.api_key_env
    if api_key_env:
        try:
            key = input(t("setup.api_key_prompt", env=api_key_env)).strip()
        except (EOFError, KeyboardInterrupt):
            key = ""
        if key:
            _save_api_key(api_key_env, key)

    mode = _ask_choice(t("setup.mode_prompt"), ["standard", "yolo"], "standard")
    effort = _ask_choice(t("setup.effort_prompt"), ["low", "medium", "high"], "high")
    try:
        temp = float(input(t("setup.temp_prompt")).strip() or "0.7")
        temp = max(0.0, min(2.0, temp))
    except (ValueError, EOFError, KeyboardInterrupt):
        temp = 0.7
    try:
        mt = int(input(t("setup.max_tokens_prompt")).strip() or "8192")
        mt = max(1, mt)
    except (ValueError, EOFError, KeyboardInterrupt):
        mt = 8192

    config.set_user("model.provider", provider)
    config.set_user("model.model", model_name)
    if preset.base_url:
        config.set_user("model.base_url", preset.base_url)
    if api_key_env:
        config.set_user("model.api_key_env", api_key_env)
    config.set_user("model.temperature", temp)
    config.set_user("model.max_tokens", mt)
    config.set_user("mode.default", mode)
    config.set_user("agent.effort", effort)
    console.print(f"\n{t('setup.done', model=f'{provider}/{model_name}')}")
    console.print(f"  {t('setup.full_summary', mode=mode, effort=effort, temp=temp, mt=mt)}")
    return 0


def _setup_blank(config) -> int:
    """空白设置: 仅创建目录骨架, 不配置模型。"""
    config.ensure_home()
    console.print(f"\n{t('setup.blank_title')}")
    console.print(f"  {t('setup.blank_created')}")
    console.print(f"  {t('setup.blank_hint')}")
    for line in t("setup.blank_commands").split("\n"):
        console.print(f"    {line}")
    return 0


# ===================================================================== cmd_bench

_BENCH_EXCLUDE_TOOLS = (
    "write_file", "edit_file", "delete_file", "delete_dir", "move_file",
    "skill_save", "memory_write", "memory_update_user", "run_shell",
)


def cmd_bench(args) -> int:
    """基准测试。"""
    bench_cmd = getattr(args, "bench_cmd", None)
    if bench_cmd == "cache":
        return _bench_cache(args)
    if bench_cmd == "latency":
        return _bench_latency(args)
    console.print("bench 功能开发中…")
    return 0


def _bench_cache(args) -> int:
    """实测 prompt cache 命中率。"""
    from ..ui.format import Table as FTable

    rounds = max(1, getattr(args, "rounds", 8))
    task = getattr(args, "task", "解释一下当前工作区的结构")
    try:
        kernel = build_kernel()
    except Exception as exc:  # noqa: BLE001
        console.print(f"启动失败: {exc}")
        return 1
    config = kernel.require("config")
    provider = config.get("model.provider", "?")
    model = config.get("model.model", "?")
    if not config.api_key():
        console.print("未配置 API Key, 无法调用模型。请先运行 qxt setup 或 qxt models 配置。")
        return 1

    agent = create_agent(kernel, os.getcwd(), confirm=lambda prompt: True, exclude_tools=_BENCH_EXCLUDE_TOOLS)
    console.print("缓存命中率基准测试")
    console.print(f"  模型: {provider}/{model}")
    console.print(f"  轮次: {rounds}")
    console.print(f"  首轮任务: {task}\n")

    rows: List[tuple] = []
    errors = 0
    prev: Dict[str, int] = {}
    for i in range(rounds):
        prompt = task if i == 0 else f"继续。请基于以上对话，深入展开第 {i + 1} 部分，补充新的细节和见解。"
        t0 = time.time()
        try:
            agent.run(prompt, stream=False)
        except Exception as exc:  # noqa: BLE001
            errors += 1
            console.print(f"  第 {i + 1} 轮失败: {exc}")
            continue
        elapsed = time.time() - t0
        cur = dict(agent.total_usage)
        delta = {k: cur.get(k, 0) - prev.get(k, 0) for k in cur}
        prev = cur
        rows.append((i + 1, delta, elapsed))

    if not rows:
        console.print("所有轮次均失败, 无法统计。")
        return 1

    total_prompt = sum(r[1].get("prompt_tokens", 0) for r in rows)
    total_completion = sum(r[1].get("completion_tokens", 0) for r in rows)
    total_hit = sum(r[1].get("prompt_cache_hit_tokens", 0) for r in rows)
    total_miss = sum(r[1].get("prompt_cache_miss_tokens", 0) for r in rows)
    hit_rate = total_hit / (total_hit + total_miss) if (total_hit + total_miss) > 0 else 0.0

    table = FTable(title="逐轮明细")
    table.add_column("轮次", justify="right")
    table.add_column("耗时(s)", justify="right")
    table.add_column("prompt", justify="right")
    table.add_column("输出", justify="right")
    table.add_column("缓存命中", justify="right")
    table.add_column("缓存未命中", justify="right")
    table.add_column("命中率", justify="right")
    for idx, usage, elapsed in rows:
        hit = usage.get("prompt_cache_hit_tokens", 0)
        miss = usage.get("prompt_cache_miss_tokens", 0)
        rate = hit / (hit + miss) if (hit + miss) > 0 else 0.0
        table.add_row(
            str(idx), f"{elapsed:.1f}",
            f"{usage.get('prompt_tokens', 0):,}",
            f"{usage.get('completion_tokens', 0):,}",
            f"{hit:,}", f"{miss:,}", f"{rate:.1%}",
        )
    console.print(table)

    from ..models.router import estimate_cost
    est_cost = estimate_cost(provider, model, total_prompt, total_completion)
    console.print("\n汇总")
    console.print(f"  总 prompt: {total_prompt:,}")
    console.print(f"  总输出:   {total_completion:,}")
    console.print(f"  缓存命中: {total_hit:,} ({hit_rate:.1%})")
    console.print(f"  缓存未命中: {total_miss:,}")
    if errors:
        console.print(f"  失败轮次: {errors}")
    console.print(f"  估算成本: ${est_cost:.4f}")
    return 0


def _bench_latency(args) -> int:
    """实测模型响应延迟与吞吐。"""
    rounds = max(1, getattr(args, "rounds", 5))
    task = getattr(args, "task", "ping")
    try:
        kernel = build_kernel()
    except Exception as exc:  # noqa: BLE001
        console.print(f"启动失败: {exc}")
        return 1
    config = kernel.require("config")
    provider = config.get("model.provider", "?")
    model = config.get("model.model", "?")
    if not config.api_key():
        console.print("未配置 API Key, 无法调用模型。请先运行 qxt setup 或 qxt models 配置。")
        return 1

    from ..models import create_adapter
    try:
        adapter = create_adapter(config)
    except Exception as exc:  # noqa: BLE001
        console.print(f"适配器创建失败: {exc}")
        return 1

    console.print("延迟基准测试")
    console.print(f"  模型: {provider}/{model}")
    console.print(f"  轮次: {rounds}")
    console.print(f"  请求: {task!r}\n")

    latencies: List[float] = []
    tokens = 0
    errors = 0
    for i in range(rounds):
        t0 = time.time()
        try:
            resp = adapter.chat([{"role": "user", "content": task}], stream=False)
        except Exception as exc:  # noqa: BLE001
            errors += 1
            console.print(f"  第 {i + 1} 轮失败: {exc}")
            continue
        elapsed = time.time() - t0
        latencies.append(elapsed)
        usage = getattr(resp, "usage", None) or {}
        tokens += usage.get("completion_tokens", 0)
        console.print(f"  第 {i + 1} 轮: {elapsed:.2f}s")

    if not latencies:
        console.print("所有轮次均失败, 无法统计。")
        return 1

    latencies.sort()
    avg = sum(latencies) / len(latencies)
    p95 = latencies[int(len(latencies) * 0.95) - 1]
    total_time = sum(latencies)
    throughput = tokens / total_time if total_time > 0 else 0.0

    console.print("\n汇总")
    console.print(f"  成功轮次: {len(latencies)}/{rounds}" + (f" (失败 {errors})" if errors else ""))
    console.print(f"  平均延迟: {avg:.2f}s")
    console.print(f"  最小延迟: {latencies[0]:.2f}s")
    console.print(f"  最大延迟: {latencies[-1]:.2f}s")
    console.print(f"  P95 延迟: {p95:.2f}s")
    console.print(f"  总输出 token: {tokens:,}")
    console.print(f"  吞吐: {throughput:.1f} tokens/s")
    return 0


# ===================================================================== cmd_open

def cmd_open(args) -> int:
    """打开文件并定位到行: qxt open <file>[:<line>]。"""
    target = getattr(args, "target", "")
    if not target:
        console.print("用法: qxt open <file>[:<line>], 例如 qxt open qingxiaotuan/core/agent.py:42")
        return 1
    line = 0
    path_str = target
    if ":" in target:
        path_str, _, line_str = target.rpartition(":")
        try:
            line = int(line_str)
        except ValueError:
            console.print(f"行号无效: {line_str}")
            return 1
    fpath = Path(path_str).expanduser()
    if not fpath.is_absolute():
        fpath = Path.cwd() / fpath
    if not fpath.exists():
        console.print(f"文件不存在: {fpath}")
        return 1
    from ..tools.code import _open_in_editor
    editor = ""
    try:
        kernel = build_kernel()
        editor = kernel.require("config").get("ui.editor", "")
    except Exception as exc:  # noqa: BLE001
        log.debug("读取编辑器配置失败: %s", exc)
    console.print(_open_in_editor(fpath.resolve(), line, editor))
    return 0
