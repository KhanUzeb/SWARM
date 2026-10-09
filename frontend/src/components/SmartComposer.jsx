import { useState } from "react";
import { ContextChips } from "./ContextDrawer.jsx";
import { canSubmitDraft } from "../lib.js";
import { CornerDownLeft } from "lucide-react";

const MODES = [
  { id: "ask", label: "Ask", hint: "Ask your team anything" },
  { id: "work", label: "Work", hint: "Describe the outcome — the team plans and executes" },
  { id: "research", label: "Research", hint: "Research with sources and evidence" },
  { id: "create", label: "Create", hint: "Create a report, doc, or artifact" },
];

/**
 * One input, four intents. The intent keys are set into the same machine
 * register as the rest of the board, and the selected one is lit.
 */
export function SmartComposer({ onSend, contextItems = [], onRemoveContext, disabled }) {
  const [mode, setMode] = useState("ask");
  const [text, setText] = useState("");
  const [sending, setSending] = useState(false);
  const active = MODES.find((m) => m.id === mode);
  const submittable = canSubmitDraft({ text, busy: sending, disabled });

  async function submit(e) {
    e?.preventDefault();
    if (!submittable) return;
    // One brief, one run: without the guard a double Enter posts the same
    // brief twice and queues two runs (POST /api/v2/runs has no idempotency).
    setSending(true);
    try {
      const result = await onSend?.(text.trim(), { mode, context: contextItems });
      if (result !== false) setText("");
    } finally {
      setSending(false);
    }
  }

  return (
    <form className="smart-composer" onSubmit={submit} aria-label="Brief the team">
      <ContextChips items={contextItems} onRemove={onRemoveContext} />

      <div className="view-tabs" role="tablist" aria-label="Intent" style={{ alignSelf: "flex-start" }}>
        {MODES.map((m) => (
          <button
            key={m.id}
            type="button"
            role="tab"
            aria-selected={mode === m.id}
            className={`view-tab ${mode === m.id ? "active" : ""}`}
            onClick={() => setMode(m.id)}
          >
            {m.label}
          </button>
        ))}
      </div>

      <div className="composer-row">
        <input
          className="composer-input"
          style={{ background: "transparent", border: 0, boxShadow: "none" }}
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder={active.hint}
          aria-label={active.hint}
          disabled={disabled}
        />
        <button
          type="submit"
          className="btn btn-primary"
          disabled={!submittable}
          aria-label="Send the brief"
        >
          <CornerDownLeft size={13} />
          {sending ? "Sending…" : "Send"}
        </button>
      </div>

      <div className="composer-hints">
        <span>Enter sends</span>
        <span style={{ marginLeft: "auto" }}>{active.label} mode</span>
      </div>
    </form>
  );
}
