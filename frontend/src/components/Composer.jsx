import { useState, useRef, useEffect, useCallback } from "react";
import { Avatar } from "../ui.jsx";

export function Composer({ onSend, placeholder, channelName, agents, compact, threadParent }) {
  const [draft, setDraft] = useState("");
  const [mention, setMention] = useState({ open: false, index: 0, items: [], query: "" });
  const inputRef = useRef(null);
  const mentionRef = useRef(null);

  const allMentions = useCallback(() => {
    const agentNames = (agents || []).map(a => ({ name: a.name, label: a.display_name || a.name, kind: "agent" }));
    return [{ name: "channel", label: "Everyone in channel", kind: "channel" }, ...agentNames];
  }, [agents]);

  function handleChange(e) {
    const value = e.target.value;
    setDraft(value);

    const match = value.match(/(?:^|\s)@(\w*)$/);
    if (match) {
      const query = match[1].toLowerCase();
      const items = allMentions().filter(m => m.name.toLowerCase().startsWith(query));
      setMention({ open: items.length > 0, index: 0, items, query: match[1] });
    } else {
      setMention({ open: false, index: 0, items: [], query: "" });
    }
  }

  function insertMention(item) {
    const before = draft.slice(0, draft.lastIndexOf("@"));
    const after = draft.slice(draft.lastIndexOf("@") + mention.query.length + 1);
    setDraft(`${before}@${item.name} ${after}`);
    setMention({ open: false, index: 0, items: [], query: "" });
    inputRef.current?.focus();
  }

  function handleKeyDown(e) {
    if (mention.open) {
      if (e.key === "ArrowDown") { e.preventDefault(); setMention(m => ({ ...m, index: Math.min(m.index + 1, m.items.length - 1) })); return; }
      if (e.key === "ArrowUp") { e.preventDefault(); setMention(m => ({ ...m, index: Math.max(m.index - 1, 0) })); return; }
      if (e.key === "Enter" || e.key === "Tab") { e.preventDefault(); insertMention(mention.items[mention.index]); return; }
      if (e.key === "Escape") { setMention({ open: false, index: 0, items: [], query: "" }); return; }
    }

    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  }

  async function send() {
    const text = draft.trim();
    if (!text) return;
    const ok = await onSend(text);
    if (ok === false) return;
    setDraft("");
    setMention({ open: false, index: 0, items: [], query: "" });
  }

  useEffect(() => {
    if (mention.open && mentionRef.current) {
      const el = mentionRef.current.children[mention.index];
      el?.scrollIntoView({ block: "nearest" });
    }
  }, [mention.index, mention.open]);

  const canSend = draft.trim().length > 0;

  return (
    <div id="composer">
      {threadParent && <div className="composer-thread-hint">Replying in thread</div>}
      <div className="composer-pill">
        <button
          type="button"
          className="composer-add"
          aria-label="Add attachment"
          onClick={() => { setDraft(draft + "@"); inputRef.current?.focus(); }}
        >
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
            <path d="M12 5v14M5 12h14" />
          </svg>
        </button>

        <textarea
          ref={inputRef}
          id={compact ? "thread-input" : "msg-input"}
          className="composer-input"
          value={draft}
          onChange={handleChange}
          onKeyDown={handleKeyDown}
          placeholder={placeholder || `Message ${channelName || "channel"}…`}
          rows={1}
          aria-label="Message input"
        />

        <button
          type="button"
          className={`composer-send-btn ${canSend ? "ready" : ""}`}
          onClick={send}
          disabled={!canSend}
          aria-label={canSend ? "Send message" : "Voice input"}
        >
          {canSend ? (
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M22 2L11 13M22 2L15 22l-4-9-9-4 20-7z" />
            </svg>
          ) : (
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z" />
              <path d="M19 10v2a7 7 0 0 1-14 0v-2" />
              <line x1="12" y1="19" x2="12" y2="23" />
              <line x1="8" y1="23" x2="16" y2="23" />
            </svg>
          )}
        </button>

        {mention.open && (
          <ul className="mention-menu" ref={mentionRef} role="listbox">
            {mention.items.map((item, i) => (
              <li key={item.name}>
                <button
                  className={`mention-item ${i === mention.index ? "active" : ""}`}
                  onMouseDown={(e) => { e.preventDefault(); insertMention(item); }}
                  role="option"
                  aria-selected={i === mention.index}
                >
                  {item.kind === "agent" ? <Avatar name={item.label} kind="agent" size="sm" /> : <span className="mention-icon">@</span>}
                  <span className="mention-label">{item.label}</span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
