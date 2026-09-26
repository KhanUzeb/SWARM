import * as React from "react";
import { Avatar } from "@/components/ui/avatar";
import { Flap, Lamp } from "@/components/Flap";
import { RichBody } from "../ui.jsx";
import { fmtTime, isAgentError, isResumable, shortModel, EMOJI } from "@/lib";
import {
  Activity,
  ArrowDown,
  ChevronDown,
  CornerDownRight,
  MessageSquare,
  RotateCcw,
  Smile,
  Terminal,
  Trash2,
} from "lucide-react";

interface Reaction {
  emoji: string;
  author: string;
}

interface Message {
  id: number | string;
  author: string;
  author_kind?: "human" | "agent" | "system";
  body?: string;
  created_at?: number | string;
  parent_id?: number | string | null;
  streaming?: boolean;
  model?: string;
}

interface WorkEvent {
  seq: number;
  type: string;
  step_id?: string;
  payload?: {
    tool?: string;
    agent?: string;
    decision?: string;
    error?: string;
  };
}

interface WorkSession {
  id: string;
  status: string;
  objective?: string;
}

interface Agent {
  name: string;
  display_name?: string;
  avatar?: string;
  job?: string;
  status?: string;
}

interface MessageListProps {
  messages: Record<string | number, Message>;
  order: Array<string | number>;
  agents?: Agent[];
  allAgents?: Agent[];
  user?: { handle?: string };
  onReply?: (id: number | string) => void;
  onReact: (id: number | string, emoji: string) => void;
  onUnreact?: (id: number | string, emoji: string) => void;
  onDelete?: (m: Message) => void;
  onOpenThread?: (id: number | string) => void;
  onRetry?: (m: Message) => void;
  replyCounts: Record<string | number, number>;
  reactions: Record<string | number, Reaction[]>;
  channelId?: string;
  onLoadMore?: () => void;
  hasMore?: boolean;
  loadingMore?: boolean;
  typing?: string | null;
  groupedWith: (prev?: Message, cur?: Message) => boolean;
  retryingId?: number | string | null;
  streamingAgents?: Record<string, boolean>;
  streamText?: Record<string, string>;
  workByMessage?: Record<string | number, WorkSession>;
  eventsByWork?: Record<string, WorkEvent[]>;
  canModerate?: boolean;
  onQuickStart?: (text: string) => void;
  channelName?: string;
}

// Shared stable refs: `map[id] || []` would allocate a new array every
// render and defeat MessageRow memoization for every row.
const EMPTY_REACTIONS: Reaction[] = [];
const EMPTY_WORK_EVENTS: WorkEvent[] = [];

// Latest-ref wrapper: parents (App) pass fresh closures every render.
function useLatest<T>(value: T) {
  const ref = React.useRef(value);
  ref.current = value;
  return ref;
}

function workFlap(status?: string): { value: string; tone: "go" | "amber" | "hold" | "unlit" } {
  switch (status) {
    case "waiting_for_approval":
    case "needs_approval":
      return { value: "Hold", tone: "hold" };
    case "running":
    case "active":
      return { value: "Run", tone: "amber" };
    case "completed":
    case "done":
      return { value: "Done", tone: "go" };
    case "failed":
      return { value: "Fail", tone: "hold" };
    default:
      return { value: status || "Idle", tone: "unlit" };
  }
}

