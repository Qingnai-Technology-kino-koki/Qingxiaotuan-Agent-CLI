"""用户级 Hooks 引擎 (零第三方依赖, 纯标准库)。

设计目标: 把 Agent 变成"可编排的", 同时把安全边界钉死:
- command 必须是 list(argv), 拒绝裸字符串 (无 shell 注入面)。
- 超时强杀 (subprocess.timeout), 异常 fail-safe (不阻断、只记录)。
- PreToolUse 阻断 / 改写参数 需显式声明 (blocking / allow_edit_args),
  全局 allow_blocking / allow_edit_args 可一键关闭。
- 所有调用写入审计事件 hook.executed (供 self-improve / 观测订阅)。
"""

from __future__ import annotations

import fnmatch
import json
import logging
import subprocess
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

# 受支持的 hook 事件类型 (对标 Claude Code 的 hooks 事件面)
# - PreToolUse / PostToolUse: 工具执行前后 (支持 matcher 过滤, Pre 可阻断/改参)
# - UserPromptSubmit: 用户提交输入后、进入模型前 (stdout 作为附加上下文注入)
# - Stop / SubagentStop: 主回合结束 / 子任务结束时通知
# - PreCompact: 上下文压缩前通知
# - SessionStart / SessionEnd: 会话级生命周期通知
HOOK_EVENTS = (
    "PreToolUse",
    "PostToolUse",
    "UserPromptSubmit",
    "Stop",
    "SubagentStop",
    "PreCompact",
    "SessionStart",
    "SessionEnd",
)

# 仅通知类事件 (无 matcher、stdout 忽略), 由 run_notify 统一派发
_NOTIFY_EVENTS = ("Stop", "SubagentStop", "PreCompact", "SessionStart", "SessionEnd")


@dataclass
class HookDecision:
    """PreToolUse 的返回决策。

    block=True 时主流程中止执行 (仅当 hook 声明 blocking 且全局允许)。
    args 非空时改写即将执行的工具参数 (仅当允许改参)。
    """

    block: bool = False
    reason: str = ""
    args: Optional[Dict[str, Any]] = None


@dataclass
class _HookSpec:
    """单条 hook 配置的解析结果 (内部用)。

    支持多种 hook 类型 (对标 Claude Code 2.1.239):
    - command: 传统脚本 (argv, stdin JSON)
    - url: HTTP POST (发送 JSON, 读取响应)
    - prompt: LLM prompt (调用模型, 返回模型输出)
    - subagent: 子代理 (创建隔离 Agent, 返回摘要)
    """
    event: str
    matcher: str
    command: List[str]
    timeout: float
    blocking: bool
    allow_edit_args: bool
    description: str = ""
    raw: Dict[str, Any] = field(default_factory=dict)
    # 新 hook 类型
    hook_type: str = "command"  # command / url / prompt / subagent
    url: str = ""  # HTTP hook URL
    prompt_text: str = ""  # Prompt hook 的模板文本
    model_override: str = ""  # Prompt/Subagent hook 可覆盖模型

    @property
    def uid(self) -> str:
        if self.hook_type == "url":
            return f"{self.event}:{self.matcher or '*'}:url={self.url}"
        if self.hook_type == "prompt":
            return f"{self.event}:{self.matcher or '*'}:prompt={self.prompt_text[:30]}"
        if self.hook_type == "subagent":
            return f"{self.event}:{self.matcher or '*'}:subagent"
        cmd = " ".join(self.command) if self.command else "<none>"
        return f"{self.event}:{self.matcher or '*'}:{cmd}"


