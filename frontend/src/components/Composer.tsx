import * as React from "react";
import { Avatar } from "@/components/ui/avatar";
import { Badge } from "@/components/ui/badge";
import { shortModel } from "@/lib";
import ModelPicker from "../ai-support/ModelPicker";
import {
  Send,
  Square,
  Sparkles,
  ChevronDown,
  AtSign,
  Terminal,
  Activity,
  Check,
  AlertCircle,
  RefreshCw,
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
    <div
      id="composer"
      className="p-3 sm:px-6 sm:pb-5 w-full max-w-4xl mx-auto shrink-0 select-none z-10 transition-all"
    >
      {/* Thread & Context Metadata Pills */}
      <div className="flex flex-wrap items-center gap-2 mb-2 px-1 text-xs">
        {threadParent && (
          <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md bg-zinc-850 text-zinc-300 border border-zinc-750 text-[11px]">
            Replying in thread
          </span>
        )}

        {workingWith.length > 0 && (
          <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-md bg-violet-500/10 text-violet-300 border border-violet-500/20 text-[11px]">
            <span className="w-1.5 h-1.5 rounded-full bg-violet-400 animate-pulse" />
            Working with {workingWith.slice(0, 3).join(", ")}
            {workingWith.length > 3 && ` +${workingWith.length - 3}`}
          </span>
        )}

        {ctxPct !== null && (
          <div className="flex items-center gap-1.5 ml-auto">
            <button
              type="button"
              onClick={onRefreshContext}
              title={`Context ${contextStats?.total_chars || 0}/${contextStats?.budget_chars || 0} chars · ${contextStats?.messages || 0} messages`}
              className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-md border text-[11px] font-mono transition-colors ${
                ctxPct >= 80
                  ? "bg-rose-500/10 text-rose-300 border-rose-500/30"
                  : "bg-zinc-900 text-zinc-400 border-zinc-800 hover:text-zinc-200"
              }`}
            >
              <div className="w-12 h-1 rounded-full bg-zinc-800 overflow-hidden">
                <div
                  className={`h-full transition-all ${
                    ctxPct >= 80 ? "bg-rose-500" : "bg-violet-500"
                  }`}
                  style={{ width: `${ctxPct}%` }}
                />
              </div>
              <span>{ctxPct}% ctx</span>
            </button>

            {onToggleContextDetails && (
              <button
                type="button"
                onClick={onToggleContextDetails}
                className="text-[11px] text-zinc-500 hover:text-zinc-300 transition-colors"
              >
                {contextDetailsOpen ? "Hide" : "Details"}
              </button>
            )}
          </div>
        )}

        {ctxPct === null && contextError && (
          <button
            type="button"
            onClick={onRefreshContext}
            className="ml-auto inline-flex items-center gap-1 text-[11px] text-amber-400 hover:underline"
          >
            <AlertCircle className="w-3 h-3" />
            <span>Context retry</span>
          </button>
        )}
      </div>

      {/* Offline Alert */}
      {offline && (
        <div id="composer-offline-note" role="status" className="mb-2 p-2 rounded-lg bg-amber-500/10 border border-amber-500/20 text-amber-300 text-xs flex items-center gap-2">
          <AlertCircle className="w-3.5 h-3.5 shrink-0" />
          <span>Offline — messages will queue and send once reconnected.</span>
        </div>
      )}

      {/* Send Failed Alert */}
      {sendFailed && (
        <div role="alert" className="mb-2 p-2 rounded-lg bg-rose-500/10 border border-rose-500/20 text-rose-300 text-xs flex items-center gap-2">
          <AlertCircle className="w-3.5 h-3.5 shrink-0" />
          <span>Send failed — draft kept. Check connection and send again.</span>
        </div>
      )}

      {/* Main OpenCode-Style Floating Dock */}
      <div className="relative rounded-2xl border border-zinc-800/90 bg-zinc-900/80 backdrop-blur-xl shadow-xl transition-all duration-150 focus-within:border-zinc-700 focus-within:ring-2 focus-within:ring-violet-500/20">
        {/* Mention Suggestions Popup */}
        {mention.open && (
          <ul
            ref={mentionRef}
            role="listbox"
            aria-label="Mention teammates"
            className="absolute bottom-full left-0 mb-2 w-72 max-h-56 overflow-y-auto rounded-xl border border-zinc-800 bg-zinc-900/95 p-1 text-zinc-200 shadow-2xl backdrop-blur-md z-50 animate-in fade-in-0 zoom-in-95"
          >
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
                  className={`w-full flex items-center gap-2 px-2.5 py-1.5 rounded-lg text-xs transition-colors text-left ${
                    i === mention.index
                      ? "bg-zinc-800 text-zinc-100 font-medium"
                      : "hover:bg-zinc-850 text-zinc-300"
                  }`}
                >
                  {item.kind === "agent" ? (
                    <Avatar name={item.label} kind="agent" size="xs" avatar={item.avatar} />
                  ) : (
                    <div className="flex items-center justify-center w-5 h-5 rounded-md bg-zinc-800 text-zinc-400">
                      <AtSign className="w-3 h-3" />
                    </div>
                  )}
                  <span className="truncate">{item.label}</span>
                </button>
              </li>
            ))}
          </ul>
        )}

        {/* Slash Command Suggestions Popup */}
        {slash.open && slashItems().length > 0 && (
          <ul
            role="listbox"
            aria-label="Slash commands"
            className="absolute bottom-full left-0 mb-2 w-80 max-h-56 overflow-y-auto rounded-xl border border-zinc-800 bg-zinc-900/95 p-1 text-zinc-200 shadow-2xl backdrop-blur-md z-50 animate-in fade-in-0 zoom-in-95"
          >
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
                  className={`w-full flex items-center gap-2 px-2.5 py-1.5 rounded-lg text-xs transition-colors text-left ${
                    i === slash.index
                      ? "bg-zinc-800 text-zinc-100 font-medium"
                      : "hover:bg-zinc-850 text-zinc-300"
                  }`}
                >
                  <Terminal className="w-3.5 h-3.5 text-violet-400 shrink-0" />
                  <span className="font-semibold text-zinc-200">/{item.name}</span>
                  <span className="text-[11px] text-zinc-500 truncate">{item.hint}</span>
                </button>
              </li>
            ))}
          </ul>
        )}

        {/* Text Input Area */}
        <div className="p-3 pb-1">
          <textarea
            ref={inputRef}
            id={compact ? "thread-input" : "msg-input"}
            value={draft}
            onChange={handleChange}
            onKeyDown={handleKeyDown}
            placeholder={
              placeholder || `Message #${channelName || "general"}, @mention teammates, or / for commands…`
            }
            rows={1}
            aria-label={placeholder || `Message ${channelName || "general"}`}
            aria-expanded={mention.open || slash.open}
            aria-describedby={offline ? "composer-offline-note" : undefined}
            className="w-full bg-transparent text-xs sm:text-sm text-zinc-100 placeholder:text-zinc-500 focus:outline-none resize-none leading-relaxed min-h-[38px] max-h-[180px]"
          />
        </div>

        {/* Bottom Toolbar Inside Composer Dock */}
        <div className="flex items-center justify-between px-3 py-2 border-t border-zinc-800/60 bg-zinc-950/40 rounded-b-2xl">
          {/* Left Actions: Model Selector & Agent Mention Chips */}
          <div className="flex items-center gap-1.5 flex-wrap min-w-0">
            {/* Quick Mention Button */}
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
              className="p-1 rounded-md text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800 transition-colors shrink-0"
              title="Mention teammate (@)"
              aria-label="Mention teammate"
            >
              <AtSign className="w-3.5 h-3.5" />
            </button>

            {/* Model Selector Pill */}
            <div ref={modelMenuRef} className="relative shrink-0">
              <button
                ref={modelButtonRef}
                type="button"
                onClick={() => setModelOpen((o) => !o)}
                aria-haspopup="listbox"
                aria-expanded={modelOpen}
                className={`inline-flex items-center gap-1 px-2 py-1 rounded-md text-[11px] font-medium border transition-colors ${
                  model
                    ? "bg-violet-500/10 text-violet-300 border-violet-500/30 hover:bg-violet-500/20"
                    : "bg-zinc-850/80 text-zinc-300 border-zinc-750 hover:bg-zinc-800"
                }`}
                title={model ? `Model: ${model}` : "Auto: agent default model"}
              >
                <Sparkles className="w-3 h-3 text-violet-400" />
                <span className="max-w-[120px] truncate">
                  {model
                    ? `${providerOf(model) ? `${providerOf(model)} / ` : ""}${shortModel(model)}`
                    : "Model: Auto"}
                </span>
                <ChevronDown className="w-3 h-3 opacity-60" />
              </button>

              {modelOpen && (
                <div style={modelPopoverStyle} className="z-[70] overflow-y-auto rounded-xl border border-zinc-800 bg-zinc-900/98 p-2 shadow-2xl backdrop-blur-xl animate-in fade-in-0 zoom-in-95">
                  <button
                    type="button"
                    onClick={() => {
                      onModelChange?.("");
                      setModelOpen(false);
                    }}
                    className={`w-full flex items-center justify-between px-2.5 py-1.5 mb-1.5 rounded-lg text-xs transition-colors ${
                      !model
                        ? "bg-violet-600 text-white font-medium"
                        : "hover:bg-zinc-800 text-zinc-300"
                    }`}
                  >
                    <span>Auto (Agent Default)</span>
                    {!model && <Check className="w-3.5 h-3.5" />}
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
                    placeholder="Search available models…"
                    openOnMount
                    inline
                  />
                </div>
              )}
            </div>

            {/* Quick Agent Chips (Top 3 agents) */}
            <div className="hidden sm:flex items-center gap-1">
              {agents.slice(0, 3).map((agent) => (
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
                  className="px-2 py-0.5 rounded-md text-[10px] text-zinc-400 hover:text-zinc-200 bg-zinc-900/80 hover:bg-zinc-850 border border-zinc-800 transition-colors"
                >
                  @{agent.name}
                </button>
              ))}
            </div>
          </div>

          {/* Right Actions: Stop & Send Button */}
          <div className="flex items-center gap-2 shrink-0">
            <span className="hidden md:inline-flex items-center gap-1 text-[10px] text-zinc-400 font-mono">
              <span>↵ send</span>
              <span>·</span>
              <span>⇧↵ newline</span>
            </span>

            {working && (
              <button
                type="button"
                onClick={onStop}
                className="flex items-center justify-center h-7 px-2.5 rounded-md bg-rose-500/20 text-rose-300 border border-rose-500/30 hover:bg-rose-500/30 text-xs font-medium transition-colors"
                title="Stop generation"
                aria-label="Stop generation"
              >
                <Square className="w-3 h-3 fill-current mr-1" />
                <span>Stop</span>
              </button>
            )}

            <button
              type="button"
              onClick={send}
              disabled={!canSend || sending}
              className={`flex items-center justify-center w-7 h-7 rounded-md transition-all duration-150 ${
                canSend && !sending
                  ? "bg-violet-600 text-white shadow-sm hover:bg-violet-500 active:scale-95"
                  : "bg-zinc-800 text-zinc-500 cursor-not-allowed opacity-50"
              }`}
              aria-label={sending ? "Sending…" : "Send message"}
            >
              <CornerDownLeft className="w-3.5 h-3.5" />
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
