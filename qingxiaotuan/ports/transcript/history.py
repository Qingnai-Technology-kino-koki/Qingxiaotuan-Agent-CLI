"""History reconciliation helpers (对齐上游 history 分组/折叠语义)."""

from __future__ import annotations

import json
import math
from typing import Any, Optional

from .ids import turn_id
from .media_ref import daemon_file_ref_from_pairing_part
from .model import (
    AgentStatusMeta,
    GoalMeta,
    TranscriptAttachment,
    TranscriptFrame,
    TranscriptItem,
    TranscriptMarker,
    TranscriptMeta,
    TranscriptTask,
    TranscriptTaskRef,
    TranscriptTurn,
)
from .operation import AgentTranscriptSnapshot

# --------------------------------------------------------------------------- #
# groupTurns.ts
# --------------------------------------------------------------------------- #

_HIDDEN_USER_ORIGINS = {"injection", "system_trigger", "retry"}
_TURN_OPENING_SYSTEM_TRIGGERS = {"goal_continuation", "subagent"}
_MARKER_USER_ORIGINS = {"skill_activation": "skill", "plugin_command": "skill", "compaction_summary": "compaction"}
_FALLBACK_ORIGIN = {"kind": "other"}


class _TurnDraft:
    def __init__(self, turn_id: str, ordinal: int, origin: dict, prompt: Optional[str] = None, attachment_ids: Optional[list] = None):
        self.turn_id = turn_id
        self.ordinal = ordinal
        self.origin = origin
        self.prompt = prompt
        self.attachment_ids = attachment_ids
        self.steps: list = []


class _StepDraft:
    def __init__(self, step_id: str, ordinal: int):
        self.step_id = step_id
        self.ordinal = ordinal
        self.frames: list = []


class _PendingNotification:
    def __init__(self, text: str, task_id: Optional[str], attachment_ids: Optional[list] = None, prompt_ids: Optional[list] = None, steered: bool = False):
        self.text = text
        self.task_id = task_id
        self.attachment_ids = attachment_ids
        self.prompt_ids = prompt_ids
        self.steered = steered


