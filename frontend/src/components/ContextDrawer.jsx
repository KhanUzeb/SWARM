/** Visible, controllable context (plan §29). Stats shape matches
 *  GET /api/channels/{id}/context: chars + counts, not tokens. */
function fmtChars(n) {
  if (n == null) return "—";
  return n >= 1000 ? `${(n / 1000).toFixed(1)}k chars` : `${n} chars`;
}

export function ContextDrawer({ stats, included = [], onRefresh, onClose }) {
  const pct = stats?.budget_chars
    ? Math.min(100, Math.round(((stats.total_chars || 0) / stats.budget_chars) * 100))
    : null;
  return (
    <aside className="context-drawer" aria-label="Context">
      <header className="drawer-head">
        <h3>Context</h3>
        <div className="row">
          {onRefresh && <button className="btn btn-ghost btn-sm" onClick={onRefresh}>Refresh</button>}
          <button className="btn btn-ghost btn-sm" onClick={onClose} aria-label="Close context">×</button>
        </div>
      </header>
      {!stats && <p className="muted small">No context data yet.</p>}
      {stats && (
        <>
          <div className="progress-track" role="progressbar" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100} aria-label="Context usage">
            <div className="progress-fill" style={{ width: `${pct}%` }} />
          </div>
          <dl className="context-rows">
            <div className="context-row"><dt>History</dt><dd>{fmtChars(stats.history_chars)} · {stats.messages ?? "—"} msgs</dd></div>
            <div className="context-row"><dt>Memory</dt><dd>{stats.memory_notes ?? "—"} notes</dd></div>
            <div className="context-row"><dt>Knowledge</dt><dd>{stats.kb_docs ?? "—"} docs</dd></div>
            {stats.dropped_messages ? <div className="context-row"><dt>Trimmed</dt><dd>{stats.dropped_messages} msgs</dd></div> : null}
            {stats.has_summary ? <div className="context-row"><dt>Summary</dt><dd>kept</dd></div> : null}
            <div className="context-row total">
              <dt>Total</dt>
              <dd>{fmtChars(stats.total_chars)} / {fmtChars(stats.budget_chars)}</dd>
            </div>
          </dl>
        </>
      )}
      {included.length > 0 && (
        <>
          <h4 className="drawer-sub">Included</h4>
          <ul className="context-included">
            {included.map((item) => <li key={item}>✓ {item}</li>)}
          </ul>
        </>
      )}
    </aside>
  );
}

export function ContextChips({ items = [], onRemove }) {
  if (!items.length) return null;
  return (
    <div className="context-chips" aria-label="Selected context">
      <span className="muted small">Context</span>
      {items.map((item) => (
        <button key={item} className="context-chip" onClick={() => onRemove?.(item)} title={`Remove ${item}`}>
          × {item}
        </button>
      ))}
    </div>
  );
}
