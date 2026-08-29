import { useState, useRef, useEffect, useCallback } from "react";
import { Button, Tooltip, Avatar } from "../ui.jsx";

export function Composer({ onSend, placeholder, channelName, agents, onMention, compact, threadParent }) {
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

  function send() {
    const text = draft.trim();
    if (!text) return;
    onSend(text);
    setDraft("");
    setMention({ open: false, index: 0, items: [], query: "" });
  }

  useEffect(() => {
    if (mention.open && mentionRef.current) {
      const el = mentionRef.current.children[mention.index];
      el?.scrollIntoView({ block: "nearest" });
    }
  }, [mention.index, mention.open]);

  return (
    <div id="composer">
      <div className="composer-wrap">
        <div className="composer-toolbar">
          <Tooltip content="Attach file"><button className="composer-tool" aria-label="Attach">📎</button></Tooltip>
          <Tooltip content="Code block"><button className="composer-tool" aria-label="Code">{"</>"}</button></Tooltip>
          <Tooltip content="Mention agent"><button className="composer-tool" aria-label="Mention" onClick={() => { setDraft(draft + "@"); inputRef.current?.focus(); }}>@</button></Tooltip>
          {threadParent && <span className="composer-thread-hint">Replying in thread</span>}
        </div>

        <div className="composer-input-wrap">
          <textarea
            ref={inputRef}
            id={compact ? "thread-input" : "msg-input"}
            className="composer-input"
            value={draft}
            onChange={handleChange}
            onKeyDown={handleKeyDown}
            placeholder={placeholder || `Message ${channelName || "#channel"}…`}
            rows={1}
            aria-label="Message input"
          />

          <div className="composer-actions">
            <Tooltip content="Send (Enter)">
              <Button variant="primary" size="sm" onClick={send} disabled={!draft.trim()} className="composer-send">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M22 2L11 13M22 2L15 22l-4-9-9-4 20-7z"/></svg>
              </Button>
            </Tooltip>
          </div>

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
                    {item.kind === "agent" ? <Avatar name={item.label} kind="agent" size="sm" /> : <span className="mention-icon">#</span>}
                    <span className="mention-label">{item.label}</span>
                    <span className="mention-handle text-mono-xs text-subtle">@{item.name}</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>

        <div className="composer-hint">
          <span><kbd>Enter</kbd> to send · <kbd>Shift</kbd>+<kbd>Enter</kbd> for newline · <kbd>@</kbd> to mention</span>
          {agents?.length > 0 && <span className="composer-agents">{agents.length} agent{agents.length !== 1 ? "s" : ""} in this space</span>}
        </div>
      </div>
    </div>
  );
}