def group_messages_into_snapshot(messages: list, options: Optional[dict] = None) -> AgentTranscriptSnapshot:
    options = options or {}
    items: list = []
    attachments: list = []
    steered_contents = {k: dict(v) for k, v in (options.get("steeredContents") or {}).items()}
    turn: Optional[_TurnDraft] = None
    pending: list = []
    next_ordinal = 0
    marker_count = 0

    def fold_turn_opening_input(message: dict):
        parts = message.get("content") or []
        ids: list = []
        texts: list = []
        for part in parts:
            if part.get("type") == "text" and isinstance(part.get("text"), str):
                texts.append(part["text"])
                continue
            if part.get("type") in ("image", "video", "audio"):
                source = part.get("source")
                if source is None:
                    continue
                entity = TranscriptAttachment(
                    attachment_id=f"att_{len(attachments) + 1}",
                    media_type=source["media_type"] if source.get("kind") == "base64" else f"{part['type']}/*",
                    source=(
                        {"kind": "url", "url": source["url"]}
                        if source.get("kind") == "url"
                        else ({"kind": "file", "fileId": source["file_id"]} if source.get("kind") == "file" else None)
                    ),
                )
                attachments.append(entity)
                ids.append(entity.attachment_id)
                continue
            if part.get("type") == "file" and "file_id" in part:
                entity = TranscriptAttachment(
                    attachment_id=f"att_{len(attachments) + 1}",
                    media_type=part.get("media_type"),
                    name=part.get("name"),
                    size=part.get("size"),
                    source={"kind": "file", "fileId": part["file_id"]},
                )
                attachments.append(entity)
                ids.append(entity.attachment_id)
                continue
            ref = daemon_file_ref_from_pairing_part(_MediaRefPart(part.get("type"), image_url=part.get("imageUrl"), video_url=part.get("videoUrl")))
            if ref is not None:
                entity = TranscriptAttachment(
                    attachment_id=f"att_{len(attachments) + 1}",
                    media_type=f"{ref['kind']}/*",
                    source={"kind": "session_media", "fileId": ref["ref"].file_id},
                )
                attachments.append(entity)
                ids.append(entity.attachment_id)
        for attachment in _origin_file_attachments(message):
            entity = TranscriptAttachment(
                attachment_id=f"att_{len(attachments) + 1}",
                media_type=attachment["mediaType"],
                name=attachment.get("name"),
                size=attachment.get("size"),
            )
            attachments.append(entity)
            ids.append(entity.attachment_id)
        return {"text": "".join(texts), "attachmentIds": ids if ids else None}

    def ensure_turn(origin=None):
        nonlocal turn
        if turn is None:
            ordinal = next_ordinal
            _advance()
            turn = _TurnDraft(turn_id(ordinal), ordinal, origin or _FALLBACK_ORIGIN)
            items.append(_draft_to_turn_item(turn))
        return turn

    def _advance():
        nonlocal next_ordinal
        next_ordinal += 1

    def flush_steered_leftovers():
        nonlocal pending, turn
        leftovers = [p for p in pending if p.steered]
        if not leftovers:
            return
        pending = [p for p in pending if not p.steered]
        for p in leftovers:
            last_step = turn.steps[-1] if turn and turn.steps else None
            if turn is None or last_step is None:
                _start_turn({"kind": "user"}, p.text, p.attachment_ids)
                continue
            last_step.frames.append(_text_frame(f"{last_step.step_id}.f{len(last_step.frames) + 1}", "user", p.text, p.attachment_ids, p.prompt_ids))
            _sync_turn_item(items, turn)

    def _start_turn(origin, prompt=None, attachment_ids=None):
        nonlocal turn, pending
        flush_steered_leftovers()
        ordinal = next_ordinal
        _advance()
        pending = []
        turn = _TurnDraft(turn_id(ordinal), ordinal, origin, prompt, attachment_ids)
        items.append(_draft_to_turn_item(turn))
        return turn

    def push_marker(marker: str, payload: Any = None):
        nonlocal marker_count
        marker_count += 1
        item = TranscriptMarker(marker_id=f"m{marker_count}", marker=marker, payload=payload)
        items.append(item)

    prev_non_task_role: Optional[str] = None
    for message in messages:
        if message.get("role") == "system":
            continue
        origin_kind = (message.get("origin") or {}).get("kind")
        is_task_origin = origin_kind in ("task", "background_task", "task_notification")
        prev_role_at_entry = prev_non_task_role
        if not is_task_origin:
            prev_non_task_role = message.get("role")

        if message.get("role") == "user":
            if origin_kind is not None and origin_kind in _HIDDEN_USER_ORIGINS:
                if _opens_own_turn(message):
                    opening = fold_turn_opening_input(message) if (message.get("origin") or {}).get("name") == "subagent" else None
                    _start_turn(_map_origin(message), opening.get("text") if opening else None, opening.get("attachmentIds") if opening else None)
                continue
            marker_key = _MARKER_USER_ORIGINS.get(origin_kind) if origin_kind is not None else None
            if marker_key is not None and not _is_user_slash_prompt(message):
                push_marker(marker_key, {"text": _text_of(message), "origin": message.get("origin")})
                continue
            content_key = json_dumps(message.get("content") or [])
            steer_kind = origin_kind or "user"
            steered_by_kind = steered_contents.get(content_key)
            steered_remaining = steered_by_kind.get(steer_kind, 0) if steered_by_kind else 0
            if steered_by_kind is not None and steered_remaining > 0:
                steered_by_kind[steer_kind] = steered_remaining - 1
                bundled = _bundled_skill_activations(message)
                parts = message.get("content") or []
                for index, activation in enumerate(bundled):
                    block = parts[index] if index < len(parts) else None
                    push_marker("skill", {
                        "text": block["text"] if (block and block.get("type") == "text" and "text" in block) else "",
                        "origin": {"kind": "skill_activation", "trigger": "user-slash", **activation},
                    })
                opening = fold_turn_opening_input({**message, "content": parts[len(bundled):]})
                pending.append(_PendingNotification(opening["text"], None, opening["attachmentIds"], steered=True))
                continue
            if marker_key is not None:
                opening = fold_turn_opening_input(message) if _is_user_slash_prompt(message) else None
                push_marker(marker_key, {"text": (opening or {}).get("text") or _text_of(message), "origin": message.get("origin")})
                if opening is not None:
                    _start_turn(_map_origin(message), opening["text"], opening["attachmentIds"])
                continue
            if is_task_origin:
                origin = message.get("origin") or {}
                task_id = origin.get("taskId") if isinstance(origin.get("taskId"), str) else None
                opens_own = (options.get("taskOriginTurnTaskIds") is None and prev_role_at_entry not in ("assistant", "tool")) or (
                    task_id is None or (options.get("taskOriginTurnTaskIds") or set()).__contains__(task_id) or origin_kind == "background_task"
                )
                if opens_own:
                    opening = fold_turn_opening_input(message)
                    _start_turn(_map_origin(message), opening["text"], opening["attachmentIds"])
                    continue
                pending.append(_PendingNotification(_notification_frame_text(_text_of(message)), task_id))
                continue
            bundled = _bundled_skill_activations(message)
            if bundled:
                parts = message.get("content") or []
                for index, activation in enumerate(bundled):
                    block = parts[index] if index < len(parts) else None
                    push_marker("skill", {
                        "text": block["text"] if (block and block.get("type") == "text" and "text" in block) else "",
                        "origin": {"kind": "skill_activation", "trigger": "user-slash", **activation},
                    })
                caller_message = {**message, "content": parts[len(bundled):]}
                opening = fold_turn_opening_input(caller_message)
                _start_turn(_map_origin(message), opening["text"], opening["attachmentIds"])
                continue
            opening = fold_turn_opening_input(message)
            _start_turn(_map_origin(message), opening["text"], opening["attachmentIds"])
            continue

        if message.get("role") == "assistant":
            current = ensure_turn()
            step_ordinal = len(current.steps) + 1
            step = _StepDraft(f"{current.turn_id}.{step_ordinal}", step_ordinal)
            current.steps.append(step)
            frame_count = 0

            def next_frame_id():
                nonlocal frame_count
                frame_count += 1
                return f"{step.step_id}.f{frame_count}"

            for p in pending:
                step.frames.append(_text_frame(next_frame_id(), "user", p.text, p.task_id, p.attachment_ids, p.prompt_ids))
            pending = []
            for part in message.get("content") or []:
                if part.get("type") == "text" and isinstance(part.get("text"), str) and part["text"]:
                    step.frames.append(_text_frame(next_frame_id(), "assistant", part["text"]))
                elif part.get("type") == "think" and isinstance(part.get("think"), str) and part["think"]:
                    step.frames.append(_ThinkingFrame(next_frame_id(), part["think"]))
            for call in message.get("toolCalls") or []:
                step.frames.append(_ToolCallFrame(f"{step.step_id}.{call['id']}", call["id"], call["name"], "running", _parse_arguments(call.get("arguments"))))
            _sync_turn_item(items, current)
            continue

        if message.get("role") == "tool":
            frame = _current_turn_tool_frame(turn, message.get("toolCallId"))
            if frame is not None and frame.kind == "tool":
                output = _text_of(message)
                patched = _ToolCallFrame(
                    frame.frame_id, frame.tool_call_id, frame.name,
                    "error" if message.get("isError") else "done",
                    frame.input, output, frame.display, output if message.get("isError") else None,
                    frame.input_text, frame.progress, frame.task_id, frame.approval_id, frame.todo_id, frame.agent_refs,
                )
                _replace_tool_frame(turn, message.get("toolCallId"), patched)
                _sync_turn_item(items, turn)

    flush_steered_leftovers()
    return AgentTranscriptSnapshot(items=items, tasks=[], interactions=[], attachments=attachments, todos=[], prompts=[], meta=TranscriptMeta(), has_more_older=False)


