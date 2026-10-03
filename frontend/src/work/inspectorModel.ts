/**
 * Pure folding of the backend's work_events vocabulary into the ordered
 * steps the run inspector prints. No React, no DOM: everything here is a
 * function of what backend/work.py actually persisted, so the inspector can
 * never show a step the backend did not record.
 *
 * Source of truth (do not extend these lists without a backend change):
 *   work.py ALLOWED_TYPES  work_queued work_started agent_started
 *                          tool_started tool_finished approval_requested
 *                          approval_resolved message_linked artifact_created
 *                          work_completed work_failed work_cancelled
 *   main.py _run_agent payloads: {objective,source} {objective,agent}
 *                          {agent,role} {tool} {tool,result} {label,approval_id}
 *                          {message_id,agent} {summary} {error}
 *
 * Two facts about the data this file is careful about:
 *  1. Tool ARGUMENTS are not in work_events. The backend writes them once,
 *     as the in-channel `system` audit line "bot ran: tool(args) -> result"
 *     (main.py persist_tools). We pair those lines by position and only when
 *     the counts match exactly; otherwise args stay absent rather than
 *     guessed.
 *  2. Tokens and cost are never persisted (generate_reply returns `usage`
 *     but _run_agent does not store it), so usageTotals() returns null and
 *     the inspector says so instead of inventing a number.
 */

export type Payload = {
  tool?: string;
  agent?: string;
  role?: string;
  result?: string;
  error?: string;
  summary?: string;
  objective?: string;
  source?: string;
  label?: string;
  decision?: string;
  approval_id?: number;
  message_id?: number;
  usage?: { prompt_tokens?: number; completion_tokens?: number } | null;
  [key: string]: unknown;
};

export type WorkEvent = {
  seq: number;
  type: string;
  step_id?: string | null;
  payload?: Payload | null;
  created_at?: number | string | null;
};

export type WorkSession = {
  id: string;
  status?: string;
  objective?: string;
  source?: string;
  channel_id?: string | null;
  agent_name?: string | null;
  created_at?: number | string | null;
  started_at?: number | string | null;
  finished_at?: number | string | null;
};

export type LampTone = "go" | "hold" | "amber" | "off";

export type InspectorStep = {
  key: string;
  seq: number;
  kind: "run" | "model" | "handoff" | "tool" | "approval" | "reply" | "artifact" | "state";
  label: string;
  detail: string;
  tone: LampTone;
  at: number;
  ms: number | null;
  args: string | null;
  output: string | null;
  failure: boolean;
};

export type AuditLine = { tool: string; args: string; result: string };

export function ms(value: unknown): number {
  if (value == null || value === "") return 0;
  if (value instanceof Date) return value.getTime() || 0;
  if (typeof value === "number") return value < 1e12 ? value * 1000 : value;
  const text = String(value).trim();
  if (!text) return 0;
  const n = Number(text);
  if (Number.isFinite(n)) return n < 1e12 ? n * 1000 : n;
  const parsed = Date.parse(text);
  return Number.isFinite(parsed) ? parsed : 0;
}

const ERROR_HINT = /error|failed|denied|timeout|exception|traceback|refused/i;

function toneForEvent(e: WorkEvent): LampTone {
  switch (e.type) {
    case "work_queued":
      return "off";
    case "work_started":
    case "agent_started":
    case "tool_started":
      return "amber";
    case "tool_finished":
    case "message_linked":
    case "artifact_created":
    case "work_completed":
      return "go";
    case "approval_requested":
    case "work_failed":
      return "hold";
    case "approval_resolved":
      return e.payload?.decision === "approved" ? "go" : "hold";
    case "work_cancelled":
      return "off";
    default:
      return "off";
  }
}

/**
 * Parse the one audit line shape the backend writes for a tool call:
 *   "<bot> ran: <tool>(<args>) -> <result>"
 * Args are Python reprs, kept verbatim as machine register — never parsed.
 */
export function parseAuditLine(body: string): AuditLine | null {
  const text = String(body || "");
  const match = /ran:\s*([A-Za-z0-9_.-]+)\(([\s\S]*)\)\s*->\s*([\s\S]*)$/.exec(text);
  if (!match) return null;
  return { tool: match[1], args: match[2], result: match[3].trim() };
}