export function MessageList({
  messages,
  order,
  agents = [],
  allAgents = [],
  user,
  onReply,
  onReact,
  onUnreact,
  onDelete,
  onOpenThread,
  onRetry,
  replyCounts = {},
  reactions = {},
  channelId,
  onLoadMore,
  hasMore,
  loadingMore,
  typing,
  groupedWith,
  retryingId,
  streamingAgents,
  streamText = {},
  workByMessage = {},
  eventsByWork = {},
  canModerate,
  onQuickStart,
  channelName,
}: MessageListProps) {
  const endRef = React.useRef<HTMLDivElement>(null);
  const logRef = React.useRef<HTMLDivElement>(null);
  const [pinned, setPinned] = React.useState(true);

  React.useEffect(() => {
    setPinned(true);
  }, [channelId]);

  function handleScroll() {
    const el = logRef.current;
    if (!el) return;
    setPinned(el.scrollHeight - el.scrollTop - el.clientHeight < 80);
  }

  function jumpToLatest() {
    setPinned(true);
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }

  const streamChars = Object.values(streamText).reduce(
    (n, t) => n + String(t || "").length,
    0,
  );

  React.useEffect(() => {
    if (endRef.current && pinned && !hasMore) {
      endRef.current.scrollIntoView({ behavior: "smooth", block: "end" });
    }
  }, [order.length, hasMore, pinned, streamChars]);

  const roots = order.map((id) => messages[id]).filter(Boolean);
  const liveStreams = Object.entries(streamText).filter(
    ([, text]) => text && text.length > 0,
  );
  const quickStarts = buildQuickStarts(agents, allAgents, channelName);

  const onReplyRef = useLatest(onReply);
  const onReactRef = useLatest(onReact);
  const onUnreactRef = useLatest(onUnreact);
  const onDeleteRef = useLatest(onDelete);
  const onOpenThreadRef = useLatest(onOpenThread);
  const onRetryRef = useLatest(onRetry);
  const stableOnReply = React.useCallback(
    (id: number | string) => onReplyRef.current?.(id),
    [],
  );
  const stableOnReact = React.useCallback(
    (id: number | string, emoji: string) => onReactRef.current(id, emoji),
    [],
  );
  const stableOnUnreact = React.useCallback(
    (id: number | string, emoji: string) => onUnreactRef.current?.(id, emoji),
    [],
  );
  const stableOnDelete = React.useCallback(
    (m: Message) => onDeleteRef.current?.(m),
    [],
  );
  const stableOnOpenThread = React.useCallback(
    (id: number | string) => onOpenThreadRef.current?.(id),
    [],
  );
  const stableOnRetry = React.useCallback(
    (m: Message) => onRetryRef.current?.(m),
    [],
  );

  // ── Empty room: the board is set, nothing has run on it yet ──
  if (roots.length === 0 && !loadingMore && liveStreams.length === 0 && !typing) {
    return (
      <div id="log">
        <div className="log-empty">
          <div className="talk-empty">
            <h2>{channelName ? channelName : "This room is empty"}</h2>
            <p>
              Mention a teammate by name to hand it the room. Everything it does —
              every tool call, handoff, and failure — is printed here in the order
              it happened.
            </p>

            {onQuickStart && quickStarts.length > 0 && (
              <div className="talk-quickstarts">
                {quickStarts.map((item) => (
                  <button
                    key={item.id}
                    type="button"
                    onClick={() => onQuickStart(item.text)}
                    className="talk-quickstart"
                  >
                    <span className="qs-label">{item.label}</span>
                    {item.hint && <span className="qs-hint">{item.hint}</span>}
                  </button>
                ))}
              </div>
            )}
          </div>
        </div>
      </div>
    );
  }

  return (
    <div id="log" ref={logRef} onScroll={handleScroll}>
      {!pinned && roots.length > 0 && (
        <button
          type="button"
          onClick={jumpToLatest}
          aria-label="Jump to the newest line"
          className="btn btn-sm jump-latest"
        >
          <ArrowDown size={13} />
          Newest
        </button>
      )}

      <div className="log-inner">
        {hasMore && (
          <div style={{ display: "grid", placeItems: "center", paddingBottom: 8 }}>
            <button onClick={onLoadMore} disabled={loadingMore} className="btn btn-ghost btn-sm" aria-live="polite">
              {loadingMore ? "Loading earlier lines" : "Load earlier lines"}
            </button>
          </div>
        )}

        {loadingMore && roots.length === 0 && (
          <div aria-label="Loading lines" role="status" style={{ display: "grid", gap: 12 }}>
            {[0, 1, 2].map((i) => (
              <div key={i} className="entry" aria-hidden>
                <div className="entry-head">
                  <div className="skeleton skeleton-text" style={{ width: "58%" }} />
                </div>
                <div style={{ display: "grid", gap: 8 }}>
                  <div className="skeleton skeleton-text" />
                  <div className="skeleton skeleton-text" />
                  <div className="skeleton skeleton-text" style={{ width: "32%" }} />
                </div>
              </div>
            ))}
          </div>
        )}

        {roots.map((m, i) => {
          const prev = roots[i - 1];
          const dayLabel = daySeparator(prev, m);
          const grouped = groupedWith(prev, m);
          const label =
            m.author_kind === "agent"
              ? allAgents.find((a) => a.name === m.author)?.display_name || m.author
              : m.author;
          const agent =
            m.author_kind === "agent"
              ? allAgents.find((a) => a.name === m.author)
              : undefined;

          return (
            <React.Fragment key={m.id}>
              {dayLabel && (
                <div className="day-sep" role="separator">
                  {dayLabel}
                </div>
              )}
              <MessageRow
                m={m}
                grouped={grouped}
                label={label}
                agent={agent}
                reactions={reactions[m.id] ?? EMPTY_REACTIONS}
                replyCount={replyCounts[m.id] || 0}
                onReply={onReply ? stableOnReply : undefined}
                onReact={stableOnReact}
                onUnreact={onUnreact ? stableOnUnreact : undefined}
                onDelete={onDelete ? stableOnDelete : undefined}
                onOpenThread={onOpenThread ? stableOnOpenThread : undefined}
                onRetry={onRetry ? stableOnRetry : undefined}
                retrying={retryingId === m.id}
                myHandle={user?.handle}
                streaming={!!(streamingAgents && streamingAgents[m.author])}
                work={workByMessage?.[m.id]}
                workEvents={
                  (workByMessage?.[m.id] && eventsByWork?.[workByMessage[m.id].id]) ||
                  EMPTY_WORK_EVENTS
                }
                canDelete={m.author === user?.handle || !!canModerate}
              />
            </React.Fragment>
          );
        })}

        {/* Live lines: the machine is still setting type. */}
        {liveStreams.map(([author, text]) => {
          const who = allAgents.find((a) => a.name === author);
          return (
            <div className="entry streaming-entry" key={`stream-${author}`}>
              <div className="entry-head">
                <span className="entry-author">{who?.display_name || author}</span>
                <span className="entry-time">now</span>
              </div>
              <div className="entry-body">
                <RichBody body={text} />
                <span className="streaming-cursor" aria-hidden="true" />
                <span className="sr-only">is still writing</span>
              </div>
            </div>
          );
        })}

        {typing && !liveStreams.length && (
          <div className="entry">
            <div className="entry-head">
              <span className="entry-author">{typing}</span>
            </div>
            <div className="entry-body">
              <span className="bui-loading">
                <span className="spinner spinner-sm" aria-hidden="true" />
                reading the room
              </span>
            </div>
          </div>
        )}

        <div ref={endRef} />
      </div>
    </div>
  );
}

