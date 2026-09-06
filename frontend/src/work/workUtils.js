// Pure work-session helpers — no React/DOM imports so they run under `bun test`.
export const WORK_ACTIVE = new Set(["queued", "running", "waiting_for_approval"]);
export const WORK_TERMINAL = new Set(["completed", "failed", "cancelled"]);

export function workStatusTone(status) {
  switch (status) {
    case "queued": return "subtle";
    case "running": return "intelligence";
    case "waiting_for_approval": return "warning";
    case "completed": return "success";
    case "failed": return "danger";
    case "cancelled": return "subtle";
    default: return "subtle";
  }
}

export function workStatusLabel(status) {
  const map = {
    queued: "Queued", running: "Running", waiting_for_approval: "Needs approval",
    completed: "Completed", failed: "Failed", cancelled: "Cancelled",
  };
  return map[status] || status || "Unknown";
}

/** Merge incoming replayed events into a sorted, deduped-by-seq list. */
export function mergeWorkEvents(existing, incoming) {
  const seen = new Map((existing || []).map(e => [e.seq, e]));
  for (const e of incoming || []) {
    if (e && typeof e.seq === "number" && !seen.has(e.seq)) seen.set(e.seq, e);
    else if (e && typeof e.seq === "number") seen.set(e.seq, { ...seen.get(e.seq), ...e });
  }
  return [...seen.values()].sort((a, b) => a.seq - b.seq);
}

/** Order sessions so anything needing attention surfaces first. */
export function groupWorkByAttention(sessions) {
  const rank = (s) => {
    if (s.requires_action || s.status === "waiting_for_approval") return 0;
    if (s.status === "failed") return 1;
    if (s.status === "running") return 2;
    if (s.status === "queued") return 3;
    return 4;
  };
  return [...(sessions || [])].sort((a, b) => rank(a) - rank(b) || (b.created_at || 0) - (a.created_at || 0));
}

/** Pure status transition for one incoming work event. */
export function applyWorkEventToSession(session, event) {
  if (!session || session.id !== event?.work_id) return session;
  const next = { ...session };
  if (event.type === "work_started" || event.type === "agent_started") next.status = "running";
  if (event.type === "approval_requested") { next.status = "waiting_for_approval"; next.requires_action = true; }
  if (event.type === "approval_resolved") { next.status = "running"; next.requires_action = false; }
  if (event.type === "work_completed") { next.status = "completed"; next.requires_action = false; }
  if (event.type === "work_failed") { next.status = "failed"; next.requires_action = true; }
  if (event.type === "work_cancelled") { next.status = "cancelled"; next.requires_action = false; }
  if (event.step_id) next.active_step = event.step_id;
  return next;
}