def _text_frame(frame_id, role, text, task_id=None, attachment_ids=None, prompt_ids=None):
    from .model import TextFrame

    return TextFrame(frame_id=frame_id, role=role, text=text, task_id=task_id, attachment_ids=attachment_ids, prompt_ids=prompt_ids)


def _ThinkingFrame(frame_id, text):  # noqa: N802
    from .model import ThinkingFrame

    return ThinkingFrame(frame_id=frame_id, text=text)


def _ToolCallFrame(frame_id, tool_call_id, name, state, input=None, output=None, display=None, error=None, input_text=None, progress=None, task_id=None, approval_id=None, todo_id=None, agent_refs=None):  # noqa: N802
    from .model import ToolCallFrame

    return ToolCallFrame(frame_id=frame_id, tool_call_id=tool_call_id, name=name, state=state, input=input, output=output, display=display, error=error, input_text=input_text, progress=progress, task_id=task_id, approval_id=approval_id, todo_id=todo_id, agent_refs=agent_refs)


def _draft_to_turn_item(draft: _TurnDraft) -> TranscriptTurn:
    return TranscriptTurn(
        kind="turn",
        turn_id=draft.turn_id,
        ordinal=draft.ordinal,
        state="completed",
        origin=draft.origin,
        prompt=draft.prompt,
        attachment_ids=draft.attachment_ids,
        steps=[
            _StepDraft_to_step(s, draft.turn_id) for s in draft.steps
        ],
    )


