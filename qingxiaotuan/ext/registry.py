"""纯 Python 引擎注册中心 (替代 ext/c/*.exe 和 ext/ts/src/*/main.ts)

统一注册所有 Python 实现的外部能力引擎, 通过 JSONL IPC 协议与内核对话。
引擎列表:
- diff: 行/词级 Myers diff + patch + 3-way merge
- crypto: AES-256-GCM 加解密 + PBKDF2-HMAC-SHA256
- index: FNV-1a 增量符号索引
- ansi: 终端转义解析/剥离/渲染
- safety: 最小影响半径护栏: 风险评分 + blast radius
- json: RFC 6901 Pointer / 逐路径 diff / 深合并
- search: 递归正则检索
- notify: 跨平台桌面通知
"""
import json
import os
import sys
import subprocess


# 引擎映射: name -> (module_path, class_name)
ENGINE_MAP = {
    "diff": ("qingxiaotuan.ext.diff_engine", "DiffEngine"),
    "crypto": ("qingxiaotuan.ext.crypto_engine", "CryptoEngine"),
    "index": ("qingxiaotuan.ext.index_engine", "IndexEngine"),
    "ansi": ("qingxiaotuan.ext.ansi_engine", "AnsiEngine"),
    "safety": ("qingxiaotuan.ext.safety_engine", "SafetyEngine"),
    "json": ("qingxiaotuan.ext.json_engine", "JsonEngine"),
    "search": ("qingxiaotuan.ext.search_engine", "SearchEngine"),
    "notify": ("qingxiaotuan.ext.notify_engine", "NotifyEngine"),
    "rules": ("qingxiaotuan.ext.rules_engine", "RuleEngine"),
    "skill-market": ("qingxiaotuan.ext.skill_market_engine", "SkillMarketEngine"),
}

# 可用引擎列表 (按名称)
ENGINE_NAMES = list(ENGINE_MAP.keys())


def get_engine(name: str):
    """获取引擎实例"""
    if name not in ENGINE_MAP:
        raise KeyError(f"Unknown engine: {name}")
    module_path, class_name = ENGINE_MAP[name]
    module = __import__(module_path, fromlist=[class_name])
    cls = getattr(module, class_name)
    return cls()


def engine_healthcheck(name: str = None):
    """健康检查所有或指定引擎"""
    results = {}
    engines = [name] if name else ENGINE_NAMES
    for eng in engines:
        try:
            instance = get_engine(eng)
            meta = instance.list_methods()
            results[eng] = {"ok": True, "meta": meta}
        except Exception as e:
            results[eng] = {"ok": False, "error": str(e)}
    return results


def run_engine_subprocess(name: str):
    """以子进程方式运行引擎 (保持 JSONL IPC 协议兼容)"""
    module_path = ENGINE_MAP[name][0]
    cmd = [sys.executable, "-m", module_path]
    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return proc


if __name__ == "__main__":
    # CLI 入口: 支持 selftest
    if len(sys.argv) > 1 and sys.argv[1] == "selftest":
        results = engine_healthcheck()
        print(json.dumps(results, indent=2, ensure_ascii=False))
        all_ok = all(v["ok"] for v in results.values())
        sys.exit(0 if all_ok else 1)
    else:
        print(f"Available engines: {', '.join(ENGINE_NAMES)}")
