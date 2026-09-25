import { useState } from "react";
import { ContextChips } from "./ContextDrawer.jsx";

const MODES = [
  { id: "ask", label: "Ask", hint: "Ask your team anything…" },
  { id: "work", label: "Work", hint: "Describe the outcome — agents plan and execute…" },
  { id: "research", label: "Research", hint: "Research with sources and evidence…" },
  { id: "create", label: "Create", hint: "Create a report, doc, or artifact…" },
];

/**
 * Composer redesign (plan §5): one input, intent modes, smart context bar.
 * Wraps any onSend(text, {mode, context}) handler.
 */
export function SmartComposer({ onSend, contextItems = [], onRemoveContext, disabled }) {
  const [mode, setMode] = useState("ask");
  const [text, setText] = useState("");
  const active = MODES.find((m) => m.id === mode);

  function submit(e) {
    e?.preventDefault();
    const value = text.trim();
    if (!value || disabled) return;
    onSend?.(value, { mode, context: contextItems });
    setText("");
  }

  return (
    <form className="smart-composer" onSubmit={submit} aria-label="Message composer">
      <ContextChips items={contextItems} onRemove={onRemoveContext} />
      <div className="composer-modes" role="tablist" aria-label="Composer mode">
        {MODES.map((m) => (
          <button
            key={m.id}
            type="button"
            role="tab"
            aria-selected={mode === m.id}
            className={`composer-mode ${mode === m.id ? "active" : ""}`}
            onClick={() => setMode(m.id)}
          >
            {m.label}
          </button>
        ))}
      </div>
      <div className="composer-row">
        <input
          className="input composer-input"
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder={active.hint}
          aria-label={active.hint}
          disabled={disabled}
          onKeyDown={(e) => {
            if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) submit();
          }}
        />
        <button type="submit" className="btn btn-primary" disabled={!text.trim() || disabled} aria-label="Send">
          ↵ Send
        </button>
      </div>
      <div className="composer-hints muted small">
        <span><kbd>Enter</kbd> to send</span><span><kbd>⌘K</kbd> commands</span>
      </div>
    </form>
  );
}
