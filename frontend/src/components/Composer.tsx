import * as React from "react";
import { Avatar } from "@/components/ui/avatar";
import { Badge } from "@/components/ui/badge";
import { shortModel } from "@/lib";
import ModelPicker from "../ai-support/ModelPicker";
import {
  Send,
  Square,
  ChevronDown,
  AtSign,
  Check,
  AlertCircle,
  CornerDownLeft,
} from "lucide-react";

interface Agent {
  name: string;
  display_name?: string;
  avatar?: string;
  status?: string;
}

interface ContextStats {
  budget_chars?: number;
  total_chars?: number;
  messages?: number;
  dropped_messages?: number;
  has_summary?: boolean;
  memory_notes?: number;
  kb_docs?: number;
}

interface ComposerProps {
  onSend: (text: string, options: { model: string | null }) => Promise<boolean | void> | boolean | void;
  placeholder?: string;
  channelName?: string;
  agents: Agent[];
  compact?: boolean;
  threadParent?: unknown;
  workingWith?: string[];
  offline?: boolean;
  sendFailed?: boolean;
  contextStats?: ContextStats | null;
  contextError?: string | null;
  onRefreshContext?: () => void;
  onToggleContextDetails?: () => void;
  contextDetailsOpen?: boolean;
  token?: string;
  model?: string | null;
  onModelChange?: (model: string) => void;
  working?: boolean;
  onStop?: () => void;
  prefill?: string;
  onPrefillConsumed?: () => void;
}

const COMMANDS = [
  { name: "retry", hint: "Retry the last agent reply", insert: "Please retry your last reply. " },
  { name: "approve", hint: "Approve the pending request", insert: "Approved — please continue. " },
  { name: "deny", hint: "Deny the pending request", insert: "Denied — do not proceed with that action. " },
  { name: "summarize", hint: "Ask for a summary", insert: "Please summarize the discussion so far. " },
];

