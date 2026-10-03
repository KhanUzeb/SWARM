/**
 * Pure helpers for the approvals UX. Everything here reads only what the
 * backend actually stores on an approvals row (agent_name, channel_id,
 * action, detail, status, created_at, resolved_at) plus the tool arguments
 * recorded for the request_approval call in the work event log.
 *
 * There is no structured `command` or `diff` column in backend/db.py, so the
 * prompt shows the recorded action and detail verbatim and renders a diff
 * block only when the detail actually contains diff lines. Nothing is
 * reconstructed or invented — if the bot did not spell out what it will run,
 * the panel says the request recorded no detail.
 */

import type { AuditLine, WorkEvent } from "./inspectorModel";
import { argsForApproval, diffLines, ms } from "./inspectorModel";

export type Approval = {
  id: number | string;
  agent_name?: string;
  channel_id?: string;
  action?: string;
  detail?: string;
  status?: string;
  created_at?: number | string | null;
  resolved_at?: number | string | null;
};

export type ApprovalRun = {
  workId: string;
  objective?: string;
  status?: string;
  channel_id?: string | null;
  created_at?: number | string | null;
};

export type ApprovalShape = "command" | "write" | "outbound" | "unspecified";

const COMMAND_HINT = /(^|\s)(rm|mv|cp|git|npm|bun|pnpm|yarn|curl|wget|ssh|scp|docker|kubectl|systemctl|chmod|chown|pip|python|bash|sh|sudo)\b|^\s*\S+\s+--|https?:\/\/\S+/i;
const WRITE_HINT = /\b(write|edit|patch|commit|merge|pr|pull request|diff|file|branch|deploy|publish|schema|migration)\b/i;
const OUTBOUND_HINT = /\b(post|send|email|dm|slack|publish|announce|tweet|status page|external|webhook)\b/i;

export function approvalShape(approval: Approval): ApprovalShape {
  const text = `${approval?.action || ""} ${approval?.detail || ""}`;
  if (!text.trim()) return "unspecified";
  if (COMMAND_HINT.test(text)) return "command";
  if (WRITE_HINT.test(text)) return "write";
  if (OUTBOUND_HINT.test(text)) return "outbound";
  return "unspecified";
}

export function shapeLabel(shape: ApprovalShape): string {
  switch (shape) {
    case "command":
      return "Shell command";
    case "write":
      return "File change";
    case "outbound":
      return "Outbound action";
    default:
      return "Action";
  }
}

/**
 * The work session an approval belongs to: same channel, still live, and the
 * one whose agent asked for it. Returns null when nothing matches, and the
 * caller says "no run recorded" rather than guessing a run.
 */
export function runForApproval(
  approval: Approval,
  runs: ApprovalRun[] | null | undefined,
): ApprovalRun | null {
  const pool = (runs || []).filter(
    (r) => r && r.workId && (!approval?.channel_id || r.channel_id === approval.channel_id),
  );
  const live = pool.filter((r) => ["queued", "running", "waiting_for_approval"].includes(r.status || ""));
  const candidates = live.length ? live : pool;
  if (candidates.length === 0) return null;
  // Prefer the most recent run that started no later than the approval.
  const asked = ms(approval?.created_at);
  const ordered = [...candidates].sort(
    (a, b) => ms(b.created_at || 0) - ms(a.created_at || 0),
  );
  if (!asked) return ordered[0];
  const before = ordered.filter((r) => ms(r.created_at || 0) <= asked);
  return before[0] || ordered[0];
}

export type ApprovalDetail = {
  call: AuditLine | null;
  args: Array<{ key: string; value: string }>;
  diff: string[];
  hasCommand: boolean;
  command: string | null;
};

/**
 * Parse the recorded request_approval arguments. Values arrive as a Python
 * repr inside the audit line; they are kept as printed machine text, never
 * eval'd or re-parsed into structure we would then trust.
 */
export function approvalDetail(
  approval: Approval,
  events: WorkEvent[] | null | undefined,
  audit: AuditLine[],
): ApprovalDetail {
  const call = argsForApproval(events, audit, approval?.id);
  const raw = call?.args || "";
  const args = splitArgs(raw);
  const body = `${raw}\n${approval?.detail || ""}`;
  const diff = diffLines(body);
  const command = pickCommand(raw, approval);
  return { call, args, diff, hasCommand: !!command, command };
}

/** `{'action': 'x', 'detail': 'y'}` -> [{key,value}] without evaluating it. */
function splitArgs(raw: string): Array<{ key: string; value: string }> {
  const text = String(raw || "").trim();
  if (!text) return [];
  const inner = text.startsWith("{") && text.endsWith("}") ? text.slice(1, -1) : text;
  const out: Array<{ key: string; value: string }> = [];
  const re = /(['"])(.*?)\1\s*:\s*((?:'(?:[^'\\]|\\.)*'|"(?:[^"\\]|\\.)*")|(?:[^,}]+))/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(inner)) !== null) {
    const value = m[3].trim();
    out.push({ key: m[2], value: stripQuotes(value) });
  }
  return out;
}

function stripQuotes(value: string): string {
  const text = String(value).trim();
  if (text.length >= 2 && /^['"]/.test(text) && /['"]$/.test(text)) {
    return text.slice(1, -1).replace(/\\(['"\\])/g, "$1").replace(/\\n/g, "\n");
  }
  return text;
}

/** A command line only when one is actually written down. */
function pickCommand(raw: string, approval: Approval): string | null {
  const fromArgs = /\bcommand\b\s*:\s*(['"])([\s\S]*?)\1/.exec(raw);
  if (fromArgs && fromArgs[2].trim()) return fromArgs[2].trim();
  const detail = String(approval?.detail || "");
  const line = detail.split(/\r?\n/).find((l) => COMMAND_HINT.test(l) && l.trim().length > 3);
  if (line) return line.trim();
  const action = String(approval?.action || "").trim();
  return approvalShape(approval) === "command" && action ? action : null;
}

export function approvalFlap(status?: string): { value: string; tone: "go" | "hold" | "amber" | "unlit" } {
  if (status === "pending") return { value: "Hold", tone: "hold" };
  if (status === "approved") return { value: "Ok", tone: "go" };
  if (status === "denied") return { value: "No", tone: "hold" };
  return { value: status || "Idle", tone: "unlit" };
}

/** How long a decision took to arrive, once it has. */
export function decisionLatency(approval: Approval): number | null {
  const asked = ms(approval?.created_at);
  const done = ms(approval?.resolved_at);
  if (!asked || !done || done < asked) return null;
  return done - asked;
}

/** Newest first; pending above resolved regardless of time. */
export function sortApprovals(rows: Approval[] | null | undefined): Approval[] {
  return [...(rows || [])].sort((a, b) => {
    const pa = a.status === "pending" ? 0 : 1;
    const pb = b.status === "pending" ? 0 : 1;
    if (pa !== pb) return pa - pb;
    return ms(b.created_at || 0) - ms(a.created_at || 0);
  });
}