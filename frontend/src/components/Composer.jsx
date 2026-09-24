import { useState, useRef, useEffect, useCallback } from "react";
import { Avatar } from "../ui.jsx";
import { shortModel } from "../lib.js";
import ModelPicker from "../ai-support/ModelPicker.jsx";

export function Composer({ onSend, placeholder, channelName, agents, compact, threadParent, workingWith, offline, sendFailed, contextStats, contextError, onRefreshContext, onToggleContextDetails, contextDetailsOpen, token, model, onModelChange, working, onStop, prefill, onPrefillConsumed }) {
  const [draft, setDraft] = useState("");
  const [mention, setMention] = useState({ open: false, index: 0, items: [], query: "" });
  const [slash, setSlash] = useState({ open: false, index: 0 });
  const [sending, setSending] = useState(false);
  const [modelOpen, setModelOpen] = useState(false);
  const [modelCatalog, setModelCatalog] = useState([]);
  const [listening, setListening] = useState(false);
  const inputRef = useRef(null);
  const mentionRef = useRef(null);
  const modelPickerRef = useRef(null);
  const recogRef = useRef(null);

  // Web Speech dictation (beautifului Prompt-Bar pattern). Rendered only
  // where the browser supports it — no dead chrome elsewhere.
  const canDictate = typeof window !== "undefined" &&
    (window.SpeechRecognition || window.webkitSpeechRecognition);

  function toggleDictation() {
    if (listening) { try { recogRef.current?.stop(); } catch { /* already stopped */ } return; }
    const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SR) return;
    const rec = new SR();
    rec.lang = (typeof navigator !== "undefined" && navigator.language) || "en-US";
    rec.interimResults = false;
    rec.onresult = (e) => {
      const text = Array.from(e.results).map(r => r[0]?.transcript || "").join(" ").trim();
      if (!text) return;
      setDraft(d => (d && !/\s$/.test(d) ? `${d} ` : d) + text);
      inputRef.current?.focus();
    };
    rec.onend = () => setListening(false);
    rec.onerror = () => setListening(false);
    recogRef.current = rec;
    setListening(true);
    try { rec.start(); } catch { setListening(false); }
  }

  useEffect(() => () => { try { recogRef.current?.abort?.(); } catch { /* unmount */ } }, []);

  // Never lose a draft: persist per channel (+thread) and restore on return.
  const draftKey = `swarm.draft.${channelName || "channel"}${compact ? ".thread" : ""}`;
  useEffect(() => {
    try { setDraft(localStorage.getItem(draftKey) || ""); }
    catch { setDraft(""); }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [draftKey]);
  useEffect(() => {
    try {
      if (draft) localStorage.setItem(draftKey, draft);
      else localStorage.removeItem(draftKey);
    } catch { /* private mode */ }
  }, [draft, draftKey]);

  // Close the model popover on outside click / Escape.
  useEffect(() => {
    if (!modelOpen) return;
    function onDown(e) { if (modelPickerRef.current && !modelPickerRef.current.contains(e.target)) setModelOpen(false); }
    function onKey(e) { if (e.key === "Escape") setModelOpen(false); }
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [modelOpen]);

  const providerOf = useCallback((modelId) => {
    const hit = (modelCatalog || []).find(m => m.id === modelId);
    return hit?.provider_name || hit?.provider_id || "";
  }, [modelCatalog]);

  const COMMANDS = [
    { name: "retry", hint: "Retry the last agent reply", insert: "Please retry your last reply. " },
    { name: "approve", hint: "Approve the pending request", insert: "Approved — please continue. " },
    { name: "deny", hint: "Deny the pending request", insert: "Denied — do not proceed with that action. " },
    { name: "summarize", hint: "Ask for a summary", insert: "Please summarize the discussion so far. " },
  ];

  const allMentions = useCallback(() => {
    const agentNames = (agents || []).map(a => ({ name: a.name, label: a.display_name || a.name, kind: "agent", avatar: a.avatar }));
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
    const slashMatch = value.match(/(?:^|\s)\/(\w*)$/);
    setSlash(slashMatch ? { open: true, index: 0, query: slashMatch[1] } : { open: false, index: 0 });
  }

  function insertMention(item) {
    const before = draft.slice(0, draft.lastIndexOf("@"));
    const after = draft.slice(draft.lastIndexOf("@") + mention.query.length + 1);
    setDraft(`${before}@${item.name} ${after}`);
    setMention({ open: false, index: 0, items: [], query: "" });
    inputRef.current?.focus();
  }

  function slashItems() {
    const q = (slash.query || "").toLowerCase();
    return COMMANDS.filter(c => c.name.startsWith(q));
  }

  function insertCommand(item) {
    const idx = draft.lastIndexOf("/");
    const before = idx >= 0 ? draft.slice(0, idx) : draft;
    setDraft(`${before}${item.insert}`);
    setSlash({ open: false, index: 0 });
    inputRef.current?.focus();
  }

  function handleKeyDown(e) {
    if (slash.open) {
      const items = slashItems();
      if (e.key === "ArrowDown") { e.preventDefault(); setSlash(s => ({ ...s, index: Math.min(s.index + 1, items.length - 1) })); return; }
      if (e.key === "ArrowUp") { e.preventDefault(); setSlash(s => ({ ...s, index: Math.max(s.index - 1, 0) })); return; }
      if ((e.key === "Enter" || e.key === "Tab") && items.length) { e.preventDefault(); insertCommand(items[slash.index] || items[0]); return; }
      if (e.key === "Escape") { setSlash({ open: false, index: 0 }); return; }
    }
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
    if (!text || sending) return; // lock while the send is in flight
    setSending(true);
    try {
      const ok = await onSend(text, { model: model || null });
      if (ok === false) return;
      setDraft("");
      setMention({ open: false, index: 0, items: [], query: "" });
      setSlash({ open: false, index: 0 });
    } finally {
      setSending(false);
    }
  }

  // Auto-grow the input up to a cap instead of a fixed single row.
  useEffect(() => {
    const el = inputRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 160)}px`;
  }, [draft]);

  useEffect(() => {
    if (prefill == null || prefill === "") return;
    setDraft(prefill);
    onPrefillConsumed?.();
    requestAnimationFrame(() => {
      inputRef.current?.focus();
      const len = prefill.length;
      inputRef.current?.setSelectionRange(len, len);
    });
  }, [prefill, onPrefillConsumed]);

  useEffect(() => {
    if (mention.open && mentionRef.current) {
      const el = mentionRef.current.children[mention.index];
      el?.scrollIntoView({ block: "nearest" });
    }
  }, [mention.index, mention.open]);

  const canSend = draft.trim().length > 0;
  const ctxPct = contextStats && contextStats.budget_chars
    ? Math.min(100, Math.round((contextStats.total_chars / contextStats.budget_chars) * 100))
    : null;
  const popupOpen = mention.open || slash.open;
  const activeId = mention.open
    ? `composer-mention-option-${mention.index}`
    : slash.open
      ? `composer-slash-option-${slash.index}`
      : undefined;

  return (
    <div id="composer" className={offline ? "is-offline" : ""}>
      {threadParent && <div className="composer-thread-hint">Replying in thread</div>}
      {(workingWith || []).length > 0 && (
        <div className="composer-context" role="status">
          <span className="pulse-dot violet" aria-hidden />
          Working with {(workingWith || []).slice(0, 3).join(", ")}
          {(workingWith || []).length > 3 && ` +${workingWith.length - 3} more`}
        </div>
      )}
      {ctxPct !== null && (
        <div className="context-meter-row">
          <button
            type="button"
            className={`context-meter${ctxPct >= 80 ? " hot" : ""}`}
            onClick={onRefreshContext}
            title={`Context ${contextStats.total_chars}/${contextStats.budget_chars} chars · ${contextStats.messages} messages${contextStats.dropped_messages ? ` · ${contextStats.dropped_messages} trimmed` : ""}${contextStats.has_summary ? " · summary kept" : ""} — click to refresh`}
          >
            <span className="context-meter-bar" aria-hidden>
              <span className="context-meter-fill" style={{ width: `${ctxPct}%` }} />
            </span>
            <span className="context-meter-label">
              Context {ctxPct}%{contextStats.memory_notes ? ` · ${contextStats.memory_notes} notes` : ""}{contextStats.kb_docs ? ` · ${contextStats.kb_docs} kb` : ""}
            </span>
          </button>
          {onToggleContextDetails && (
            <button
              type="button"
              className="context-breakdown-btn"
              onClick={onToggleContextDetails}
              aria-expanded={contextDetailsOpen}
            >
              {contextDetailsOpen ? "Hide breakdown" : "Breakdown"}
            </button>
          )}
        </div>
      )}
      {ctxPct === null && contextError && (
        <button type="button" className="context-meter unavailable" onClick={onRefreshContext} title="Context unavailable — click to retry">
          <span className="context-meter-label">Context unavailable — retry</span>
        </button>
      )}
      {offline && (
        <div className="composer-offline" role="alert">
          You are offline — messages will be retried. Reconnecting…
        </div>
      )}
      {sendFailed && (
        <div className="composer-send-state" role="alert">
          Send failed — your draft is kept. Fix the connection and send again.
        </div>
      )}
      <div className="composer-model-row">
        <div className="composer-model-picker" ref={modelPickerRef}>
          <button
            type="button"
            className={`model-chip${model ? " set" : ""}`}
            onClick={() => setModelOpen(o => !o)}
            aria-expanded={modelOpen}
            aria-label={model ? `Model: ${model}. Change model` : "Model: Auto. Choose a model"}
            title={model ? `Answering with ${model}` : "Auto — each agent answers with its default model"}
          >
            <span className="model-chip-dot" aria-hidden />
            {model
              ? `${providerOf(model) ? `${providerOf(model)} · ` : ""}${shortModel(model)}`
              : "Auto"}
            <span aria-hidden> ▾</span>
          </button>
          {modelOpen && (
            <div className="composer-model-pop">
              <button
                type="button"
                className={`model-auto-row${!model ? " selected" : ""}`}
                onClick={() => { onModelChange?.(""); setModelOpen(false); }}
              >
                <span className="model-chip-dot" aria-hidden />
                <span><b>Auto</b> <span className="muted small">agent default</span></span>
                {!model && <span aria-hidden>✓</span>}
              </button>
              <ModelPicker
                token={token}
                value={model || ""}
                onChange={(id) => { onModelChange?.(id); setModelOpen(false); }}
                onModelsLoaded={(list) => setModelCatalog(list || [])}
                placeholder="Search models"
              />
            </div>
          )}
        </div>
      </div>
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
          aria-describedby={compact ? undefined : "composer-hint"}
          role="combobox"
          aria-expanded={popupOpen}
          aria-controls={mention.open ? "composer-mention-list" : slash.open ? "composer-slash-list" : undefined}
          aria-activedescendant={activeId}
          aria-autocomplete="list"
        />

        {canDictate && (
          <button
            type="button"
            className={`composer-mic-btn${listening ? " listening" : ""}`}
            onClick={toggleDictation}
            aria-pressed={listening}
            aria-label={listening ? "Stop dictation" : "Dictate message"}
            title={listening ? "Stop dictation" : "Dictate message (voice input)"}
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden>
              <rect x="9" y="2" width="6" height="12" rx="3" />
              <path d="M5 10a7 7 0 0 0 14 0M12 17v4" />
            </svg>
          </button>
        )}

        {working && (
          <button
            type="button"
            className="composer-stop-btn"
            onClick={onStop}
            aria-label="Stop generating"
            title="Stop the running reply (partial text is kept)"
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" aria-hidden>
              <rect x="6" y="6" width="12" height="12" rx="2" />
            </svg>
          </button>
        )}
        <button
          type="button"
          className={`composer-send-btn ${canSend ? "ready" : ""}`}
          onClick={send}
          disabled={!canSend || sending}
          aria-label={sending ? "Sending…" : "Send message"}
          title="Send (Enter)"
          aria-keyshortcuts="Enter"
        >
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M22 2L11 13M22 2L15 22l-4-9-9-4 20-7z" />
          </svg>
        </button>

        {mention.open && (
          <ul className="mention-menu" ref={mentionRef} role="listbox" id="composer-mention-list" aria-label="Mention suggestions">
            {mention.items.map((item, i) => (
              <li key={item.name} role="presentation">
                <button
                  id={`composer-mention-option-${i}`}
                  className={`mention-item ${i === mention.index ? "active" : ""}`}
                  onMouseDown={(e) => { e.preventDefault(); insertMention(item); }}
                  role="option"
                  aria-selected={i === mention.index}
                >
                  {item.kind === "agent" ? <Avatar name={item.label} kind="agent" size="sm" avatar={item.avatar} /> : <span className="mention-icon">@</span>}
                  <span className="mention-label">{item.label}</span>
                </button>
              </li>
            ))}
          </ul>
        )}

        {slash.open && slashItems().length > 0 && (
          <ul className="mention-menu slash-menu" role="listbox" id="composer-slash-list" aria-label="Command suggestions">
            {slashItems().map((item, i) => (
              <li key={item.name} role="presentation">
                <button
                  id={`composer-slash-option-${i}`}
                  className={`mention-item ${i === slash.index ? "active" : ""}`}
                  onMouseDown={(e) => { e.preventDefault(); insertCommand(item); }}
                  role="option"
                  aria-selected={i === slash.index}
                >
                  <span className="mention-icon">/</span>
                  <span className="mention-label">{item.name}</span>
                  <span className="mention-hint">{item.hint}</span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
      {!compact && (
        <p className="composer-hint text-subtle" id="composer-hint" aria-hidden>
          <kbd>Enter</kbd> send · <kbd>Shift</kbd>+<kbd>Enter</kbd> newline
        </p>
      )}
    </div>
  );
}
