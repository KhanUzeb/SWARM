/** Visible, controllable context (plan §29). */
export function ContextDrawer({ stats, included = [], onManage, onClose }) {
  const rows = [
    ["Conversation", stats?.conversation],
    ["Knowledge", stats?.knowledge],
    ["Files", stats?.files],
    ["Memory", stats?.memory],
  ];
  return (
    <aside className="context-drawer" aria-label="Context">
      <header className="drawer-head">
        <h3>Context</h3>
        <button className="btn btn-ghost btn-sm" onClick={onClose} aria-label="Close context">×</button>
      </header>
      <dl className="context-rows">
        {rows.map(([label, value]) => (
          <div key={label} className="context-row">
            <dt>{label}</dt>
            <dd>{value != null ? `${(value / 1000).toFixed(1)}k tokens` : "—"}</dd>
          </div>
        ))}
        <div className="context-row total">
          <dt>Total</dt>
          <dd>{stats?.total != null ? `${(stats.total / 1000).toFixed(1)}k / ${((stats?.budget || 32000) / 1000).toFixed(0)}k` : "—"}</dd>
        </div>
      </dl>
      {included.length > 0 && (
        <>
          <h4 className="drawer-sub">Included</h4>
          <ul className="context-included">
            {included.map((item) => <li key={item}>✓ {item}</li>)}
          </ul>
        </>
      )}
      {onManage && <button className="btn btn-secondary btn-sm" onClick={onManage}>Manage context</button>}
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