def _StepDraft_to_step(step: _StepDraft, turn_id: str):
    from .model import TranscriptStep

    return TranscriptStep(kind="step", step_id=step.step_id, turn_id=turn_id, ordinal=step.ordinal, state="completed", frames=list(step.frames))


def _sync_turn_item(items: list, draft: _TurnDraft) -> None:
    for i, entry in enumerate(items):
        if entry.kind == "turn" and entry.turn_id == draft.turn_id:
            items[i] = _draft_to_turn_item(draft)
            return


def _current_turn_tool_frame(turn: Optional[_TurnDraft], tool_call_id):
    if turn is None or tool_call_id is None:
        return None
    for s in reversed(turn.steps):
        for f in reversed(s.frames):
            if f.kind == "tool" and f.tool_call_id == tool_call_id:
                return f
    return None


def _replace_tool_frame(turn: _TurnDraft, tool_call_id, nxt) -> None:
    for s in reversed(turn.steps):
        for i, f in enumerate(s.frames):
            if f.kind == "tool" and f.tool_call_id == tool_call_id:
                s.frames[i] = nxt
                return


def _notification_frame_text(text: str) -> str:
    if not text.startswith("<notification"):
        return text
    opening_end = text.find(">")
    closing_start = text.rfind("</notification>")
    if opening_end == -1 or closing_start <= opening_end:
        return text
    inner = text[opening_end + 1 : closing_start]
    lines = inner.split("\n")
    header_end = 0
    while header_end < len(lines) and lines[header_end].strip() == "":
        header_end += 1
    body_start = header_end
    if body_start < len(lines) and lines[body_start].startswith("Title: "):
        title = lines[body_start][len("Title: "):]
        body_start += 1
    else:
        title = ""
    if body_start < len(lines) and lines[body_start].startswith("Severity: "):
        body_start += 1
    body_lines = lines[body_start:]
    child_start = next((i for i, line in enumerate(body_lines) if line.lstrip().startswith("<output-file") or line.lstrip().startswith("<output-preview")), -1)
    body = "\n".join(body_lines[:child_start] if child_start != -1 else body_lines).strip()
    if title and body:
        return f"{title}\n{body}"
    return title or (body if body else text)


