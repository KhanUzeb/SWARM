/** Unified status language (plan §2.1 rule 5 + §10). One dot/badge system. */
export const STATUS_META = {
  queued: { label: "Queued", tone: "idle" },
  planning: { label: "Planning", tone: "running" },
  executing: { label: "Running", tone: "running" },
  running: { label: "Running", tone: "running" },
  tool_call: { label: "Tool call", tone: "running" },
  handoff: { label: "Handoff", tone: "running" },
  retrying: { label: "Retrying", tone: "warning" },
  waiting_for_approval: { label: "Needs you", tone: "warning" },
  waiting_for_input: { label: "Waiting", tone: "warning" },
  verifying: { label: "Verifying", tone: "info" },
  completed: { label: "Done", tone: "success" },
  failed: { label: "Failed", tone: "danger" },
  cancelled: { label: "Cancelled", tone: "idle" },
  idle: { label: "Idle", tone: "idle" },
  working: { label: "Working", tone: "running" },
  needs_approval: { label: "Needs you", tone: "warning" },
};

export function StatusDot({ status, size = 8 }) {
  const tone = STATUS_META[status]?.tone || "idle";
  return (
    <span
      className={`status-dot status-${tone}`}
      style={{ width: size, height: size }}
      role="img"
      aria-label={STATUS_META[status]?.label || status}
    />
  );
}

export function WorkStatusBadge({ status }) {
  const meta = STATUS_META[status] || STATUS_META.idle;
  return (
    <span className={`status-chip status-${meta.tone}`} role="status">
      <StatusDot status={status} />
      {meta.label}
    </span>
  );
}

export function ProgressBar({ done, total }) {
  const pct = total > 0 ? Math.round((done / total) * 100) : 0;
  return (
    <div className="progress-track" role="progressbar" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100} aria-label="Work progress">
      <div className="progress-fill" style={{ width: `${pct}%` }} />
    </div>
  );
}

export function FailureState({ title, detail, done = [], missing = [], actions = [] }) {
  return (
    <div className="failure-card" role="alert">
      <h4>{title}</h4>
      {detail && <p className="muted">{detail}</p>}
      {done.length > 0 && (
        <div className="failure-section">
          <span className="failure-heading">Completed</span>
          <ul>{done.map((d, i) => <li key={i}>✓ {d}</li>)}</ul>
        </div>
      )}
      {missing.length > 0 && (
        <div className="failure-section">
          <span className="failure-heading">Not completed</span>
          <ul>{missing.map((d, i) => <li key={i}>× {d}</li>)}</ul>
        </div>
      )}
      {actions.length > 0 && (
        <div className="failure-actions">
          {actions.map((a) => (
            <button key={a.label} className={`btn btn-${a.variant || "secondary"} btn-sm`} onClick={a.onClick}>
              {a.label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
