// Pure work-session helpers — no React/DOM imports so they run under `bun test`.
export const WORK_ACTIVE = new Set(["queued", "running", "waiting_for_approval"]);
export const WORK_TERMINAL = new Set(["completed", "failed", "cancelled"]);

// One shared map drives tone + label so new statuses can't drift apart.
const WORK_STATUS_META = {
  queued: { tone: "subtle", label: "Queued" },
  running: { tone: "intelligence", label: "Running" },
  waiting_for_approval: { tone: "warning", label: "Needs approval" },
  completed: { tone: "success", label: "Completed" },
  failed: { tone: "danger", label: "Failed" },
  cancelled: { tone: "subtle", label: "Cancelled" },
};

export function workStatusTone(status) {
  return (WORK_STATUS_META[status] || {}).tone || "subtle";
}

export function workStatusLabel(status) {
  return (WORK_STATUS_META[status] || {}).label || status || "Unknown";
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
const WORK_EVENT_TRANSITIONS = {
  work_started: { status: "running" },
  agent_started: { status: "running" },
  approval_requested: { status: "waiting_for_approval", requires_action: true },
  approval_resolved: { status: "running", requires_action: false },
  work_completed: { status: "completed", requires_action: false },
  work_failed: { status: "failed", requires_action: true },
  work_cancelled: { status: "cancelled", requires_action: false },
};

export function applyWorkEventToSession(session, event) {
  if (!session || session.id !== event?.work_id) return session;
  const transition = WORK_EVENT_TRANSITIONS[event.type];
  const next = transition ? { ...session, ...transition } : { ...session };
  if (event.step_id) next.active_step = event.step_id;
  return next;
}