/** Fold the event log into ordered steps. Pure; safe to memoise on events. */
export function buildSteps(events: WorkEvent[] | null | undefined, audit: AuditLine[] = []): InspectorStep[] {
  const list = [...(events || [])].sort((a, b) => (a.seq || 0) - (b.seq || 0));
  const steps: InspectorStep[] = [];
  const openTools: InspectorStep[] = [];
  const agents: string[] = [];

  for (const e of list) {
    const p = e.payload || {};
    const at = ms(e.created_at);
    const base = {
      key: `${e.seq}:${e.type}`,
      seq: e.seq,
      at,
      ms: null as number | null,
      args: null as string | null,
      output: null as string | null,
      failure: false,
      tone: toneForEvent(e),
    };

    if (e.type === "work_queued") {
      steps.push({ ...base, kind: "run", label: "Queued", detail: String(p.objective || p.source || "") });
    } else if (e.type === "work_started") {
      steps.push({ ...base, kind: "run", label: "Run started", detail: String(p.objective || "") });
    } else if (e.type === "agent_started") {
      const agent = String(p.agent || "");
      const handoff = agents.length > 0 && agent && agents[agents.length - 1] !== agent;
      if (agent) agents.push(agent);
      steps.push({
        ...base,
        kind: handoff ? "handoff" : "model",
        label: handoff ? `Handoff to ${agent}` : "Model call",
        detail: [agent ? `@${agent}` : "", String(p.role || "")].filter(Boolean).join(" "),
      });
    } else if (e.type === "tool_started") {
      const tool = String(p.tool || e.step_id || "tool");
      const step: InspectorStep = { ...base, kind: "tool", label: tool, detail: "" };
      steps.push(step);
      openTools.push(step);
    } else if (e.type === "tool_finished") {
      const tool = String(p.tool || e.step_id || "tool");
      const idx = openTools.findIndex((s) => s.label === tool);
      const step = idx >= 0 ? openTools.splice(idx, 1)[0] : null;
      if (step) {
        step.output = String(p.result || "");
        step.tone = ERROR_HINT.test(step.output) ? "hold" : "go";
        step.ms = at && step.at ? Math.max(0, at - step.at) : null;
      } else {
        steps.push({ ...base, kind: "tool", label: tool, detail: "", output: String(p.result || "") });
      }
    } else if (e.type === "approval_requested") {
      steps.push({
        ...base,
        kind: "approval",
        label: String(p.label || "Approval requested"),
        detail: p.approval_id != null ? `approval #${p.approval_id}` : "",
      });
    } else if (e.type === "approval_resolved") {
      steps.push({
        ...base,
        kind: "approval",
        label: `Approval ${p.decision || "resolved"}`,
        detail: p.approval_id != null ? `approval #${p.approval_id}` : "",
      });
    } else if (e.type === "message_linked") {
      steps.push({
        ...base,
        kind: "reply",
        label: "Reply posted",
        detail: p.message_id != null ? `message #${p.message_id}` : "",
      });
    } else if (e.type === "artifact_created") {
      steps.push({ ...base, kind: "artifact", label: "Artifact created", detail: String(p.name || "") });
    } else if (e.type === "work_completed") {
      steps.push({ ...base, kind: "state", label: "Completed", detail: String(p.summary || "") });
    } else if (e.type === "work_failed") {
      steps.push({
        ...base,
        kind: "state",
        label: "Failed",
        detail: String(p.error || ""),
        tone: "hold",
        failure: true,
        output: String(p.error || ""),
      });
    } else if (e.type === "work_cancelled") {
      steps.push({ ...base, kind: "state", label: "Cancelled", detail: String((p as Payload).reason || "") });
    }
  }

  return attachArgs(steps, audit);
}

/**
 * Tool arguments live only in the in-channel audit lines, one per tool call,
 * in the same order the backend emitted them. Pair them only on an exact
 * count match; a mismatch leaves every row without args, which is honest.
 */
function attachArgs(steps: InspectorStep[], audit: AuditLine[]): InspectorStep[] {
  const toolSteps = steps.filter((s) => s.kind === "tool");
  const lines = audit.filter((l) => l && l.tool);
  if (toolSteps.length === 0 || toolSteps.length !== lines.length) return steps;
  for (let i = 0; i < toolSteps.length; i++) {
    const line = lines[i];
    if (line.tool !== toolSteps[i].label) return steps; // order drifted: trust nothing
    toolSteps[i].args = line.args;
  }
  return steps;
}

/**
 * The recorded request_approval arguments behind one approval row. The
 * backend emits tool_started/tool_finished for request_approval immediately
 * before the approval_requested event for the row it just created, so the
 * nearest preceding request_approval step is that row's call.
 */
export function argsForApproval(events: WorkEvent[] | null | undefined, audit: AuditLine[], approvalId: number | string): AuditLine | null {
  const list = [...(events || [])].sort((a, b) => (a.seq || 0) - (b.seq || 0));
  const gate = list.findIndex((e) => e.type === "approval_requested" && String(e.payload?.approval_id) === String(approvalId));
  if (gate < 0) return null;
  const toolSteps = list.filter((e) => e.type === "tool_started").slice(0, gate);
  for (let i = toolSteps.length - 1; i >= 0; i--) {
    if (String(toolSteps[i].payload?.tool || toolSteps[i].step_id || "") === "request_approval") {
      const toolIndex = list.filter((e) => e.type === "tool_started").indexOf(toolSteps[i]);
      const lines = audit.filter((l) => l && l.tool);
      return lines.length === list.filter((e) => e.type === "tool_started").length ? lines[toolIndex] || null : null;
    }
  }
  return null;
}

/** Wall time of the run: created → finished, else created → last event. */
export function runDuration(session: WorkSession | null | undefined, events: WorkEvent[] | null | undefined): number | null {
  if (!session) return null;
  const first = ms(session.created_at);
  const terminal = session.status === "completed" || session.status === "failed" || session.status === "cancelled";
  const last = terminal && ms(session.finished_at)
    ? ms(session.finished_at)
    : Math.max(ms(session.finished_at), ...(events || []).map((e) => ms(e.created_at)), 0);
  if (!first || !last) return null;
  return Math.max(0, last - first);
}