def _opens_own_turn(message: dict) -> bool:
    origin = message.get("origin") or {}
    return origin.get("kind") == "system_trigger" and isinstance(origin.get("name"), str) and origin["name"] in _TURN_OPENING_SYSTEM_TRIGGERS


def _is_user_slash_prompt(message: dict) -> bool:
    origin = message.get("origin") or {}
    return origin.get("kind") in ("skill_activation", "plugin_command") and origin.get("trigger") == "user-slash"


def _map_origin(message: dict) -> dict:
    origin = message.get("origin") or {}
    kind = origin.get("kind")
    if kind in ("cron_job", "cron_missed"):
        job_id = origin.get("jobId")
        return {"kind": "cron", "taskId": job_id if isinstance(job_id, str) else None, "payload": origin}
    if kind in ("task", "background_task"):
        task_id = origin.get("taskId")
        return {"kind": "task", "taskId": task_id} if isinstance(task_id, str) else {"kind": "other", "payload": origin}
    if kind == "hook_result":
        return {"kind": "hook", "payload": origin}
    if kind == "shell_command":
        return {"kind": "user", "payload": origin}
    if kind in ("user", None):
        return {"kind": "user"}
    return {"kind": "other", "payload": origin}


class _BundledSkillActivation(dict):
    pass


def _bundled_skill_activations(message: dict) -> list:
    if (message.get("origin") or {}).get("kind") != "user":
        return []
    activations = (message.get("origin") or {}).get("skillActivations")
    if not isinstance(activations, list):
        return []
    return [a for a in activations if isinstance(a, dict) and isinstance(a.get("activationId"), str) and isinstance(a.get("skillName"), str)]


class _OriginFileAttachment(dict):
    pass


def _origin_file_attachments(message: dict) -> list:
    kind = (message.get("origin") or {}).get("kind")
    if kind not in ("user", "skill_activation"):
        return []
    attachments = (message.get("origin") or {}).get("attachments")
    if not isinstance(attachments, list):
        return []
    return [a for a in attachments if isinstance(a, dict) and isinstance(a.get("name"), str) and isinstance(a.get("mediaType"), str) and isinstance(a.get("size"), (int, float)) and isinstance(a.get("path"), str)]


def _text_of(message: dict) -> str:
    return "".join(p["text"] for p in (message.get("content") or []) if p.get("type") == "text" and isinstance(p.get("text"), str))


def _parse_arguments(raw):
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return raw


def json_dumps(obj) -> str:
    import json as _json

    return _json.dumps(obj, sort_keys=True, ensure_ascii=False)


class _MediaRefPart:
    def __init__(self, part_type, text=None, image_url=None, video_url=None):
        self.type = part_type
        self.text = text
        self.image_url = image_url
        self.video_url = video_url


# --------------------------------------------------------------------------- #
# foldFacts.ts
# --------------------------------------------------------------------------- #


