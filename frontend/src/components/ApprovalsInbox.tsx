import * as React from "react";
import { ChevronDown, FileCode, ShieldAlert, ShieldCheck, ShieldX, Terminal } from "lucide-react";
import { Flap, Lamp } from "./Flap";
import { fmtTime } from "../lib";
import {
  approvalDetail,
  approvalFlap,
  approvalShape,
  decisionLatency,
  runForApproval,
  shapeLabel,
  sortApprovals,
  type Approval,
  type ApprovalRun,
} from "../work/approvalsModel";
import { fmtDuration, type AuditLine, type WorkEvent } from "../work/inspectorModel";

type ApprovalsInboxProps = {
  approvals?: Approval[] | null;
  eventsByWork?: Record<string, WorkEvent[]> | null;
  auditByWork?: Record<string, AuditLine[]> | null;
  runs?: ApprovalRun[] | null;
  agents?: Array<{ name: string; display_name?: string; avatar?: string; job?: string }>;
  onResolve?: (id: number | string, decision: "approved" | "denied") => void;
  busyId?: number | string | null;
  error?: string | null;
  onRetry?: () => void;
};

/**
 * The approvals inbox: what needs a human now, and what was already decided.
 *
 * Everything shown is read off an approvals row or the work event log. The
 * resolve endpoint takes only `approved` | `denied`, so that is all the
 * panel offers — there is no run-scoped grant to press, and pretending
 * otherwise would lie about what the backend can honor.
 */
export function ApprovalsInbox({
  approvals,
  eventsByWork,
  auditByWork,
  runs,
  agents,
  onResolve,
  busyId,
  error,
  onRetry,
}: ApprovalsInboxProps) {
  const rows = sortApprovals(approvals);
  const pending = rows.filter((a) => a.status === "pending");
  const decided = rows.filter((a) => a.status !== "pending");

  return (
    <section className="approvals-inbox" aria-label="Approvals">
      {/* The page header already says Approvals. This row is the count, so
          the lamp is labelled rather than floating as an orphan square. */}
      <header className="approvals-inbox-head">
        <Lamp tone={pending.length ? "hold" : "go"} title={pending.length ? "Approvals pending" : "No approvals pending"} />
        <span className="approvals-inbox-count">
          {pending.length === 0
            ? "Nothing waiting on you"
            : `${pending.length} waiting on you`}
        </span>
        {decided.length > 0 && (
          <span className="approvals-inbox-settled">
            {decided.length} already decided
          </span>
        )}
      </header>

      {error && (
        <p className="approvals-error" role="alert">
          {error}{" "}
          {onRetry && (
            <button type="button" className="btn btn-ghost btn-sm" onClick={onRetry}>
              Retry
            </button>
          )}
        </p>
      )}

      <div className="approvals-list">
        {rows.length === 0 && (
          <p className="approvals-empty">
            No approvals recorded. A bot raises one before any outward-facing or destructive action.
          </p>
        )}

        {pending.map((a) => (
          <ApprovalPrompt
            key={String(a.id)}
            approval={a}
            events={runEventsFor(a, eventsByWork, runs)}
            audit={auditFor(a, eventsByWork, auditByWork, runs)}
            run={runForApproval(a, runs)}
            agents={agents}
            onResolve={onResolve}
            busy={busyId === a.id}
          />
        ))}

        {decided.length > 0 && (
          <details className="approvals-decided">
            <summary>
              {decided.length} past decision{decided.length === 1 ? "" : "s"}
              <ChevronDown size={12} aria-hidden="true" />
            </summary>
            <div className="approvals-list">
              {decided.map((a) => (
                <ApprovalPrompt
                  key={String(a.id)}
                  approval={a}
                  events={runEventsFor(a, eventsByWork, runs)}
                  audit={auditFor(a, eventsByWork, auditByWork, runs)}
                  run={runForApproval(a, runs)}
                  agents={agents}
                />
              ))}
            </div>
          </details>
        )}
      </div>
    </section>
  );
}

function runEventsFor(
  a: Approval,
  eventsByWork: Record<string, WorkEvent[]> | null | undefined,
  runs: ApprovalRun[] | null | undefined,
): WorkEvent[] {
  const run = runForApproval(a, runs);
  return (run && eventsByWork?.[run.workId]) || [];
}

function auditFor(
  a: Approval,
  eventsByWork: Record<string, WorkEvent[]> | null | undefined,
  auditByWork: Record<string, AuditLine[]> | null | undefined,
  runs: ApprovalRun[] | null | undefined,
): AuditLine[] {
  const run = runForApproval(a, runs);
  return (run && auditByWork?.[run.workId]) || [];
}

/**
 * One approval, shown as what will actually run: the action as recorded, the
 * request_approval arguments when the run logged them, a diff only when the
 * detail carries diff lines, and the run and bot it belongs to.
 */
