"""Budget-aware conversation context: history trim + memory + knowledge.

The old path passed `history[-window:]` straight to the model, so long
rooms silently blew past useful context and the knowledge base was never
consulted. `build_context` assembles one budgeted package — recent
history first, then memory notes, the channel summary, and knowledge
hits — and reports what was dropped so the UI can show a context meter.
"""
from __future__ import annotations

from typing import Any

from . import db

# Rough char budget for the assembled prompt context (~3k tokens).
BUDGET_CHARS = 12_000
CHARS_PER_MESSAGE = 600
MEMORY_CHARS = 2_000
KB_CHARS = 2_500


def _chars(value: str | None) -> int:
    return len(value or "")


def _last_human_text(history: list[dict[str, Any]]) -> str:
    for entry in reversed(history or []):
        if entry.get("author_kind") == "human" and (entry.get("body") or "").strip():
            return str(entry["body"])
    return ""


async def build_context(
    agent_name: str,
    channel_id: str,
    history: list[dict[str, Any]],
    *,
    window: int = 12,
    budget_chars: int = BUDGET_CHARS,
    kb_query: str | None = None,
    kb_limit: int = 3,
) -> dict[str, Any]:
    """Assemble the model context package for one reply turn."""
    window = max(1, min(int(window or 12), 50))
    usable = [m for m in (history or []) if m.get("author_kind") != "system"]
    recent = usable[-window:]
    dropped = max(0, len(usable) - len(recent))

    notes, summary = await db.get_context_memories(agent_name, channel_id, 12)

    query = (kb_query or _last_human_text(history))[:300]
    kb_hits: list[dict[str, Any]] = []
    if query.strip():
        from . import knowledge as kb_mod
        try:
            kb_hits = await kb_mod.search_docs(
                query, owners=[f"agent:{agent_name}"], channel_id=channel_id, limit=kb_limit)
        except Exception:  # noqa: BLE001 — knowledge is best-effort
            kb_hits = []

    # Channel-scoped hits are boosted: they sort first and are the last
    # KB entries dropped when the budget bites.
    for hit in kb_hits:
        hit["boosted"] = bool(channel_id) and hit.get("channel_id") == channel_id
    kb_hits.sort(key=lambda h: (not h.get("boosted"), -(h.get("updated_at") or 0)))

    # Enforce the char budget: history first, then notes, then KB.
    history_chars = sum(_chars(m.get("body")) + _chars(m.get("author")) for m in recent)
    notes_chars = sum(_chars(n.get("body")) for n in (notes or []))
    kb_chars = sum(_chars(h.get("body")) for h in kb_hits)
    summary_chars = _chars((summary or {}).get("body"))
    total = history_chars + notes_chars + summary_chars + kb_chars

    trimmed_notes = list(notes or [])
    trimmed_kb = list(kb_hits)
    trimmed_history = list(recent)
    extra_dropped = 0
    while total > budget_chars and (len(trimmed_history) > 1 or trimmed_notes or trimmed_kb):
        if any(not h.get("boosted") for h in trimmed_kb):
            # Drop an unboosted hit first (last one).
            for idx in range(len(trimmed_kb) - 1, -1, -1):
                if not trimmed_kb[idx].get("boosted"):
                    dropped_hit = trimmed_kb.pop(idx)
                    total -= _chars(dropped_hit.get("body"))
                    break
        elif trimmed_kb:
            dropped_hit = trimmed_kb.pop()
            total -= _chars(dropped_hit.get("body"))
        elif trimmed_notes:
            dropped_note = trimmed_notes.pop(0)
            total -= _chars(dropped_note.get("body"))
        else:
            dropped_msg = trimmed_history.pop(0)
            total -= _chars(dropped_msg.get("body")) + _chars(dropped_msg.get("author"))
            extra_dropped += 1

    return {
        "history": trimmed_history,
        "notes": trimmed_notes,
        "summary": summary,
        "kb_hits": trimmed_kb,
        "stats": {
            "budget_chars": budget_chars,
            "total_chars": total,
            "history_chars": sum(_chars(m.get("body")) for m in trimmed_history),
            "notes_chars": sum(_chars(n.get("body")) for n in trimmed_notes),
            "kb_chars": sum(_chars(h.get("body")) for h in trimmed_kb),
            "messages": len(trimmed_history),
            "dropped_messages": dropped + extra_dropped,
            "has_summary": bool(summary and summary.get("body")),
            "memory_notes": len(trimmed_notes),
            "kb_hits": len(trimmed_kb),
            "over_budget": total > budget_chars,
        },
    }


async def context_stats(
    channel_id: str, agent_name: str | None = None, window: int = 12
) -> dict[str, Any]:
    """Lightweight numbers for the composer's context meter (no model call)."""
    from . import knowledge as kb_mod
    history = await db.get_history(channel_id, limit=max(window * 2, window))
    usable = [m for m in history if m.get("author_kind") != "system"]
    recent = usable[-window:]
    chars = sum(_chars(m.get("body")) for m in recent)
    out: dict[str, Any] = {
        "channel_id": channel_id,
        "window": window,
        "budget_chars": BUDGET_CHARS,
        "history_chars": chars,
        "messages": len(recent),
        "dropped_messages": max(0, len(usable) - len(recent)),
        "has_summary": False,
        "memory_notes": 0,
        "kb_docs": 0,
    }
    if agent_name:
        counts = await db.count_memories(agent_name, channel_id)
        out["memory_notes"] = counts["notes"]
        out["has_summary"] = counts["summaries"] > 0
        try:
            out["kb_docs"] = await kb_mod.count_docs(f"agent:{agent_name}")
        except Exception:  # noqa: BLE001
            out["kb_docs"] = 0
        # Count what build_context counts so the meter tracks the real
        # package: memory note + summary bodies on top of history.
        try:
            notes, summary = await db.get_context_memories(agent_name, channel_id, 12)
        except Exception:  # noqa: BLE001
            notes, summary = [], None
        notes_chars = sum(_chars(n.get("body")) for n in (notes or []))
        summary_chars = _chars((summary or {}).get("body"))
        out["notes_chars"] = notes_chars
        out["summary_chars"] = summary_chars
        chars += notes_chars + summary_chars
    out["total_chars"] = chars
    return out


async def compact_channel(channel_id: str, agent_name: str, window: int = 12) -> dict[str, Any] | None:
    """Roll older history into the channel summary now; returns the summary."""
    from .agent import compact_summary
    history = await db.get_history(channel_id, limit=200)
    text = compact_summary(history, window)
    if not text:
        return None
    return await db.append_summary(agent_name, channel_id, text)