export function Composer({
  onSend,
  placeholder,
  channelName,
  agents = [],
  compact = false,
  threadParent,
  workingWith = [],
  offline = false,
  sendFailed = false,
  contextStats,
  contextError,
  onRefreshContext,
  onToggleContextDetails,
  contextDetailsOpen,
  token,
  model,
  onModelChange,
  working = false,
  onStop,
  prefill,
  onPrefillConsumed,
}: ComposerProps) {
  const [draft, setDraft] = React.useState("");
  const [mention, setMention] = React.useState<{
    open: boolean;
    index: number;
    items: Array<{ name: string; label: string; kind: string; avatar?: string }>;
    query: string;
  }>({ open: false, index: 0, items: [], query: "" });
  const [slash, setSlash] = React.useState<{ open: boolean; index: number; query?: string }>({
    open: false,
    index: 0,
  });
  const [sending, setSending] = React.useState(false);
  const [modelOpen, setModelOpen] = React.useState(false);
  const [modelCatalog, setModelCatalog] = React.useState<Array<{ id: string; provider_name?: string; provider_id?: string }>>([]);

  const inputRef = React.useRef<HTMLTextAreaElement>(null);
  const mentionRef = React.useRef<HTMLUListElement>(null);
  const modelMenuRef = React.useRef<HTMLDivElement>(null);
  const modelButtonRef = React.useRef<HTMLButtonElement>(null);
  const [modelPopoverStyle, setModelPopoverStyle] = React.useState<React.CSSProperties>({});

  const providerOf = React.useCallback(    (modelId: string) => {
      const hit = (modelCatalog || []).find((m) => m.id === modelId);
      return hit?.provider_name || hit?.provider_id || "";
    },
    [modelCatalog]
  );

  const allMentions = React.useCallback(() => {
    const agentNames = (agents || []).map((a) => ({
      name: a.name,
      label: a.display_name || a.name,
      kind: "agent",
      avatar: a.avatar,
    }));
    return [
      { name: "channel", label: "Everyone in channel", kind: "channel" },
      ...agentNames,
    ];
  }, [agents]);

  React.useEffect(() => {
    if (!modelOpen) return undefined;
    const onPointerDown = (event: MouseEvent) => {
      if (modelMenuRef.current && !modelMenuRef.current.contains(event.target as Node)) {
        setModelOpen(false);
      }
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setModelOpen(false);
    };
    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [modelOpen]);

  React.useLayoutEffect(() => {
    if (!modelOpen || !modelButtonRef.current) return;
    const rect = modelButtonRef.current.getBoundingClientRect();
    const width = Math.min(320, Math.max(240, window.innerWidth - 16));
    const left = Math.min(Math.max(8, rect.left), window.innerWidth - width - 8);
    setModelPopoverStyle({
      position: "fixed",
      left,
      bottom: Math.max(8, window.innerHeight - rect.top + 8),
      width,
      maxHeight: Math.min(430, window.innerHeight - 16),
    });
  }, [modelOpen]);

  function handleChange(e: React.ChangeEvent<HTMLTextAreaElement>) {
    const value = e.target.value;
    setDraft(value);

    const match = value.match(/(?:^|\s)@(\w*)$/);
    if (match) {
      const query = match[1].toLowerCase();
      const items = allMentions().filter((m) =>
        m.name.toLowerCase().startsWith(query)
      );
      setMention({ open: items.length > 0, index: 0, items, query: match[1] });
    } else {
      setMention({ open: false, index: 0, items: [], query: "" });
    }

    const slashMatch = value.match(/(?:^|\s)\/(\w*)$/);
    setSlash(
      slashMatch
        ? { open: true, index: 0, query: slashMatch[1] }
        : { open: false, index: 0 }
    );
  }

  function insertMention(item: { name: string }) {
    const lastAt = draft.lastIndexOf("@");
    const before = draft.slice(0, lastAt);
    const after = draft.slice(lastAt + mention.query.length + 1);
    setDraft(`${before}@${item.name} ${after}`);
    setMention({ open: false, index: 0, items: [], query: "" });
    inputRef.current?.focus();
  }

  function slashItems() {
    const q = (slash.query || "").toLowerCase();
    return COMMANDS.filter((c) => c.name.startsWith(q));
  }

  function insertCommand(item: { insert: string }) {
    const idx = draft.lastIndexOf("/");
    const before = idx >= 0 ? draft.slice(0, idx) : draft;
    setDraft(`${before}${item.insert}`);
    setSlash({ open: false, index: 0 });
    inputRef.current?.focus();
  }

  function handleKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (slash.open) {
      const items = slashItems();
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setSlash((s) => ({ ...s, index: Math.min(s.index + 1, items.length - 1) }));
        return;
      }
      if (e.key === "ArrowUp") {
        e.preventDefault();
        setSlash((s) => ({ ...s, index: Math.max(s.index - 1, 0) }));
        return;
      }
      if ((e.key === "Enter" || e.key === "Tab") && items.length) {
        e.preventDefault();
        insertCommand(items[slash.index] || items[0]);
        return;
      }
      if (e.key === "Escape") {
        setSlash({ open: false, index: 0 });
        return;
      }
    }

    if (mention.open) {
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setMention((m) => ({ ...m, index: Math.min(m.index + 1, m.items.length - 1) }));
        return;
      }
      if (e.key === "ArrowUp") {
        e.preventDefault();
        setMention((m) => ({ ...m, index: Math.max(m.index - 1, 0) }));
        return;
      }
      if ((e.key === "Enter" || e.key === "Tab") && mention.items.length) {
        e.preventDefault();
        insertMention(mention.items[mention.index]);
        return;
      }
      if (e.key === "Escape") {
        setMention({ open: false, index: 0, items: [], query: "" });
        return;
      }
    }

    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  }

  async function send() {
    const text = draft.trim();
    if (!text || sending) return;
    setSending(true);
    try {
      const ok = await onSend(text, { model: model || null });
      if (ok === false) return;
      // Do not erase text typed while an earlier send was in flight.
      setDraft((current) => current.trim() === text ? "" : current);
      setMention((current) => current.open ? { ...current, open: false } : current);
      setSlash((current) => current.open ? { ...current, open: false } : current);
    } finally {
      setSending(false);
    }
  }

  // Auto-grow textarea
  React.useEffect(() => {
    const el = inputRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 180)}px`;
  }, [draft]);

  React.useEffect(() => {
    if (prefill == null || prefill === "") return;
    setDraft(prefill);
    onPrefillConsumed?.();
    requestAnimationFrame(() => {
      inputRef.current?.focus();
      const len = prefill.length;
      inputRef.current?.setSelectionRange(len, len);
    });
  }, [prefill, onPrefillConsumed]);

  React.useEffect(() => {
    if (mention.open && mentionRef.current) {
      const el = mentionRef.current.children[mention.index] as HTMLElement;
      el?.scrollIntoView({ block: "nearest" });
    }
  }, [mention.index, mention.open]);

  const canSend = draft.trim().length > 0;
  const ctxPct =
    contextStats && contextStats.budget_chars
      ? Math.min(
          100,
          Math.round((contextStats.total_chars! / contextStats.budget_chars) * 100)
        )
      : null;

  return (
    <div id="composer">
      {/* The register line: what this send will carry. */}
      <div className="composer-hints" style={{ marginBottom: 6 }}>
        {threadParent && <span className="chip">Replying in thread</span>}

        {workingWith.length > 0 && (
          <span className="chip chip-amber">
            {workingWith.slice(0, 3).join(", ")}
            {workingWith.length > 3 && ` +${workingWith.length - 3}`} working
          </span>
        )}

        {ctxPct !== null && (
          <div style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: 8 }}>
            <button
              type="button"
              onClick={onRefreshContext}
              title={`Context ${contextStats?.total_chars || 0} of ${contextStats?.budget_chars || 0} characters · ${contextStats?.messages || 0} messages`}
              className={`chip ${ctxPct >= 80 ? "chip-hold" : ""}`}
            >
              <span className="progress-track" style={{ width: 44 }}>
                <span
                  className={`progress-fill ${ctxPct >= 80 ? "is-hold" : ""}`}
                  style={{ width: `${ctxPct}%` }}
                />
              </span>
              Context {ctxPct}%
            </button>
            {onToggleContextDetails && (
              <button
                type="button"
                onClick={onToggleContextDetails}
                className="btn btn-ghost btn-sm"
              >
                {contextDetailsOpen ? "Hide detail" : "Detail"}
              </button>
            )}
          </div>
        )}

        {ctxPct === null && contextError && (
          <button type="button" onClick={onRefreshContext} className="chip chip-hold" style={{ marginLeft: "auto" }}>
            <AlertCircle size={12} />
            Context unavailable — retry
          </button>
        )}
      </div>

      {offline && (
        <div id="composer-offline-note" role="status" className="send-failure-banner" style={{ borderColor: "rgba(240,180,41,0.45)", background: "var(--lamp-hold-wash)" }}>
          <AlertCircle size={14} className="shrink-0" />
          <span className="send-failure-text">
            No link to the workspace. This message is held and sent when the link comes back.
          </span>
        </div>
      )}

      {sendFailed && (
        <div role="alert" className="send-failure-banner">
          <AlertCircle size={14} className="shrink-0" />
          <span className="send-failure-text">
            The message did not send. Your draft is kept — send it again.
          </span>
        </div>
      )}

      {/* A slab, docked. Not a floating pill. */}
      <div className="composer-row">
        {mention.open && (
          <ul
            ref={mentionRef}
            role="listbox"
            aria-label="Mention teammates"
            className="dropdown-menu"
            style={{ bottom: "calc(100% + 6px)", left: 0, width: 280 }}
          >
            <li className="dropdown-section reg">Hand it to</li>
            {mention.items.map((item, i) => (
              <li key={item.name}>
                <button
                  type="button"
                  onMouseDown={(e) => {
                    e.preventDefault();
                    insertMention(item);
                  }}
                  role="option"
                  aria-selected={i === mention.index}
                  className="dropdown-item"
                  style={i === mention.index ? { background: "var(--housing-high)", color: "var(--ink-on-housing)" } : undefined}
                >
                  {item.kind === "agent" ? (
                    <Avatar name={item.label} kind="agent" size="xs" avatar={item.avatar} />
                  ) : (
                    <AtSign size={13} className="shrink-0" />
                  )}
                  <span className="truncate">{item.label}</span>
                </button>
              </li>
            ))}
          </ul>
        )}

        {slash.open && slashItems().length > 0 && (
          <ul
            role="listbox"
            aria-label="Commands"
            className="dropdown-menu"
            style={{ bottom: "calc(100% + 6px)", left: 0, width: 320 }}
          >
            <li className="dropdown-section reg">Commands</li>
            {slashItems().map((item, i) => (
              <li key={item.name}>
                <button
                  type="button"
                  onMouseDown={(e) => {
                    e.preventDefault();
                    insertCommand(item);
                  }}
                  role="option"
                  aria-selected={i === slash.index}
                  className="dropdown-item"
                  style={i === slash.index ? { background: "var(--housing-high)", color: "var(--ink-on-housing)" } : undefined}
                >
                  <span className="reg reg-lit">/{item.name}</span>
                  <span className="truncate">{item.hint}</span>
                </button>
              </li>
            ))}
          </ul>
        )}

        <div ref={modelMenuRef} className="relative shrink-0">
          <button
            ref={modelButtonRef}
            type="button"
            onClick={() => setModelOpen((o) => !o)}
            aria-haspopup="listbox"
            aria-expanded={modelOpen}
            className="model-picker-toggle"
            title={model ? `Model: ${model}` : "Auto: each teammate's default model"}
          >
            <span className="model-picker-value">
              {model
                ? `${providerOf(model) ? `${providerOf(model)} / ` : ""}${shortModel(model)}`
                : "Auto"}
            </span>
            <ChevronDown size={11} className="model-picker-chevron" />
          </button>

          {modelOpen && (
            <div style={modelPopoverStyle} className="model-picker-pop">
              <button
                type="button"
                onClick={() => {
                  onModelChange?.("");
                  setModelOpen(false);
                }}
                className="model-option"
                style={!model ? { background: "var(--housing-high)" } : undefined}
              >
                <span className="model-option-name">Auto — each teammate's default</span>
                {!model && <Check size={13} className="model-live" />}
              </button>
              <ModelPicker
                id="composer-model-picker"
                token={token}
                providerId=""
                apiKey=""
                value={model || ""}
                onChange={(id: string) => {
                  onModelChange?.(id);
                  setModelOpen(false);
                }}
                onModelsLoaded={(list: Array<{ id: string; provider_name?: string; provider_id?: string }>) => setModelCatalog(list || [])}
                placeholder="Search models"
                openOnMount
                inline
              />
            </div>
          )}
        </div>

        <textarea
          ref={inputRef}
          id={compact ? "thread-input" : "msg-input"}
          value={draft}
          onChange={handleChange}
          onKeyDown={handleKeyDown}
          placeholder={placeholder || `Message ${channelName || "this room"} — @ to hand it to a teammate, / for commands`}
          rows={1}
          aria-label={placeholder || `Message ${channelName || "this room"}`}
          aria-expanded={mention.open || slash.open}
          aria-describedby={offline ? "composer-offline-note" : undefined}
          className="composer-input"
        />

        <button
          type="button"
          onClick={() => {
            setDraft((d) => {
              const prefix = d && !/\s$/.test(d) ? `${d} ` : d;
              return `${prefix}@`;
            });
            const items = allMentions();
            setMention({ open: items.length > 0, index: 0, items, query: "" });
            inputRef.current?.focus();
          }}
          className="btn btn-ghost btn-icon"
          title="Hand this to a teammate"
          aria-label="Hand this to a teammate"
        >
          <AtSign size={14} />
        </button>

        {working ? (
          <button
            type="button"
            onClick={onStop}
            className="btn btn-danger"
            title="Stop generation"
          >
            <Square size={12} fill="currentColor" />
            Stop
          </button>
        ) : (
          <button
            type="button"
            onClick={send}
            disabled={!canSend || sending}
            className="btn btn-primary btn-icon"
            aria-label={sending ? "Sending" : "Send message"}
            title="Send — Enter. Shift+Enter for a new line."
          >
            {sending ? <span className="spinner spinner-sm" /> : <CornerDownLeft size={14} />}
          </button>
        )}
      </div>

      {/* Quick hand-offs. */}
      {agents.length > 0 && (
        <div className="composer-hints" style={{ marginTop: 6 }}>
          <span className="reg">Hand to</span>
          {agents.slice(0, 4).map((agent) => (
            <button
              key={agent.name}
              type="button"
              onClick={() => {
                const tag = `@${agent.name} `;
                if (!draft.includes(tag)) {
                  setDraft((d) => (d ? `${d} ${tag}` : tag));
                }
                inputRef.current?.focus();
              }}
              className="chip"
            >
              {agent.display_name || agent.name}
            </button>
          ))}
          <span className="reg" style={{ marginLeft: "auto" }}>
            Enter sends · Shift+Enter newline
          </span>
        </div>
      )}
    </div>
  );
}
