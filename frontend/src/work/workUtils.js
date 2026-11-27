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
  // An advisory is a warning, not a new step: work.py only raises
  // requires_action for work_advisory and leaves the session's active step
  // alone. Mirroring its step_id here made the rail claim the run had moved.
  if (event.step_id && event.type !== "work_advisory") next.active_step = event.step_id;
  return next;
}

/**
 * One printed line from an API error body. Most /api failures carry a string
 * detail, but handlers also return objects (e.g. a workflow graph error) and
 * a bare FastAPI validation array can reach the client if the custom 422
 * handler is bypassed. Interpolating either of those yields "[object Object]",
 * so flatten them here. Bounded on purpose: a hostile payload cannot flood
 * the alert. Pure so it can be tested under `bun test` without a DOM.
 */
export function apiErrorText(detail, fallback = "Could not start work") {
  const text = (value) => {
    if (typeof value === "string") return value.trim();
    if (typeof value === "number" || typeof value === "boolean") return String(value);
    return "";
  };
  if (Array.isArray(detail)) {
    const parts = detail.slice(0, 3).map((item) => {
      if (!item || typeof item !== "object") return text(item);
      const loc = Array.isArray(item.loc)
        ? item.loc.map(text).filter(Boolean).join(".")
        : text(item.loc);
      const msg = text(item.msg || item.message || item.detail);
      return [loc, msg].filter(Boolean).join(": ");
    }).filter(Boolean);
    if (!parts.length) return fallback;
    const extra = detail.length > 3 ? ` (+${detail.length - 3} more)` : "";
    return parts.join("; ") + extra;
  }
  if (detail && typeof detail === "object") {
    return text(detail.message || detail.msg || detail.detail) || fallback;
  }
  return text(detail) || fallback;
}

/** The exact alert text WorkHome prints: what failed, what survived, what next. */
export function launchFailureText(detail, fallback = "Could not start work") {
  return `${apiErrorText(detail, fallback)}. Your brief is still here — try again.`;
}

/**
 * One printed line for a non-terminal work advisory. The backend keeps the
 * run's own kind (step_failed, execution_ambiguous, ...) and a detail; both
 * are read as given so a warning reads as a warning, never as a completed or
 * failed outcome. Bounded like every other printed line.
 */
export function advisoryDetail(event) {
  const payload = event?.payload || {};
  const kind = typeof payload.kind === "string" ? payload.kind.replace(/_/g, " ").trim() : "";
  const detail = typeof payload.detail === "string" ? payload.detail.trim() : "";
  return [kind, detail].filter(Boolean).join(": ").slice(0, 180);
}

const ACTIVITY_LABELS = {
  run_queued: "Queued",
  run_started: "Started",
  step_started: "Step started",
  step_completed: "Step finished",
  step_failed: "Step failed",
  step_skipped: "Step skipped",
  tool_finished: "Tool finished",
  approval_requested: "Needs you",
  approval_resolved: "Decision recorded",
  approval_reconciled: "Decision reconciled",
  run_completed: "Done",
  run_failed: "Failed",
  run_cancelled: "Cancelled",
  execution_ambiguous: "Needs review",
  approval_ambiguous: "Needs review",
  approval_binding_error: "Needs review",
  work_advisory: "Attention needed",
};

function firstText(...values) {
  for (const value of values) {
    if (typeof value === "string" && value.trim()) return value.trim();
  }
  return "";
}

/**
 * One printed line for a durable work event: a plain label plus its real
 * outcome. The Activity tab used to show only the raw event_type and step id,
 * so a failed step looked identical to a completed one and the reason was
 * invisible. Payload text is untrusted and bounded; React renders it as text.
 */
export function activitySummary(event) {
  const type = String(event?.event_type || event?.type || "");
  const payload = event?.payload || {};
  const label = ACTIVITY_LABELS[type] || type.replace(/_/g, " ") || "Event";
  let detail = "";
  if (type === "work_advisory") {
    detail = advisoryDetail(event);
  } else if (type === "step_failed" || type === "run_failed" || type === "work_failed") {
    detail = firstText(payload.error, payload.message, payload.detail);
  } else if (type === "run_cancelled" || type === "work_cancelled") {
    detail = firstText(payload.reason, payload.detail);
  } else if (type === "step_completed") {
    detail = firstText(payload.output, payload.label);
  } else if (type === "run_completed" || type === "work_completed") {
    detail = firstText(payload.summary);
  } else if (type === "approval_requested") {
    detail = firstText(payload.label, payload.reason, payload.canonical_args?.label);
  } else if (type === "step_skipped") {
    detail = firstText(payload.label, payload.reason);
  } else if (type === "step_started") {
    detail = firstText(payload.label);
  } else if (type === "tool_finished") {
    detail = firstText(payload.tool, payload.name, payload.label);
  } else if (type === "execution_ambiguous" || type === "approval_ambiguous" || type === "approval_binding_error") {
    detail = firstText(payload.detail);
  }
  if (!detail) detail = firstText(event?.step_id, payload.agent) || "run";
  return { label, detail: detail.slice(0, 180) };
}

/**
 * What a failed run actually completed, from its durable step_completed events.
 * The failure card used to label the first two plan entries as Completed and
 * the current step as the only missing one, which claimed work that never
 * happened and hid steps that were still outstanding. Pure so this evidence
 * mapping can be tested without a DOM.
 */
export function failureSurvivors(plan, evidence, currentStep) {
  const completed = new Set(
    (evidence || [])
      .filter(e => e?.type === "step_completed" && e.step_id)
      .map(e => e.step_id),
  );
  const ordered = [...new Set([...(plan || []), ...completed])].filter(Boolean);
  const done = ordered.filter(step => completed.has(step));
  let missing = ordered.filter(step => !completed.has(step));
  if (!ordered.length && currentStep) missing = [currentStep];
  return { done, missing };
}

/**
 * Movement inside a tablist. Both work strips declared `role="tablist"` but
 * only answered clicks, so a keyboard user had to Tab through every control
 * to reach the evidence tabs while the role promised arrow keys. Returning
 * null for every other key leaves Tab/Enter/Space to the browser. Pure so the
 * contract is testable without a DOM.
 */
/**
 * The first descriptor with actual fields. `approval_state` decodes a missing
 * or malformed canonical_args column as `{}`, which is truthy: using it first
 * hid the agent, scope and label printed in the approval_requested event and
 * replaced them with generic fallbacks. Never let an empty record mask the
 * exact data the human is being asked to approve.
 */
export function approvalDescriptor(...candidates) {
  for (const value of candidates) {
    if (value && typeof value === "object" && !Array.isArray(value) && Object.keys(value).length) {
      return value;
    }
  }
  return {};
}

export function tabIndexForKey(current, count, key) {
  if (!count || typeof key !== "string") return null;
  const at = Number.isInteger(current) && current >= 0 && current < count ? current : 0;
  switch (key) {
    case "ArrowRight":
    case "ArrowDown": return (at + 1) % count;
    case "ArrowLeft":
    case "ArrowUp": return (at - 1 + count) % count;
    case "Home": return 0;
    case "End": return count - 1;
    default: return null;
  }
}
