"""Budget-aware conversation context: history trim + memory + knowledge.

The old path passed `history[-window:]` straight to the model, so long
rooms silently blew past useful context and the knowledge base was never
consulted. `build_context` assembles one budgeted package — recent
history first, then memory notes, the channel summary, and knowledge
hits — and reports what was dropped so the UI can show a context meter.

Accounting contract: the `*_chars` breakdown keys describe prompt
content actually loaded, and they sum to `total_chars` exactly. Count
keys (`memory_notes`, `kb_hits`, `kb_docs`) describe store inventory or
package items — never treat a count as metered content.
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


def _history_entry_chars(message: dict[str, Any]) -> int:
    """Chars one history entry contributes to the metered package.

    `_build_messages` prefixes human turns with the speaker's name
    (`f"{author}: {body}"`), so that name is loaded prompt content; agent
    turns render the body alone. The two-char `": "` join is not metered.
    One helper so the pre-trim estimate, the trim loop, and the reported
    breakdown can never drift apart.
    """
    body = _chars(message.get("body"))
    if message.get("author_kind") == "agent":
        return body
    return body + _chars(message.get("author"))


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
    history_chars = sum(_history_entry_chars(m) for m in recent)
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
            total -= _history_entry_chars(dropped_msg)
            extra_dropped += 1

    # Loaded-content breakdown, recomputed from the kept package with the
    # same arithmetic as the estimate and trim loop above, so the four
    # char counts always sum to total_chars. `memory_notes`/`kb_hits`
    # count kept package items; `dropped_messages` counts window overflow
    # plus budget trims — they are counts, not metered characters.
    return {
        "history": trimmed_history,
        "notes": trimmed_notes,
        "summary": summary,
        "kb_hits": trimmed_kb,
        "stats": {
            "budget_chars": budget_chars,
            "total_chars": total,
            "history_chars": sum(_history_entry_chars(m) for m in trimmed_history),
            "notes_chars": sum(_chars(n.get("body")) for n in trimmed_notes),
            "summary_chars": _chars((summary or {}).get("body")),
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
    """Lightweight numbers for the composer's context meter (no model call).

    Two kinds of numbers, kept apart on purpose. `total_chars` and the
    `*_chars` keys describe prompt content actually loaded — the windowed
    history, plus the memory notes and channel summary `build_context`
    would inject (same 12-note window, same arithmetic). Count keys
    (`memory_notes`, `has_summary`, `kb_docs`) are store inventory: every
    note the agent holds in scope and every KB doc it owns, metered or
    not — reading an inventory count as loaded content is a UI bug.

    KB prompt content is deliberately absent: hits are chosen per turn
    from the turn query (last human line or routine tick) and then
    budget-trimmed, which this path cannot know without a turn. `kb_chars`
    stays None rather than implying an empty load.
    """
    from . import knowledge as kb_mod
    history = await db.get_history(channel_id, limit=max(window * 2, window))
    usable = [m for m in history if m.get("author_kind") != "system"]
    recent = usable[-window:]
    # Same metering as build_context so the meter tracks the package.
    chars = sum(_history_entry_chars(m) for m in recent)
    out: dict[str, Any] = {
        "channel_id": channel_id,
        "window": window,
        "budget_chars": BUDGET_CHARS,
        "history_chars": chars,
        "messages": len(recent),
        "dropped_messages": max(0, len(usable) - len(recent)),
        # Inventory defaults to unknown (None), not zero: without an agent
        # name there is no memory/KB owner to count, and a bare 0 would
        # imply an empty load rather than an uncounted one.
        "has_summary": None,
        "memory_notes": None,
        "kb_docs": None,
        # Turn-scoped: a per-turn KB query is required before this is knowable.
        "kb_chars": None,
    }
    if agent_name:
        counts = await db.count_memories(agent_name, channel_id)
        out["memory_notes"] = counts["notes"]
        try:
            notes, summary = await db.get_context_memories(agent_name, channel_id, 12)
        except Exception:  # noqa: BLE001
            notes, summary = [], None
        notes_chars = sum(_chars(n.get("body")) for n in (notes or []))
        summary_chars = _chars((summary or {}).get("body"))
        out["notes_chars"] = notes_chars
        out["summary_chars"] = summary_chars
        # What the turn path would keep before budget trimming: the
        # 12-note injection window plus the channel summary row it loads.
        out["notes_loaded"] = len(notes or [])
        # One accounting contract: same has_summary semantics as
        # build_context.stats, from the same loaded fetch.
        out["has_summary"] = bool(summary and summary.get("body"))
        chars += notes_chars + summary_chars
        try:
            out["kb_docs"] = await kb_mod.count_docs(f"agent:{agent_name}")
        except Exception:  # noqa: BLE001
            pass  # Unknown inventory stays None, never an empty 0
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