function ApprovalPrompt({
  approval,
  events,
  audit,
  run,
  agents,
  onResolve,
  busy,
}: {
  approval: Approval;
  events: WorkEvent[];
  audit: AuditLine[];
  run: ApprovalRun | null;
  agents?: Array<{ name: string; display_name?: string; avatar?: string; job?: string }>;
  onResolve?: (id: number | string, decision: "approved" | "denied") => void;
  busy?: boolean;
}) {
  const shape = approvalShape(approval);
  const flap = approvalFlap(approval.status);
  const detail = React.useMemo(
    () => approvalDetail(approval, events, audit),
    [approval, events, audit],
  );
  const agent = (agents || []).find((x) => x.name === approval.agent_name);
  const pending = approval.status === "pending";
  const latency = decisionLatency(approval);
  const Mark =
    shape === "command" ? Terminal : shape === "write" ? FileCode : pending ? ShieldAlert : ShieldCheck;

  return (
    <article className={`approval-card${pending ? " is-pending" : ""}${approval.status === "denied" ? " is-denied" : ""}`}>
      <div className="approval-head">
        <span className="approval-shape">
          <Mark size={13} aria-hidden="true" />
          {shapeLabel(shape)}
        </span>
        <span className="approval-flap">
          <Flap value={flap.value} tone={flap.tone} />
        </span>
      </div>

      <p className="approval-action">{approval.action || "Unnamed action"}</p>

      {approval.detail ? (
        <p className="approval-detail">{approval.detail}</p>
      ) : (
        <p className="approval-detail approval-detail-none">
          The request recorded no detail, so nothing is known about what it will do.
        </p>
      )}

      {detail.command && (
        <Register label="will run" value={detail.command} tone="hold" />
      )}

      {detail.diff.length > 0 && (
        <details className="approval-diff" open>
          <summary>
            File change ({detail.diff.length} lines)
            <ChevronDown size={11} aria-hidden="true" />
          </summary>
          <pre className="approval-diff-body">
            {detail.diff.map((line, i) => (
              <span
                key={i}
                className={
                  line.startsWith("+") && !line.startsWith("+++")
                    ? "is-add"
                    : line.startsWith("-") && !line.startsWith("---")
                      ? "is-del"
                      : "is-ctx"
                }
              >
                {line}
                {"\n"}
              </span>
            ))}
          </pre>
        </details>
      )}

      {detail.args.length > 0 && (
        <details className="approval-args">
          <summary>
            Recorded call ({detail.args.length} argument{detail.args.length === 1 ? "" : "s"})
            <ChevronDown size={11} aria-hidden="true" />
          </summary>
          <dl className="approval-args-list">
            {detail.args.map((arg) => (
              <div key={arg.key} className="approval-arg">
                <dt>{arg.key}</dt>
                <dd>{arg.value}</dd>
              </div>
            ))}
          </dl>
        </details>
      )}

      <div className="approval-origin">
        <span className="approval-origin-item">
          <span className="reg">bot</span>
          <span className="approval-agent">{agent?.display_name || approval.agent_name || "unknown"}</span>
        </span>
        <span className="approval-origin-item">
          <span className="reg">run</span>
          {run ? (
            <span className="approval-run truncate" title={run.objective || run.workId}>
              {run.objective || run.workId}
            </span>
          ) : (
            <span className="approval-run approval-run-none">no run recorded</span>
          )}
        </span>
        <span className="approval-origin-item">
          <span className="reg">raised</span>
          <span className="approval-raised">{approval.created_at ? fmtTime(approval.created_at) : "—"}</span>
        </span>
        {latency != null && (
          <span className="approval-origin-item">
            <span className="reg">held for</span>
            <span className="approval-raised">{fmtDuration(latency)}</span>
          </span>
        )}
      </div>

      {pending && onResolve && (
        <div className="approval-actions">
          <button
            type="button"
            className="btn btn-primary btn-sm"
            onClick={() => onResolve(approval.id, "approved")}
            disabled={busy}
          >
            <ShieldCheck size={12} aria-hidden="true" />
            {busy ? "Working" : "Allow"}
          </button>
          <button
            type="button"
            className="btn btn-ghost btn-sm"
            onClick={() => onResolve(approval.id, "denied")}
            disabled={busy}
          >
            <ShieldX size={12} aria-hidden="true" />
            {busy ? "Working" : "Deny"}
          </button>
        </div>
      )}
    </article>
  );
}

function Register({ label, value, tone }: { label: string; value: string; tone: "hold" | "default" }) {
  const text = String(value || "").trim();
  if (!text) return null;
  return (
    <span className={`inspector-register${tone === "hold" ? " is-hold" : ""}`}>
      <span className="inspector-register-label">{label}</span>
      <span className="inspector-register-value">{text}</span>
    </span>
  );
}