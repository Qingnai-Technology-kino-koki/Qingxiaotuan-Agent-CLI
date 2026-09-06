"""--json-schema 结构化输出 (对标 Claude Code 的 structured/JSON-mode 输出约束)。

允许调用方用一个 JSON Schema 描述期望的输出形状, Agent 被要求以符合该 schema 的
JSON 响应。本模块承担:
  - 加载 schema (内联 JSON 或本地 .json 文件)
  - 构建注入系统提示的"结构化输出指令"
  - 从模型最终回答中鲁棒地抽取 JSON (容忍代码围栏/前后缀文本)
  - 校验 JSON 是否符合 schema (优先用 ``jsonschema``; 不可用时降级到内置轻量校验器)
  - 失败时自动修复重试 (把校验错误回填成一条 user 消息, 让模型下次输出修正版)

设计约束
--------
- 不强制引入第三方依赖: 内置校验器覆盖常用子集 (type/properties/required/items/
  enum/pattern/minimum/maximum/minItems/maxItems/additionalProperties)。
- 纯函数、可测: 不触碰 Agent 内部状态, 由 cmd_chat 编排。
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger(__name__)


# ================================================================ 核心数据结构

class SchemaResult:
    """一次结构化输出的结果。"""

    def __init__(self, ok: bool, value: Any = None, error: str = "",
                 raw: str = "", attempts: int = 1) -> None:
        self.ok = ok
        self.value = value          # 校验通过的 JSON 值
        self.error = error          # 校验失败原因 (ok=False 时)
        self.raw = raw              # 模型原始回答
        self.attempts = attempts    # 实际尝试次数

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "value": self.value,
            "error": self.error,
            "raw": self.raw,
            "attempts": self.attempts,
        }


# ================================================================ Schema 加载

def load_schema(spec: str) -> Tuple[Dict[str, Any], Optional[str]]:
    """加载 JSON Schema。

    ``spec`` 可以是:
      - 内联 JSON 对象字符串 (以 ``{`` 开头);
      - ``@path/to/file.json`` (以 @ 前缀指文件);
      - 普通路径 (.json) —— 若文件存在则读它, 否则视为内联。
    返回 ``(schema, error)``; 失败时 schema 为 {} 且 error 非空。
    """
    p = Path(spec).expanduser()
    if spec.startswith("@"):
        p = Path(spec[1:]).expanduser()
    raw = ""
    if p.suffix in (".json", ".jsonl") and p.exists():
        try:
            raw = p.read_text(encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            return {}, f"读取 schema 文件失败: {exc}"
    else:
        raw = spec

    raw = raw.strip()
    if not raw:
        return {}, "schema 为空"
    # 去掉可能包裹的围栏
    raw = untag_fence(raw)
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError as exc:
        return {}, f"schema 不是合法 JSON: {exc}"
    if not isinstance(obj, dict):
        return {}, "schema 必须是 JSON 对象"
    if "type" not in obj and "properties" not in obj and "$ref" not in obj:
        if any(k in obj for k in ("properties", "required", "additionalProperties")):
            obj = {"type": "object", **obj}
        else:
            return {}, "schema 至少需要 type/properties/$ref 之一"
    return obj, None


# ================================================================ 文本注入

def build_instruction(schema: Dict[str, Any], *, strict: bool = True) -> str:
    """构造注入系统提示的结构化输出指令。"""
    schema_json = json.dumps(schema, ensure_ascii=False, indent=2)
    strict_line = (
        "你的最终回答必须是一个合法 JSON 值, 且必须完全符合给定的 JSON Schema。"
        "不要输出 JSON 之外的解释文字。"
        if strict else
        "请在最终回答中输出一个 JSON 值, 尽量符合给定的 JSON Schema。"
    )
    return (
        "## 结构化输出要求\n"
        "你被要求以结构化 JSON 输出最终结果。\n"
        + strict_line +
        "\n\n必须符合的 JSON Schema:\n```json\n" + schema_json + "\n```\n"
    )


def untag_fence(text: str) -> str:
    """剥掉可能包裹代码的 ```json ... ``` 围栏。"""
    m = re.search(r"```(?:json)?\s*(.*?)```", text, flags=re.DOTALL)
    return m.group(1).strip() if m else text.strip()


def extract_json(text: str) -> Tuple[Any, Optional[str]]:
    """从模型回答中抽取 JSON。

    依次尝试:
      1. 全文若是合法 JSON 直接解析;
      2. 剥围栏后解析;
      3. 用括号配对扫描最外层 JSON 结构 (容忍前后杂质文本)。
    返回 ``(value, error)``。
    """
    if not text:
        return None, "模型未返回任何内容"
    text = text.strip()

    for cand in (text, untag_fence(text)):
        if cand:
            try:
                return json.loads(cand), None
            except json.JSONDecodeError:
                pass

    value, _err = _scan_json(text)
    if value is not None:
        return value, None
    return None, "无法从输出中提取 JSON"


def _scan_json(text: str) -> Tuple[Any, Optional[str]]:
    """用括号配对扫描最外层 JSON 对象/数组, 容忍前后缀文本。"""
    stack: List[str] = []
    start = -1
    pairs = {"{": "}", "[": "]"}
    i = 0
    n = len(text)
    in_str = False
    while i < n:
        ch = text[i]
        if in_str:
            if ch == "\\":
                i += 2
                continue
            if ch == '"':
                in_str = False
            i += 1
            continue
        if ch == '"':
            in_str = True
            if start < 0:
                start = i
            i += 1
            continue
        if ch in ("{", "["):
            if start < 0:
                start = i
            stack.append(ch)
        elif ch in ("}", "]"):
            if stack and pairs[stack[-1]] == ch:
                stack.pop()
                if not stack:
                    try:
                        return json.loads(text[start:i + 1]), None
                    except json.JSONDecodeError:
                        start = -1
        i += 1
    return None, None


# ================================================================ 轻量校验器

def validate_against(value: Any, schema: Any, path: str = "$") -> List[str]:
    """校验 ``value`` 是否满足 ``schema``, 返回错误列表 (空=通过)。

    若环境中安装了真正的 ``jsonschema`` 库则委托给它, 否则用内置校验器。
    """
    try:
        import jsonschema  # type: ignore  # noqa: F401
        errors: List[str] = []
        validator = jsonschema.Draft7Validator(schema)
        for e in sorted(validator.iter_errors(value), key=lambda x: list(x.path)):
            errors.append(f"{e.json_path or '$'}: {e.message}")
        return errors
    except ImportError:
        return _native_validate(value, schema, path)


def _native_validate(value: Any, schema: Any, path: str) -> List[str]:
    if schema is True:
        return []
    if schema is False:
        return [f"{path}: 任何值都不允许 (schema=false)"]
    if not isinstance(schema, dict):
        return []

    errs: List[str] = []

    if "oneOf" in schema:
        if not any(not _native_validate(value, sub, path) for sub in schema["oneOf"]):
            errs.append(f"{path}: 未命中任何 oneOf 分支")
        return errs
    if "allOf" in schema:
        for sub in schema["allOf"]:
            errs.extend(_native_validate(value, sub, path))
        return errs
    if "anyOf" in schema:
        if not any(not _native_validate(value, sub, path) for sub in schema["anyOf"]):
            errs.append(f"{path}: 未命中 anyOf")
        return errs
    if "not" in schema:
        if not _native_validate(value, schema["not"], path):
            errs.append(f"{path}: 命中了 not 禁止的分支")
        return errs

    stype = schema.get("type")
    if stype:
        if not _type_ok(value, stype):
            errs.append(f"{path}: 期望类型 {stype}, 实际 {_typename(value)}")
            return errs

    if schema.get("enum") is not None and value not in schema["enum"]:
        errs.append(f"{path}: 值不在允许枚举内")
    if "const" in schema and value != schema["const"]:
        errs.append(f"{path}: 值不等于 const")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        minv = schema.get("minimum")
        excl_min = schema.get("exclusiveMinimum")
        if isinstance(excl_min, bool):
            if excl_min and minv is not None and value <= minv:
                errs.append(f"{path}: {value} 必须严格大于 minimum={minv}")
        elif isinstance(excl_min, (int, float)):
            if value <= excl_min:
                errs.append(f"{path}: {value} 必须严格大于 exclusiveMinimum={excl_min}")
        elif minv is not None and value < minv:
            errs.append(f"{path}: {value} 小于 minimum={minv}")

        maxv = schema.get("maximum")
        excl_max = schema.get("exclusiveMaximum")
        if isinstance(excl_max, bool):
            if excl_max and maxv is not None and value >= maxv:
                errs.append(f"{path}: {value} 必须严格小于 maximum={maxv}")
        elif isinstance(excl_max, (int, float)):
            if value >= excl_max:
                errs.append(f"{path}: {value} 必须严格小于 exclusiveMaximum={excl_max}")
        elif maxv is not None and value > maxv:
            errs.append(f"{path}: {value} 大于 maximum={maxv}")
        if schema.get("multipleOf") not in (None, 0) and value % schema["multipleOf"] != 0:
            errs.append(f"{path}: {value} 不是 multipleOf={schema['multipleOf']} 的倍数")

    if isinstance(value, str):
        if schema.get("pattern") and not re.search(schema["pattern"], value):
            errs.append(f"{path}: 不匹配 pattern={schema['pattern']}")
        if schema.get("minLength") is not None and len(value) < schema["minLength"]:
            errs.append(f"{path}: 短于 minLength={schema['minLength']}")
        if schema.get("maxLength") is not None and len(value) > schema["maxLength"]:
            errs.append(f"{path}: 长于 maxLength={schema['maxLength']}")
        if schema.get("format") and not _check_format(value, schema["format"]):
            errs.append(f"{path}: 不符合 format={schema['format']}")

    if isinstance(value, list):
        items = schema.get("items")
        if isinstance(items, dict):
            for idx, item in enumerate(value):
                errs.extend(_native_validate(item, items, f"{path}[{idx}]"))
        elif isinstance(items, list):
            # items 列表 => tuple/prefixItems 验证 (前 n 位逐个验证, 超出部分不约束)
            for idx, item in enumerate(value[:len(items)]):
                errs.extend(_native_validate(item, items[idx], f"{path}[{idx}]"))
        for kw, target in (("minItems", "元素少于"), ("maxItems", "元素多于")):
            if schema.get(kw) is not None:
                lim = schema[kw]
                if kw == "minItems" and len(value) < lim:
                    errs.append(f"{path}: 元素少于 minItems={lim}")
                if kw == "maxItems" and len(value) > lim:
                    errs.append(f"{path}: 元素多于 maxItems={lim}")
        if schema.get("uniqueItems"):
            for i in range(len(value)):
                dup = next((j for j in range(i + 1, len(value))
                            if _json_eq(value[i], value[j])), None)
                if dup is not None:
                    errs.append(f"{path}: 元素 {i} 与 {dup} 重复, 违反 uniqueItems")
                    break

    if isinstance(value, dict):
        props = schema.get("properties", {}) or {}
        pats = schema.get("patternProperties", {}) or {}
        for name, psch in props.items():
            if name in value:
                errs.extend(_native_validate(value[name], psch, f"{path}.{name}"))
        for name, v in value.items():
            if name in props:
                continue
            for pat, psch in pats.items():
                if re.search(pat, name):
                    errs.extend(_native_validate(v, psch, f"{path}.{name}"))
                    break
        for name in schema.get("required", []) or []:
            if name not in value:
                errs.append(f"{path}: 缺少必填字段 {name!r}")
        if schema.get("additionalProperties") is False:
            covered = set(props) | set(_match_pattern_keys(value, pats))
            extra = set(value) - covered
            if extra:
                errs.append(f"{path}: 出现额外字段 {sorted(extra)}")
        if schema.get("minProperties") is not None and len(value) < schema["minProperties"]:
            errs.append(f"{path}: 字段少于 minProperties={schema['minProperties']}")
        if schema.get("maxProperties") is not None and len(value) > schema["maxProperties"]:
            errs.append(f"{path}: 字段多于 maxProperties={schema['maxProperties']}")
        if schema.get("propertyNames"):
            for name in value:
                errs.extend(_native_validate(name, schema["propertyNames"], f"{path}." + str(repr(name))))
        for dep, need in (schema.get("dependentRequired", {}) or {}).items():
            if dep in value:
                for n in need:
                    if n not in value:
                        errs.append(f"{path}: 字段 {dep!r} 存在时需 {n!r}")

    return errs


# ================================================================ format 校验

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_TIME_RE = re.compile(r"^\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:Z|[+-]\d{2}:?\d{2})?$")
_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_IPV4_RE = re.compile(r"^(?:\d{1,3}\.){3}\d{1,3}$")


def _check_format(value: str, fmt: str) -> bool:
    """常用 string format 校验子集 (datetime/uuid/email/uri/hostname/ipv4/ipv6)。"""
    f = (fmt or "").lower()
    if f in ("date-time", "datetime"):
        return "T" in value and _DATE_RE.match(value.split("T")[0]) is not None
    if f == "date":
        return bool(_DATE_RE.match(value))
    if f in ("time",):
        return bool(_TIME_RE.match(value))
    if f in ("uuid", "uuid4"):
        return bool(_UUID_RE.match(value))
    if f == "email":
        return bool(_EMAIL_RE.match(value))
    if f == "ipv4":
        return bool(_IPV4_RE.match(value)) and all(0 <= int(p) <= 255 for p in value.split("."))
    if f == "uri":
        return "://" in value and not value[:1].isspace()
    if f == "hostname":
        return bool(re.match(r"^[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?$", value))
    if f in ("decimal", "number"):
        try:
            float(value)
            return True
        except ValueError:
            return False
    return True  # 未知 format 不强制


def _match_pattern_keys(value: Dict[str, Any], pats: Dict[str, Any]) -> set:
    return {k for k in value if any(re.search(p, k) for p in pats)}


def _json_eq(a: Any, b: Any) -> bool:
    """JSON 意义下的深度相等 (dict 键不排序语义)。"""
    if isinstance(a, dict) and isinstance(b, dict):
        if set(a) != set(b):
            return False
        return all(_json_eq(a[k], b[k]) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(_json_eq(x, y) for x, y in zip(a, b))
    return a == b


def _type_ok(value: Any, stype: Any) -> bool:
    expect = stype if isinstance(stype, list) else [stype]
    if "null" in expect and value is None:
        return True
    for t in expect:
        if t == "object" and isinstance(value, dict):
            return True
        if t == "array" and isinstance(value, list):
            return True
        if t == "string" and isinstance(value, str):
            return True
        if t == "number" and isinstance(value, (int, float)) and not isinstance(value, bool):
            return True
        if t == "integer" and isinstance(value, int) and not isinstance(value, bool):
            return True
        if t == "boolean" and isinstance(value, bool):
            return True
    return False


def _typename(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


# ================================================================ 顶层编排

def produce_structured(schema: Dict[str, Any], model_hint: str = "请按 schema 输出",
                       *, max_attempts: int = 3, strict: bool = True,
                       repair_fn=None) -> SchemaResult:
    """简化的独立校验入口 (无 Agent 编排时用)。

    ``model_hint`` 为一次"原始回答"。返回 SchemaResult。
    供测试与无 CLI 环境复用; 多轮修复重试由 ``repair_answer`` + cmd_chat 完成。
    """
    value, err = extract_json(model_hint)
    if err:
        return SchemaResult(ok=False, error=err, raw=model_hint)
    errors = validate_against(value, schema)
    if errors:
        return SchemaResult(ok=False, error=errors[0], value=value, raw=model_hint)
    return SchemaResult(ok=True, value=value, raw=model_hint)


# ================================================================ 多态分发

def match_oneof_branch(value: Any, schema: Any) -> Optional[str]:
    """给定带 ``oneOf`` 的判别 schema, 返回 ``value`` 命中的分支名。

    分支名取各分支的 ``title``; 无 title 时退回 ``branch[{i}]``。未命中返回 None。
    供多态输出路由 (依据模型输出的判别字段/结构决定调哪个处理器) 使用。
    """
    subs = schema.get("oneOf") if isinstance(schema, dict) else None
    if not subs:
        return None
    for i, sub in enumerate(subs):
        if isinstance(sub, dict) and not _native_validate(value, sub, "$"):
            return sub.get("title") or f"branch[{i}]"
    return None


def dispatch_builtin(schema: Dict[str, Any], value: Any, handlers,
                     *, key: str = "name", on_unknown=None):
    """多态分发: 依据 ``value`` 中的 ``key`` 字段从 ``handlers`` 选执行器。

    ``handlers`` 为 ``{判别名: callable(value)->Any}``。命中则执行并返回其结果;
    未命中时若有 ``on_unknown`` 调用之, 否则返回描述性错误字符串。
    用于把一次结构化输出按类别路由到不同后续步骤 (对标 Claude Code 的
    root tool dispatch)。
    """
    disc = value.get(key) if isinstance(value, dict) else None
    fn = handlers.get(disc) if disc is not None else None
    if fn is not None:
        return fn(value)
    if on_unknown is not None:
        return on_unknown(value)
    return {"ok": False,
            "error": f"未找到 key={key!r} 对应的处理器: {disc!r}",
            "available": sorted(handlers)}


# ================================================================ 顶层编排

def run_structured(executor, schema: Dict[str, Any], *,
                   max_attempts: int = 3, strict: bool = True,
                   initial_task: str = "", on_attempt=None) -> SchemaResult:
    """带修复重试的完整结构化输出流程。

    ``executor(task_text: str) -> str`` 视作执行器 (内部把回填的 task 追加进会话再跑)。
    流程: 跑初始任务 -> 抽取/校验 -> 失败则用 ``repair_answer`` 回填修复提示重跑,
    最多 ``max_attempts`` 次。每次尝试前回调用 ``on_attempt(attempt, error)``。
    """
    attempt = 0
    raw = initial_task
    last: Optional[SchemaResult] = None
    while attempt < max_attempts:
        if attempt > 0:
            repair_msgs = repair_answer(schema, raw, strict=strict)
            task = _append_repairs(raw, repair_msgs)
        else:
            task = raw
        raw = executor(task)
        attempt += 1
        last = _try_candidate(raw, schema)
        if on_attempt:
            err = None if (last and last.ok) else (last.error if last else "无法解析输出")
            on_attempt(attempt, err)
        if last and last.ok:
            last.attempts = attempt
            return last
    if last is None:
        last = SchemaResult(ok=False, error="执行器未返回任何输出", raw=raw)
    last.attempts = attempt
    return last


def _append_repairs(raw: str, repair_msgs: List[dict]) -> str:
    """执行器只接受纯文本时, 把修复指令编码进下一条任务提示。"""
    content = repair_msgs[0]["content"] if repair_msgs else "请修正输出"
    return content


def _try_candidate(raw: str, schema: Dict[str, Any]) -> Optional[SchemaResult]:
    """对一次原始回答做抽取+校验, 通过返回 SchemaResult(ok=True), 否则 None。"""
    value, err = extract_json(raw)
    if err:
        return SchemaResult(ok=False, error=err, raw=raw)
    errors = validate_against(value, schema)
    if errors:
        return SchemaResult(ok=False, error=summarize_errors(errors)[0],
                            value=value, raw=raw)
    return SchemaResult(ok=True, value=value, raw=raw)


def repair_answer(schema: Dict[str, Any], raw: str,
                  *, strict: bool = True, max_hints: int = 6) -> List[Dict[str, Any]]:
    """生成"修复提示"消息 (供 Agent 下一轮改进), 并返回应追加的消息列表。

    把当前失败原因整理成一条 user 指令, 让模型原地修正后重出 JSON。
    """
    value, extract_err = extract_json(raw)
    problems: List[str] = []
    if extract_err:
        problems.append(f"你的输出无法被解析为 JSON: {extract_err}")
    else:
        problems = summarize_errors(validate_against(value, schema))
    if not problems:
        problems = ["输出未通过 schema 校验"]
    hint = (
        "你上一条回复不符合要求的 JSON Schema 要求。请只输出一个修正后的 JSON, "
        "不要任何多余文字。问题:\n- " + "\n- ".join(problems[:max_hints])
    )
    return [{"role": "user", "content": hint}]


def summarize_errors(errors: List[str], max_group: int = 5) -> List[str]:
    """把逐字段错误精炼成修复提示 (按属性分组, 每条取最具体的原因)。

    例如多个字段报错会被压缩成 "字段 X/Y/Z 不合法" 加详情的组合, 避免把
    整段原文回填给模型浪费 token。
    """
    if not errors:
        return errors
    # 提取每条错误的位置前缀 (如 $.pages 或 $.items[0]) 用于分组
    grouped: Dict[str, List[str]] = {}
    for e in errors:
        m = re.match(r"^([^:：]+)[:：]\s*(.*)$", e)
        if m:
            grouped.setdefault(m.group(1), []).append(m.group(2))
        else:
            grouped.setdefault("", []).append(e)
    summary: List[str] = []
    for at, details in grouped.items():
        if not details:
            continue
        # 同一位置有多个原因时只保留前 2 个
        shown = details[:max_group]
        for d in shown:
            summary.append(f"{at}: {d}" if at else d)
    return summary[:max_group * 4]