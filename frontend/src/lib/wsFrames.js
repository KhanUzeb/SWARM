/** Backend WebSocket frame contract, frontend copy.
 *
 * Source of truth: backend/main.py `hub.broadcast*` call sites. Every frame
 * type the backend can emit for a channel must appear in exactly one set
 * below; a type in no set is silently dropped by the socket handler and that
 * slice of UI goes stale until the next full reload.
 */

// Full message payloads merged into the log via ingestLive.
// Backend: ws_channel replay + api_post_message + _run_agent replies.
export const WS_MESSAGE_FRAME_TYPES = new Set(["message", "live"]);

// Reaction and deletion deltas merged via ingestLive.
// Backend: api_add_reaction ("reaction"), api_remove_reaction
// ("reaction_removed"), api_delete_message / retry ("message_deleted").
export const WS_DELTA_FRAME_TYPES = new Set(["message_deleted", "reaction", "reaction_removed"]);

// A decision landed in some room: the approvals inbox and the work rail
// must reload. Backend: api_resolve_approval broadcasts "approval" to all.
export const WS_APPROVAL_FRAME_TYPES = new Set(["approval"]);

// Roster-level change: the agent list must reload, and archived bots also
// invalidate the channel list. Backend: "agents_changed" on create,
// "bot_status" on approval resolve / status change, "bot_archived" on delete.
export const WS_ROSTER_FRAME_TYPES = new Set(["agents_changed", "bot_status", "bot_archived"]);

/** Outbox drain decision: queued messages may only send over a live socket,
 *  otherwise a flush during an outage would read as sent while still queued. */
export function shouldFlushOutbox(wsStatus, pendingCount) {
  return wsStatus === "connected" && pendingCount > 0;
}