const MessageRow = React.memo(function MessageRow({
  m,
  grouped,
  label,
  agent,
  reactions,
  replyCount,
  onReply,
  onReact,
  onUnreact,
  onDelete,
  onOpenThread,
  onRetry,
  retrying,
  myHandle,
  streaming,
  work,
  workEvents,
  canDelete,
}: {
  m: Message;
  grouped?: boolean;
  label: string;
  agent?: Agent;
  reactions: Reaction[];
  replyCount: number;
  onReply?: (id: number | string) => void;
  onReact: (id: number | string, emoji: string) => void;
  onUnreact?: (id: number | string, emoji: string) => void;
  onDelete?: (m: Message) => void;
  onOpenThread?: (id: number | string) => void;
  onRetry?: (m: Message) => void;
  retrying?: boolean;
  myHandle?: string;
  streaming?: boolean;
  work?: WorkSession;
  workEvents: WorkEvent[];
  canDelete?: boolean;
}) {
  const [pickOpen, setPickOpen] = React.useState(false);
  const pickRef = React.useRef<HTMLDivElement>(null);

  React.useEffect(() => {
    if (!pickOpen) return undefined;
    function onPointerDown(e: MouseEvent) {
      if (pickRef.current && !pickRef.current.contains(e.target as Node)) setPickOpen(false);
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") setPickOpen(false);
    }
    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [pickOpen]);

  const counts: Record<string, number> = {};
  const byEmoji: Record<string, string[]> = {};
  for (const r of reactions || []) {
    counts[r.emoji] = (counts[r.emoji] || 0) + 1;
    (byEmoji[r.emoji] = byEmoji[r.emoji] || []).push(r.author);
  }

  const toggleReact = (emoji: string) => {
    const mine = (byEmoji[emoji] || []).includes(myHandle || "");
    if (mine && onUnreact) onUnreact(m.id, emoji);
    else onReact(m.id, emoji);
  };

  const isMe = m.author === myHandle;
  const failedAgent = m.author_kind === "agent" && isAgentError(m.body);
  const stoppedAgent =
    m.author_kind === "agent" &&
    !failedAgent &&
    String(m.body || "").includes("[reply cut off");
  const resumable = m.author_kind === "agent" && isResumable(m.body);

  // A system line is the machine register speaking about itself.
  if (m.author_kind === "system") {
    return (
      <div className="sys-line" style={{ padding: "6px 0" }}>
        <Activity size={12} className="shrink-0" aria-hidden="true" />
        <RichBody body={m.body || ""} />
      </div>
    );
  }

  const avatarKind = agent ? "agent" : "human";
  const workState = workFlap(work?.status);

  return (
    <div className={`entry ${grouped ? "grouped" : ""} ${isMe ? "mine" : ""} ${streaming ? "is-streaming" : ""}`}>
      <div className="entry-head">
        {!grouped && (
          <>
            <div style={{ display: "flex", alignItems: "center", gap: 6, minWidth: 0 }}>
              <Avatar
                name={label}
                kind={avatarKind}
                size="xs"
                avatar={agent?.avatar}
                className="shrink-0"
              />
              <span className="entry-author">{label}</span>
            </div>
            <span className="entry-time">{fmtTime(m.created_at)}</span>
            {agent?.job && !grouped && <span className="entry-job">{agent.job}</span>}
          </>
        )}
      </div>

      <div className="entry-main">
        {/* The machine register: everything the run did, printed on the line. */}
        {(m.model || work) && !grouped && (
          <div className="entry-annotations">
            {m.model && (
              <span className="tool-chip tool-chip-icon" title={`Answered with ${m.model}`}>
                {shortModel(m.model, 22)}
              </span>
            )}
            {work && <Flap value={workState.value} tone={workState.tone} />}
          </div>
        )}

        <div
          className={`entry-body ${failedAgent ? "is-failed" : ""} ${stoppedAgent ? "is-stopped" : ""}`}
        >
          <RichBody body={m.body || ""} />
        </div>

        {workEvents.length > 0 && <AgentTrace events={workEvents} />}

        {resumable && onRetry && (
          <div className="entry-foot">
            <button
              type="button"
              onClick={() => onRetry(m)}
              disabled={retrying}
              className="btn btn-sm"
            >
              <RotateCcw size={12} />
              {retrying ? "Retrying" : stoppedAgent ? "Resume" : "Retry"}
            </button>
          </div>
        )}

        <div className="entry-foot agent-msg-actions">
          {onReply && (
            <button
              onClick={() => onReply(m.parent_id || m.id)}
              className="msg-mini-action"
              title="Reply"
              aria-label={`Reply to ${label}`}
            >
              <MessageSquare size={13} />
            </button>
          )}
          <div className="relative" ref={pickRef}>
            <button
              onClick={() => setPickOpen((o) => !o)}
              className="msg-mini-action"
              title="React"
              aria-label={`React to the line from ${label}`}
              aria-expanded={pickOpen}
            >
              <Smile size={13} />
            </button>
            {pickOpen && (
              <ReactPicker onPick={(emoji) => onReact(m.id, emoji)} onClose={() => setPickOpen(false)} />
            )}
          </div>
          {onDelete && canDelete && (
            <button
              onClick={() => {
                if (window.confirm("Delete this line?")) onDelete(m);
              }}
              className="msg-mini-action is-danger"
              title="Delete"
              aria-label="Delete line"
            >
              <Trash2 size={13} />
            </button>
          )}

          <ReactionChips counts={counts} byEmoji={byEmoji} myHandle={myHandle} onToggle={toggleReact} />

          {!grouped && !m.parent_id && replyCount > 0 && onOpenThread && (
            <button onClick={() => onOpenThread(m.id)} className="thread-link" style={{ marginLeft: "auto" }}>
              <CornerDownRight size={12} />
              {replyCount} {replyCount === 1 ? "reply" : "replies"}
            </button>
          )}
        </div>
      </div>
    </div>
  );
});

const AgentTrace = React.memo(function AgentTrace({ events }: { events: WorkEvent[] }) {
  const [open, setOpen] = React.useState(false);
  const tools: string[] = [];
  const seen = new Set<string>();

  for (const e of events) {
    const t = e.payload?.tool || (e.type.startsWith("tool_") ? e.step_id : null);
    if (t && !seen.has(t)) {
      seen.add(t);
      tools.push(t);
    }
  }

  if (tools.length === 0 && events.length === 0) return null;

  return (
    <div className="trace">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        className="trace-toggle"
      >
        <Terminal size={12} className="shrink-0" />
        <span>
          {tools.length > 0 ? tools.slice(0, 3).join(" · ") : "Execution trace"}
          {tools.length > 3 && ` +${tools.length - 3}`}
        </span>
        <span style={{ opacity: 0.6 }}>
          {events.length} step{events.length === 1 ? "" : "s"}
        </span>
        <ChevronDown
          size={12}
          style={{ transform: open ? "rotate(180deg)" : "none", transition: "transform 110ms" }}
        />
      </button>

      {open && (
        <div className="trace-body">
          {events.map((e) => (
            <div className="thinking-step" key={e.seq}>
              <span className="node-index">{String(e.seq).padStart(3, "0")}</span>
              <span className="thinking-step-detail">{traceLabel(e)}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
});

function ReactPicker({
  onPick,
  onClose,
}: {
  onPick: (emoji: string) => void;
  onClose: () => void;
}) {
  const ref = React.useRef<HTMLDivElement>(null);
  React.useEffect(() => {
    function onDown(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) onClose();
    }
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [onClose]);

  return (
    <div ref={ref} className="dropdown-menu" style={{ bottom: "calc(100% + 6px)", left: 0, width: 232 }} role="menu">
      <div className="dropdown-section reg">React</div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(6, 1fr)", gap: 2 }}>
        {EMOJI.map((emoji: string) => (
          <button
            key={emoji}
            type="button"
            onClick={() => {
              onPick(emoji);
              onClose();
            }}
            className="msg-mini-action"
            style={{ width: "100%", height: 30, fontSize: 15 }}
            aria-label={`React with ${emoji}`}
          >
            {emoji}
          </button>
        ))}
      </div>
    </div>
  );
}

function ReactionChips({
  counts,
  byEmoji,
  myHandle,
  onToggle,
}: {
  counts: Record<string, number>;
  byEmoji: Record<string, string[]>;
  myHandle?: string;
  onToggle: (emoji: string) => void;
}) {
  const emojis = Object.keys(counts);
  if (!emojis.length) return null;

  return (
    <>
      {emojis.map((emoji) => {
        const mine = (byEmoji[emoji] || []).includes(myHandle || "");
        return (
          <button
            key={emoji}
            onClick={() => onToggle(emoji)}
            className={`reaction ${mine ? "mine" : ""}`}
            aria-label={`${emoji}${mine ? ", remove your reaction" : ", react"}`}
          >
            <span>{emoji}</span>
            {counts[emoji] > 1 && <span>{counts[emoji]}</span>}
          </button>
        );
      })}
    </>
  );
}

function toDayKey(m?: Message): string {
  if (!m?.created_at) return "";
  const ms = typeof m.created_at === "number" ? m.created_at * 1000 : Date.parse(m.created_at);
  if (!ms) return "";
  const d = new Date(ms);
  return `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`;
}

function daySeparator(prev?: Message, m?: Message): string | null {
  if (!m) return null;
  const cur = toDayKey(m);
  if (!cur) return null;
  if (!prev || toDayKey(prev) !== cur) {
    const ms = typeof m.created_at === "number" ? m.created_at * 1000 : Date.parse(m.created_at);
    const d = new Date(ms);
    const today = new Date();
    const yesterday = new Date(today);
    yesterday.setDate(today.getDate() - 1);
    if (d.toDateString() === today.toDateString()) return "Today";
    if (d.toDateString() === yesterday.toDateString()) return "Yesterday";
    return d.toLocaleDateString(undefined, { weekday: "long", month: "short", day: "numeric" });
  }
  return null;
}

function buildQuickStarts(roomAgents: Agent[], allAgents: Agent[], channelName?: string) {
  const pool = (roomAgents && roomAgents.length ? roomAgents : allAgents) || [];
  const items = pool.slice(0, 3).map((a) => ({
    id: `agent-${a.name}`,
    label: `@${a.name}`,
    hint: a.job || "Teammate",
    text: `@${a.name} `,
  }));
  if (channelName && channelName !== "general") {
    items.unshift({
      id: "summarize",
      label: "Catch me up",
      hint: "Summarize this room",
      text: "@swarm Please summarize what we know so far in this channel. ",
    });
  } else {
    items.unshift({
      id: "standup",
      label: "What is blocked?",
      hint: "Ask the room",
      text: "@swarm What's blocking us right now? ",
    });
  }
  return items.slice(0, 4);
}

function traceLabel(e: WorkEvent): string {
  const map: Record<string, string> = {
    work_queued: "Queued",
    work_started: "Started",
    agent_started: `${e.payload?.agent || "Teammate"} working`,
    tool_started: `${e.payload?.tool || e.step_id || "tool"} started`,
    tool_finished: `${e.payload?.tool || e.step_id || "tool"} finished`,
    approval_requested: "Approval requested — action needed",
    approval_resolved: `Approval ${e.payload?.decision || "resolved"}`,
    message_linked: "Reply posted",
    artifact_created: "Artifact created",
    work_completed: "Completed",
    work_failed: `Failed${e.payload?.error ? `: ${e.payload.error.slice(0, 120)}` : ""}`,
    work_cancelled: "Cancelled",
  };
  return map[e.type] || e.type;
}