def fold_wire_record_facts(records: Any, base: AgentTranscriptSnapshot) -> AgentTranscriptSnapshot:
    tasks: dict = {}
    interactions: dict = {}
    ended_turns: dict = {}
    next_turn_id = 0
    cancelled_turn_ids = set()
    hidden_turn_ids = set()
    active_cancel_turn_ids: set = set()

    def skip_cancelled_turn_ids():
        nonlocal next_turn_id
        while next_turn_id in cancelled_turn_ids:
            hidden_turn_ids.add(next_turn_id)
            next_turn_id += 1

    todo = None
    goal = None
    goal_touched = False
    plan_active = None
    plan_revision = None
    swarm_active = None
    tower_active = None

    appended: list = []
    marker_seq = 0
    used_ref_ids = set()
    for item in base.items:
        if item.kind == "marker":
            import re as _re

            m = _re.match(r"^m(\d+)$", item.marker_id)
            if m is not None:
                marker_seq = max(marker_seq, int(m.group(1)))
        elif item.kind == "taskref":
            used_ref_ids.add(item.ref_id)

    def push_marker(marker: str, record: dict):
        nonlocal marker_seq
        marker_seq += 1
        appended.append(TranscriptMarker(marker_id=f"m{marker_seq}", marker=marker, payload=_payload_of(record), at=_record_time_iso(record)))

    def upsert_task(record: dict):
        info = record.get("info") or {}
        if not isinstance(info.get("taskId"), str):
            return
        task_id = info["taskId"]
        prev = tasks.get(task_id)
        status = info.get("status")
        task = TranscriptTask(
            task_id=task_id,
            kind=_map_task_kind(info.get("kind")),
            state=status if isinstance(status, str) and status in _TASK_STATES else (prev.state if prev else "running"),
            detached=bool(info.get("detached")) if isinstance(info.get("detached"), bool) else (prev.detached if prev else True),
            description=info.get("description") if isinstance(info.get("description"), str) else (prev.description if prev else None),
            agent_id=info.get("agentId") if isinstance(info.get("agentId"), str) else (prev.agent_id if prev else None),
            output_tail=record["outputTail"] if isinstance(record.get("outputTail"), str) else (prev.output_tail if prev else ""),
            started_at=prev.started_at if prev else _epoch_ms_to_iso(info.get("startedAt")),
            ended_at=_epoch_ms_to_iso(info.get("endedAt")) or (prev.ended_at if prev else None),
        )
        tasks[task_id] = task
        if record.get("type") == "task.started":
            ref_id = f"ref-{task_id}"
            if ref_id not in used_ref_ids:
                used_ref_ids.add(ref_id)
                appended.append(TranscriptTaskRef(ref_id=ref_id, task_id=task_id, at=_record_time_iso(record)))

    for record in records:
        t = record.get("type")
        if t == "tools.update_store":
            if record.get("key") == "todo":
                todo = {"todoId": "todo", "items": _read_todo_items(record.get("value")), "updatedAt": _record_time_iso(record)}
        elif t == "goal.create":
            goal_touched = True
            goal = {
                "objective": record.get("objective") if isinstance(record.get("objective"), str) else "",
                "status": "active",
                "completionCriterion": record.get("completionCriterion") if isinstance(record.get("completionCriterion"), str) else None,
                "budgetUsed": 0,
            }
            push_marker("goal", record)
        elif t == "goal.update":
            goal_touched = True
            if goal is not None:
                budget = (record.get("budgetLimits") or {}).get("tokenBudget")
                goal = {
                    **goal,
                    "status": record.get("status") if record.get("status") in _GOAL_STATUSES else goal["status"],
                    "budgetUsed": record.get("tokensUsed") if isinstance(record.get("tokensUsed"), (int, float)) else goal["budgetUsed"],
                    "budgetLimit": budget if isinstance(budget, (int, float)) else goal.get("budgetLimit"),
                }
            push_marker("goal", record)
        elif t == "goal.clear":
            goal_touched = True
            goal = None
        elif t == "plan_mode.enter":
            plan_active = True
            plan_revision = None
            push_marker("plan.enter", record)
        elif t in ("plan_mode.exit", "plan_mode.cancel"):
            plan_active = False
            plan_revision = None
            push_marker("plan.exit", record)
        elif t == "plan.revision":
            plan_active = True
            plan_revision = {
                "reviewPath": record.get("path") if isinstance(record.get("path"), str) else None,
                "version": record.get("version") if isinstance(record.get("version"), int) else None,
            }
            push_marker("plan.revision", record)
        elif t == "swarm_mode.enter":
            swarm_active = True
            push_marker("swarm.enter", record)
        elif t == "swarm_mode.exit":
            swarm_active = False
            push_marker("swarm.exit", record)
        elif t == "tower_mode.enter":
            tower_active = True
        elif t == "tower_mode.exit":
            tower_active = False
        elif t in ("task.started", "task.terminated"):
            upsert_task(record)
        elif t == "turn.cancel":
            target = record.get("target")
            tid = record.get("turnId")
            is_int = isinstance(tid, int) and not isinstance(tid, bool)
            if target == "queued" and is_int and tid >= next_turn_id:
                cancelled_turn_ids.add(tid)
                skip_cancelled_turn_ids()
            elif target == "active":
                if not is_int or tid < 0 or tid in active_cancel_turn_ids:
                    pass
                else:
                    active_cancel_turn_ids.add(tid)
                    if record.get("reason") == "user_cancelled":
                        push_marker("interruption", record)
        elif t == "interaction.request":
            kind = record.get("kind")
            if kind not in ("approval", "question") or not isinstance(record.get("id"), str):
                continue
            req_tool = (record.get("request") or {}).get("toolCallId") if isinstance(record.get("request"), dict) else None
            tool_call_id = record.get("toolCallId") if isinstance(record.get("toolCallId"), str) else (req_tool if isinstance(req_tool, str) else None)
            interactions[record["id"]] = {
                "interactionId": record["id"],
                "interactionKind": kind,
                "toolCallId": tool_call_id,
                "state": "pending",
                "request": record.get("request"),
            }
        elif t == "interaction.resolved":
            if not isinstance(record.get("id"), str):
                continue
            entity = interactions.get(record["id"])
            if entity is None:
                continue
            interactions[record["id"]] = {
                **entity,
                "state": _map_interaction_end_state(entity["interactionKind"], record.get("response")),
                "response": record.get("response"),
            }
        elif t == "turn.ended":
            if isinstance(record.get("turnId"), int):
                ended_turns[record["turnId"]] = record
        elif t == "turn.prompt":
            skip_cancelled_turn_ids()
            tid = next_turn_id
            next_turn_id += 1
            if not _is_visible_turn_origin(record.get("origin")):
                hidden_turn_ids.add(tid)

    for iid, entity in list(interactions.items()):
        if entity["state"] == "pending":
            interactions[iid] = {**entity, "state": "cancelled"}

    ended_by_ordinal: dict = {}
    for tid, record in ended_turns.items():
        if tid in hidden_turn_ids:
            continue
        hidden = sum(1 for h in hidden_turn_ids if h < tid)
        ended_by_ordinal[tid - hidden] = record

    if ended_by_ordinal:
        new_items = []
        for item in base.items:
            if item.kind != "turn" or item.ordinal not in ended_by_ordinal:
                new_items.append(item)
                continue
            rec = ended_by_ordinal[item.ordinal]
            new_items.append(TranscriptTurn(**{
                **_turn_to_kwargs(item),
                "state": _map_turn_end_reason(rec.get("reason")) or item.state,
                "endedAt": _record_time_iso(rec) or item.ended_at,
                "durationMs": rec.get("durationMs") if isinstance(rec.get("durationMs"), int) else item.duration_ms,
                "error": _read_turn_error_message(rec.get("error")) or item.error,
            }))
        items = new_items
    else:
        items = base.items

    modes_touched = plan_active is not None or swarm_active is not None or tower_active is not None
    meta = TranscriptMeta(
        **{
            **_meta_kwargs(base.meta),
            "goal": (GoalMeta.from_dict(goal) if goal is not None else None) if goal_touched else base.meta.goal,
            "modes": (
                _ModesMeta(
                    plan=(plan_revision or {}) if plan_active else None if (plan_active is not None and not plan_active) else base.meta.modes.plan if base.meta.modes else None,
                    swarm={} if swarm_active else None if (swarm_active is not None and not swarm_active) else base.meta.modes.swarm if base.meta.modes else None,
                    tower={} if tower_active else None if (tower_active is not None and not tower_active) else base.meta.modes.tower if base.meta.modes else None,
                )
                if modes_touched
                else base.meta.modes
            ),
        },
    )

    return AgentTranscriptSnapshot(
        items=[*items, *appended] if appended else items,
        tasks=list(tasks.values()),
        interactions=list(interactions.values()),
        attachments=base.attachments,
        todos=[_Todo_from_dict(todo)] if todo is not None else base.todos,
        prompts=base.prompts,
        meta=meta,
        has_more_older=base.has_more_older,
    )


