"""Turn 分页 (对齐上游 paginate 的分页语义)。"""

from __future__ import annotations

from typing import Optional

from .ids import compare_turn_ids
from .model import TranscriptItem


def paginate_turns(items: list, query: dict) -> dict:
    page_size = max(1, query.get("pageSize", 1))
    segments = _split_segments(items)
    if not segments:
        return {"items": [], "hasMore": False}
    if query.get("afterTurn") is not None:
        return _page([s for s in segments if s["turnId"] and compare_turn_ids(s["turnId"], query["afterTurn"]) > 0], page_size, "newer")
    if query.get("beforeTurn") is not None:
        older = [s for s in segments if not s["turnId"] or compare_turn_ids(s["turnId"], query["beforeTurn"]) < 0]
        return _page(older, page_size, "older")
    return _page(segments, page_size, "older")


def _split_segments(items: list):
    segments = []
    current: list = []
    current_turn: Optional[str] = None
    for item in items:
        if item.kind == "turn":
            if current:
                segments.append({"items": current, "turnId": current_turn})
                current = []
                current_turn = None
            current = [item]
            current_turn = item.turn_id
        else:
            current.append(item)
    if current:
        segments.append({"items": current, "turnId": current_turn})
    return segments


def _page(segments: list, page_size: int, direction: str) -> dict:
    head = None if segments[0]["turnId"] is not None else segments[0]
    turn_segments = segments[1:] if head is not None else segments
    if direction == "older":
        selected = turn_segments[-page_size:]
        reaches_first = len(selected) == len(turn_segments)
        has_more = len(turn_segments) > len(selected) and len(selected) > 0
        out = []
        if reaches_first and head is not None:
            out.extend(head["items"])
        for s in selected:
            out.extend(s["items"])
        return {"items": out, "hasMore": has_more}
    selected = turn_segments[:page_size]
    has_more = len(turn_segments) > len(selected) and len(selected) > 0
    out = []
    for s in selected:
        out.extend(s["items"])
    return {"items": out, "hasMore": has_more}
