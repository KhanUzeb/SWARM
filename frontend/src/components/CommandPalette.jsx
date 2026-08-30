import { useState, useRef, useEffect, useMemo } from "react";
import { Input } from "../ui.jsx";

export function CommandPalette({ open, onClose, commands, onCommand }) {
  const [query, setQuery] = useState("");
  const [index, setIndex] = useState(0);
  const inputRef = useRef(null);
  const listRef = useRef(null);

  const filtered = useMemo(() => {
    if (!query.trim()) return commands;
    const q = query.toLowerCase();
    return commands.filter(c =>
      c.label.toLowerCase().includes(q) ||
      c.keywords?.some(k => k.toLowerCase().includes(q)) ||
      c.group?.toLowerCase().includes(q)
    );
  }, [query, commands]);

  const groups = useMemo(() => {
    const map = {};
    for (const c of filtered) {
      const g = c.group || "Actions";
      (map[g] ||= []).push(c);
    }
    return map;
  }, [filtered]);

  useEffect(() => {
    if (open) { setQuery(""); setIndex(0); setTimeout(() => inputRef.current?.focus(), 10); }
  }, [open]);

  useEffect(() => { setIndex(0); }, [query]);

  useEffect(() => {
    if (listRef.current && filtered[index]) {
      const el = listRef.current.querySelector(`[data-index="${index}"]`);
      el?.scrollIntoView({ block: "nearest" });
    }
  }, [index, filtered]);

  if (!open) return null;

  let flatIndex = -1;

  function handleKey(e) {
    if (e.key === "Escape") { onClose(); return; }
    if (e.key === "ArrowDown") { e.preventDefault(); setIndex(i => Math.min(i + 1, filtered.length - 1)); }
    if (e.key === "ArrowUp") { e.preventDefault(); setIndex(i => Math.max(i - 1, 0)); }
    if (e.key === "Enter") { e.preventDefault(); const cmd = filtered[index]; if (cmd) { onCommand(cmd); onClose(); } }
  }

  return (
    <div className="modal-overlay cmd-overlay" onClick={onClose} role="dialog" aria-modal="true" aria-label="Command palette">
      <div className="cmd-palette" onClick={e => e.stopPropagation()}>
        <div className="cmd-input-row">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden><circle cx="11" cy="11" r="8"/><path d="M21 21l-4.3-4.3"/></svg>
          <Input
            ref={inputRef}
            className="cmd-input"
            placeholder="Search commands…"
            value={query}
            onChange={e => setQuery(e.target.value)}
            onKeyDown={handleKey}
            aria-label="Command input"
          />
          <span className="cmd-esc">esc</span>
        </div>

        <div className="cmd-list scrollable" ref={listRef}>
          {filtered.length === 0 && (
            <div className="cmd-empty">
              <div className="empty-state-icon" aria-hidden>
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5"><circle cx="11" cy="11" r="8"/><path d="M21 21l-4.3-4.3"/></svg>
              </div>
              <span className="cmd-empty-text">No commands found</span>
            </div>
          )}
          {Object.entries(groups).map(([group, items]) => (
            <div key={group} className="cmd-group">
              <div className="cmd-group-label">{group}</div>
              {items.map(item => {
                flatIndex++;
                const fi = flatIndex;
                return (
                  <button
                    key={item.id}
                    data-index={fi}
                    className={`cmd-item ${fi === index ? "active" : ""}`}
                    onMouseEnter={() => setIndex(fi)}
                    onClick={() => { onCommand(item); onClose(); }}
                  >
                    <span className="cmd-item-label">{item.label}</span>
                    {item.hint && <span className="cmd-item-hint">{item.hint}</span>}
                    {item.shortcut && <span className="cmd-item-kbd">{item.shortcut}</span>}
                  </button>
                );
              })}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