# foldFacts helpers ----------------------------------------------------------- #

_TASK_STATES = {"running", "completed", "failed", "timed_out", "killed", "lost"}
_GOAL_STATUSES = {"active", "paused", "blocked", "complete"}


def _is_visible_turn_origin(origin) -> bool:
    kind = (origin or {}).get("kind")
    if kind == "system_trigger":
        return (origin or {}).get("name") in ("goal_continuation", "subagent")
    if kind in ("skill_activation", "plugin_command"):
        return (origin or {}).get("trigger") == "user-slash"
    if kind in ("injection", "retry", "compaction_summary"):
        return False
    return True


def _map_task_kind(kind) -> str:
    if kind == "process":
        return "shell"
    if kind == "agent":
        return "subagent"
    return "other"


def _map_interaction_end_state(kind, response) -> str:
    if kind == "question":
        return "dismissed" if response is None else "answered"
    decision = (response or {}).get("decision")
    if decision in ("approved", "rejected", "cancelled"):
        return decision
    return "cancelled"


def _map_turn_end_reason(reason):
    if reason == "completed":
        return "completed"
    if reason == "cancelled":
        return "cancelled"
    if reason in ("failed", "blocked"):
        return "failed"
    return None


def _read_turn_error_message(error):
    if error is None or not isinstance(error, dict):
        return None
    message = error.get("message")
    return message if isinstance(message, str) else None