class HookManager:
    """从 config 加载用户级 hooks, 在事件时机执行外部脚本。"""

    def __init__(self, config: Any = None, workspace: Optional[str] = None,
                 kernel: Any = None) -> None:
        self.workspace = workspace
        self._kernel = kernel
        self.enabled = True
        self.default_timeout = 5.0
        self.allow_blocking = True
        self.allow_edit_args = False
        self.audit_log = True
        self._specs: List[_HookSpec] = []

        hooks_cfg = self._read_cfg(config)
        if hooks_cfg is None:
            self.enabled = False
            return
        self.enabled = bool(hooks_cfg.get("enabled", True))
        self.default_timeout = float(hooks_cfg.get("default_timeout", 5))
        self.allow_blocking = bool(hooks_cfg.get("allow_blocking", True))
        self.allow_edit_args = bool(hooks_cfg.get("allow_edit_args", False))
        self.audit_log = bool(hooks_cfg.get("audit_log", True))

        for evt in HOOK_EVENTS:
            for item in hooks_cfg.get(evt, []) or []:
                spec = self._parse_spec(evt, item)
                if spec is not None:
                    self._specs.append(spec)

    # ------------------------------------------------------------------ 配置

    @staticmethod
    def _read_cfg(config: Any) -> Optional[Dict[str, Any]]:
        """从 Config 对象或 dict 取出 hooks 块。"""
        if config is None:
            return None
        try:
            if hasattr(config, "get"):
                v = config.get("hooks", None)
            else:
                v = config.get("hooks") if isinstance(config, dict) else None
        except Exception:
            return None
        if v is None:
            return {}
        if not isinstance(v, dict):
            return {}
        return v

    def _parse_spec(self, event: str, item: Any) -> Optional[_HookSpec]:
        """解析单条 hook 配置; 支持 command / url / prompt / subagent 四种类型。"""
        if not isinstance(item, dict):
            self._audit(event, {"command": str(item)}, "rejected_bad_spec",
                        "hook 配置项必须是含 command 列表的对象")
            return None

        # 确定 hook 类型
        hook_type = str(item.get("type", "command")).strip().lower()
        if hook_type not in ("command", "url", "prompt", "subagent"):
            hook_type = "command"  # 未知类型降级为 command

        cmd: List[str] = []
        url = ""
        prompt_text = ""
        model_override = str(item.get("model", "") or "")

        if hook_type == "url":
            url = str(item.get("url", "") or item.get("command", ""))
            if not url:
                self._audit(event, item, "rejected_empty_url", "HTTP hook 缺少 url")
                return None
        elif hook_type == "prompt":
            prompt_text = str(item.get("prompt", "") or item.get("command", ""))
            if not prompt_text:
                self._audit(event, item, "rejected_empty_prompt", "Prompt hook 缺少 prompt")
                return None
        elif hook_type == "subagent":
            prompt_text = str(item.get("prompt", "") or item.get("instructions", ""))
            # subagent 类型不需要其他必填
        else:
            # command 类型: 传统 argv
            cmd_raw = item.get("command")
            if isinstance(cmd_raw, str):
                self._audit(event, item, "rejected_string_command",
                            "command 必须是列表 [解释器, 脚本, 参数...], 不接受裸字符串")
                return None
            if not isinstance(cmd_raw, (list, tuple)) or not cmd_raw:
                self._audit(event, item, "rejected_empty_command", "command 为空或非列表")
                return None
            cmd = [str(x) for x in cmd_raw]

        try:
            timeout = float(item.get("timeout", self.default_timeout))
        except (TypeError, ValueError):
            timeout = self.default_timeout

        return _HookSpec(
            event=event,
            matcher=str(item.get("matcher", "*") or "*"),
            command=cmd,
            timeout=max(0.1, timeout),
            blocking=bool(item.get("blocking", False)),
            allow_edit_args=bool(item.get("allow_edit_args", False)),
            description=str(item.get("description", "")),
            raw=item,
            hook_type=hook_type,
            url=url,
            prompt_text=prompt_text,
            model_override=model_override,
        )

    # ------------------------------------------------------------------ 匹配

    @staticmethod
    def _match(matcher: str, tool_name: str) -> bool:
        """matcher 支持 `*`, `|` 分隔, `!` 前缀排除, fnmatch 通配。

        例: "write_file|edit_file" 命中二者; "*" 命中全部;
            "!delete_*" 排除删除类; "!delete_*|read_*" 命中存在读取但非删除。
        """
        if not matcher or matcher == "*":
            return True
        parts = [p.strip() for p in matcher.split("|") if p.strip()]
        if not parts:
            return True
        included = False
        excluded = False
        has_include = False
        for p in parts:
            if p.startswith("!"):
                if fnmatch.fnmatch(tool_name, p[1:].strip()):
                    excluded = True
            else:
                has_include = True
                if fnmatch.fnmatch(tool_name, p):
                    included = True
        if has_include:
            return included and not excluded
        return not excluded

    # ------------------------------------------------------------------ 执行

    def run_pre(self, tool_name: str, args: Dict[str, Any],
                dangerous: bool = False, impact: Optional[str] = None) -> HookDecision:
        """PreToolUse: 返回决策 (可阻断 / 可改写参数)。

        安全: 阻断仅当 hook.blocking 且全局 allow_blocking 均为 True;
              改参仅当 hook.allow_edit_args 或全局 allow_edit_args。
        """
        decision = HookDecision()
        if not self.enabled:
            return decision
        for spec in self._specs:
            if spec.event != "PreToolUse":
                continue
            if not self._match(spec.matcher, tool_name):
                continue
            out = self._exec(spec, {
                "hook_event_name": "PreToolUse",
                "tool_name": tool_name,
                "tool_input": args,
                "dangerous": dangerous,
                "impact": impact,
            })
            if out is None:
                continue
            parsed = self._parse_decision(out)
            if parsed is None:
                continue
            if parsed.get("block"):
                if self.allow_blocking and spec.blocking:
                    decision.block = True
                    decision.reason = str(parsed.get("reason", "hook 未提供原因"))
                    return decision  # 首个阻断即生效
                # 配置不允许阻断 → 仅记录, 不阻断 (fail-safe)
                self._audit("PreToolUse", spec.raw, "block_denied",
                            parsed.get("reason", ""))
            new_args = parsed.get("args")
            if isinstance(new_args, dict) and new_args:
                if self.allow_edit_args or spec.allow_edit_args:
                    decision.args = new_args
                else:
                    self._audit("PreToolUse", spec.raw, "edit_args_denied",
                                "hook 请求改写参数但未被授权")
        return decision

    def run_post(self, tool_name: str, args: Dict[str, Any],
                 result: Any = None) -> None:
        """PostToolUse: 只读审计, stdout 忽略, 异常隔离。"""
        if not self.enabled:
            return
        res = result
        payload = {
            "hook_event_name": "PostToolUse",
            "tool_name": tool_name,
            "tool_input": args,
            "tool_result": {
                "status": getattr(res, "status", None),
                "content": (getattr(res, "content", "") or "")[:2000],
                "error_type": getattr(res, "error_type", None),
            },
        }
        for spec in self._specs:
            if spec.event != "PostToolUse":
                continue
            if not self._match(spec.matcher, tool_name):
                continue
            self._exec(spec, payload)  # stdout 忽略

    def run_notify(self, event: str, payload: Optional[Dict[str, Any]] = None) -> None:
        """通知类事件的统一派发 (Stop/SubagentStop/PreCompact/SessionStart/SessionEnd)。

        只读通知: stdout 忽略、异常隔离、不阻断主流程。
        """
        if not self.enabled or event not in _NOTIFY_EVENTS:
            return
        pl = dict(payload or {})
        pl["hook_event_name"] = event
        for spec in self._specs:
            if spec.event != event:
                continue
            self._exec(spec, pl)

    def run_session(self, event: str, payload: Optional[Dict[str, Any]] = None) -> None:
        """SessionStart / SessionEnd: 只读通知 (run_notify 的会话别名)。"""
        self.run_notify(event, payload)

    def run_user_prompt_submit(self, prompt: str) -> str:
        """UserPromptSubmit: 收集各 hook 的 stdout 作为附加上下文。

        返回所有命中 hook 的 stdout (各截断 1000 字符后以换行拼接);
        无 hook 命中或无输出时返回空串。对标 Claude Code 的 additionalContext。
        """
        if not self.enabled:
            return ""
        outs: List[str] = []
        for spec in self._specs:
            if spec.event != "UserPromptSubmit":
                continue
            out = self._exec(spec, {
                "hook_event_name": "UserPromptSubmit",
                "prompt": (prompt or "")[:4000],
            })
            if out and out.strip():
                outs.append(out.strip()[:1000])
        return "\n".join(outs)

    # ------------------------------------------------------------------ 底层

    def _exec(self, spec: _HookSpec, payload: Dict[str, Any]) -> Optional[str]:
        """执行单条 hook, 返回结果文本; 超时/异常返回 None。

        支持四种类型:
        - command: 传统 argv 脚本 (stdin JSON, stdout 文本)
        - url: HTTP POST (发送 JSON, 读取响应)
        - prompt: LLM prompt (调用模型, 返回模型输出)
        - subagent: 子代理 (创建隔离 Agent, 返回摘要)
        """
        if spec.hook_type == "url":
            return self._exec_http(spec, payload)
        elif spec.hook_type == "prompt":
            return self._exec_prompt(spec, payload)
        elif spec.hook_type == "subagent":
            return self._exec_subagent(spec, payload)
        else:
            return self._exec_command(spec, payload)

    def _exec_command(self, spec: _HookSpec, payload: Dict[str, Any]) -> Optional[str]:
        """执行传统 argv 脚本。"""
        try:
            proc = subprocess.run(
                spec.command,
                input=json.dumps(payload, ensure_ascii=False),
                capture_output=True,
                text=True,
                timeout=spec.timeout,
                cwd=self.workspace,
            )
        except subprocess.TimeoutExpired:
            self._audit(spec.event, spec.raw, "timeout",
                        f"hook 超过 {spec.timeout}s 被强杀")
            return None
        except Exception as exc:  # noqa: BLE001
            self._audit(spec.event, spec.raw, "error", f"{type(exc).__name__}: {exc}")
            return None
        if proc.returncode != 0:
            self._audit(spec.event, spec.raw, "nonzero_rc",
                        f"rc={proc.returncode} stderr={proc.stderr[:200]}")
        return proc.stdout

    def _exec_http(self, spec: _HookSpec, payload: Dict[str, Any]) -> Optional[str]:
        """执行 HTTP POST hook。"""
        import urllib.request
        import urllib.error

        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            spec.url,
            data=data,
            headers={"Content-Type": "application/json", "User-Agent": "qingxiaotuan-hook"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=spec.timeout) as resp:  # noqa: S310
                body = resp.read(50000).decode("utf-8", errors="replace")
                return body
        except urllib.error.HTTPError as exc:
            self._audit(spec.event, spec.raw, "http_error",
                        f"HTTP {exc.code} {exc.reason}")
            return None
        except Exception as exc:  # noqa: BLE001
            self._audit(spec.event, spec.raw, "error", f"{type(exc).__name__}: {exc}")
            return None

    def _exec_prompt(self, spec: _HookSpec, payload: Dict[str, Any]) -> Optional[str]:
        """执行 Prompt hook: 调用模型返回结果。"""
        if self._kernel is None:
            return None
        model = self._kernel.get("model_adapter")
        if model is None:
            self._audit(spec.event, spec.raw, "no_model", "Prompt hook: 无可用模型适配器")
            return None

        # 构建 prompt: 将 payload 注入模板
        prompt = spec.prompt_text
        for key, val in payload.items():
            placeholder = "{" + key + "}"
            if placeholder in prompt:
                prompt = prompt.replace(placeholder, json.dumps(val, ensure_ascii=False) if not isinstance(val, str) else val)

        messages = [{"role": "user", "content": prompt}]
        try:
            result = model.chat(messages, stream=False)
            if hasattr(result, "content"):
                return result.content or ""
            return str(result) if result else ""
        except Exception as exc:  # noqa: BLE001
            self._audit(spec.event, spec.raw, "error", f"Prompt hook 模型调用失败: {exc}")
            return None

    def _exec_subagent(self, spec: _HookSpec, payload: Dict[str, Any]) -> Optional[str]:
        """执行 Subagent hook: 创建隔离 Agent 运行任务。"""
        if self._kernel is None:
            return None
        try:
            from ..app import create_agent
            sub = create_agent(self._kernel, self.workspace or "")
            # 将 payload 注入为上下文
            context = json.dumps(payload, ensure_ascii=False)[:3000]
            task = f"{spec.prompt_text}\n\n上下文: {context}" if spec.prompt_text else f"处理以下事件: {context}"
            result = sub.run(task, stream=False, max_iterations=5)
            return result[:3000] if result else ""
        except Exception as exc:  # noqa: BLE001
            self._audit(spec.event, spec.raw, "error", f"Subagent hook 失败: {exc}")
            return None

    @staticmethod
    def _parse_decision(stdout: str) -> Optional[Dict[str, Any]]:
        """解析 hook stdout 为决策 dict。兼容两种风格:
        - {"decision":"block","reason":...} / {"block":true,"reason":...}
        - {"args": {...}}
        返回 None 表示无有效决策。
        """
        s = (stdout or "").strip()
        if not s:
            return None
        try:
            data = json.loads(s)
        except Exception:
            return None
        if not isinstance(data, dict):
            return None
        if "decision" in data:
            return {"block": str(data.get("decision")).lower() == "block",
                    "reason": data.get("reason", ""), "args": data.get("args")}
        if "block" in data:
            return {"block": bool(data.get("block")),
                    "reason": data.get("reason", ""), "args": data.get("args")}
        if "args" in data:
            return {"block": False, "reason": "", "args": data.get("args")}
        return None

    def _audit(self, event: str, hook: Any, status: str, detail: str = "") -> None:
        """写入审计事件 hook.executed (供 self-improve / 观测订阅)。"""
        if not self.audit_log:
            return
        if self._kernel is None:
            return
        try:
            desc = ""
            if isinstance(hook, dict):
                desc = hook.get("description", "") or " ".join(
                    [str(x) for x in hook.get("command", [])])
            self._kernel.emit("hook.executed", {
                "event": event,
                "status": status,
                "detail": detail,
                "hook": desc,
            })
        except Exception as exc:
            log.debug("hook.executed 审计事件发送失败: %s", exc)

    # ------------------------------------------------------------------ 枚举

    def list_hooks(self) -> List[Dict[str, Any]]:
        """列出所有已配置 hook (脱敏, 仅供展示)。"""
        out: List[Dict[str, Any]] = []
        for spec in self._specs:
            out.append({
                "event": spec.event,
                "matcher": spec.matcher,
                "command": spec.command,
                "timeout": spec.timeout,
                "blocking": spec.blocking,
                "allow_edit_args": spec.allow_edit_args,
                "description": spec.description,
            })
        return out

    def enabled_for(self, event: str) -> bool:
        return self.enabled and any(s.event == event for s in self._specs)