export function fmtDuration(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value) || value < 0) return "";
  const s = Math.round(value / 1000);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ${String(s % 60).padStart(2, "0")}s`;
  return `${Math.floor(m / 60)}h ${String(m % 60).padStart(2, "0")}m`;
}

/**
 * Token counts, only if an event ever carries them. Today the backend stores
 * none, so this is null and the inspector states that plainly instead of
 * showing a fabricated number.
 */
export function usageTotals(events: WorkEvent[] | null | undefined): { prompt: number; completion: number } | null {
  let prompt = 0;
  let completion = 0;
  let seen = false;
  for (const e of events || []) {
    const u = e.payload?.usage;
    if (!u) continue;
    const p = Number(u.prompt_tokens);
    const c = Number(u.completion_tokens);
    if (Number.isFinite(p)) { prompt += p; seen = true; }
    if (Number.isFinite(c)) { completion += c; seen = true; }
  }
  return seen ? { prompt, completion } : null;
}

/** The run's own flap face: live, done, failed, or held for a human. */
export function runFlap(status?: string): { value: string; tone: "go" | "hold" | "amber" | "unlit" } {
  switch (status) {
    case "waiting_for_approval":
      return { value: "Hold", tone: "hold" };
    case "running":
      return { value: "Run", tone: "amber" };
    case "queued":
      return { value: "Queue", tone: "unlit" };
    case "completed":
      return { value: "Done", tone: "go" };
    case "failed":
      return { value: "Fail", tone: "hold" };
    case "cancelled":
      return { value: "Stop", tone: "unlit" };
    default:
      return { value: status || "Idle", tone: "unlit" };
  }
}

/**
 * The bot a session belongs to, as the backend recorded it on the events.
 */
export function agentOfSession(session: WorkSession | null | undefined, events: WorkEvent[] | null | undefined): string {
  for (const e of events || []) {
    const agent = e.payload?.agent;
    if (agent) return String(agent);
  }
  return String(session?.agent_name || "");
}

/** The reply message a session produced, from its message_linked event. */
export function linkedMessageId(session: WorkSession | null | undefined, events: WorkEvent[] | null | undefined): number | null {
  let found: number | null = null;
  for (const e of events || []) {
    if (e.type !== "message_linked") continue;
    const id = Number(e.payload?.message_id);
    if (Number.isFinite(id)) found = id;
  }
  return found;
}

/**
 * Attribute the backend's tool audit lines to the run they belong to.
 *
 * main.py persist_tools writes one `system` line per tool call *before* the
 * run's reply is persisted, and the reply is what `message_linked` records.
 * So each run owns the contiguous block of its own bot's system lines that
 * sits between the previous run's reply and its own. Runs with no linked
 * reply (still running, or failed) own the trailing block after the last
 * known reply. Nothing is inferred across agents or channels.
 */
export function pairAuditLines(
  sessions: WorkSession[],
  eventsByWork: Record<string, WorkEvent[]>,
  messages: Record<string | number, { author?: string; author_kind?: string; body?: string }>,
  order: Array<string | number>,
  channelId: string,
): Record<string, AuditLine[]> {
  const runs = sessions
    .filter((s) => s && (!s.channel_id || s.channel_id === channelId))
    .map((s) => {
      const events = eventsByWork[s.id] || [];
      return {
        workId: s.id,
        agent: agentOfSession(s, events),
        replyId: linkedMessageId(s, events),
        active: !["completed", "failed", "cancelled"].includes(s.status || ""),
      };
    })
    .filter((r) => r.agent)
    .sort((a, b) => (a.replyId ?? Infinity) - (b.replyId ?? Infinity));

  if (runs.length === 0) return {};

  const lines = order
    .map((id) => {
      const m = messages[id];
      if (!m || m.author_kind !== "system" || !m.body) return null;
      const parsed = parseAuditLine(m.body);
      return parsed ? { id: Number(id), author: m.author, line: parsed } : null;
    })
    .filter(Boolean) as Array<{ id: number; author?: string; line: AuditLine }>;

  const out: Record<string, AuditLine[]> = {};
  let cursor = -Infinity;
  for (const run of runs) {
    const upper = run.replyId ?? Infinity;
    const own = lines.filter((l) => l.author === run.agent && l.id > cursor && l.id < upper);
    if (own.length) out[run.workId] = own.map((l) => l.line);
    if (Number.isFinite(upper)) cursor = upper;
    else break; // a run with no reply owns the remainder; nothing follows it
  }
  return out;
}

/** Lines of a unified diff inside an approval's recorded text, if any. */
export function diffLines(text: unknown): string[] {
  const lines = String(text || "").split(/\r?\n/);
  const hits = lines.filter((l) => /^(diff --git |index [0-9a-f]{7,}|--- |\+\+\+ |@@ )/.test(l) || /^[+-][^+-]/.test(l));
  return hits.length >= 2 ? hits : [];
}