def _record_time_iso(record) -> Optional[str]:
    time = record.get("time")
    if isinstance(time, (int, float)) and math.isfinite(time):
        from datetime import datetime, timezone

        return datetime.fromtimestamp(time / 1000, tz=timezone.utc).isoformat()
    if isinstance(time, str):
        return time
    return None


def _epoch_ms_to_iso(value):
    if isinstance(value, (int, float)) and math.isfinite(value):
        from datetime import datetime, timezone

        return datetime.fromtimestamp(value / 1000, tz=timezone.utc).isoformat()
    return None


def _payload_of(record: dict) -> dict:
    return {k: v for k, v in record.items() if k not in ("type", "time")}


def _read_todo_items(raw) -> list:
    if not isinstance(raw, list):
        return []
    items = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        title = entry.get("title")
        status = entry.get("status")
        if not isinstance(title, str):
            continue
        if status not in ("pending", "in_progress", "done"):
            continue
        items.append({"title": title, "status": status})
    return items


def _turn_to_kwargs(turn):
    return {
        "kind": "turn",
        "turn_id": turn.turn_id,
        "ordinal": turn.ordinal,
        "state": turn.state,
        "origin": turn.origin,
        "prompt": turn.prompt,
        "attachment_ids": turn.attachment_ids,
        "steps": turn.steps,
        "started_at": turn.started_at,
        "ended_at": turn.ended_at,
        "usage": turn.usage,
        "duration_ms": turn.duration_ms,
        "error": turn.error,
    }


def _meta_kwargs(meta):
    return {
        "goal": meta.goal,
        "modes": meta.modes,
        "activity": meta.activity,
        "agent": meta.agent,
    }


def _ModesMeta(plan=None, swarm=None, tower=None):  # noqa: N802
    from .model import ModesMeta

    return ModesMeta(plan=plan, swarm=swarm, tower=tower)


def _Todo_from_dict(todo):
    from .model import TodoItem, TranscriptTodo

    return TranscriptTodo(
        todo_id=todo["todoId"],
        items=[TodoItem(title=i["title"], status=i["status"]) for i in todo["items"]],
        updated_at=todo.get("updatedAt"),
    )
