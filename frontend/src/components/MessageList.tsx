import * as React from "react";
import { Avatar } from "@/components/ui/avatar";
import { Tooltip } from "@/components/ui/tooltip";
import { LoadingState, Shimmer } from "../beautifului.jsx";
import { RichBody } from "../ui.jsx";
import { fmtTime, isAgentError, isResumable, shortModel, EMOJI } from "@/lib";
import {
  MessageSquare,
  Smile,
  Trash2,
  RotateCcw,
  Sparkles,
  ChevronDown,
  ChevronUp,
  Terminal,
  Activity,
  ArrowDown,
  CornerDownRight,
  ShieldAlert,
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
    0
  );

  React.useEffect(() => {
    if (endRef.current && pinned && !hasMore) {
      endRef.current.scrollIntoView({ behavior: "smooth", block: "end" });
    }
  }, [order.length, hasMore, pinned, streamChars]);

  const roots = order.map((id) => messages[id]).filter(Boolean);
  const liveStreams = Object.entries(streamText).filter(
    ([, text]) => text && text.length > 0
  );
  const quickStarts = buildQuickStarts(agents, allAgents, channelName);

  if (roots.length === 0 && !loadingMore && liveStreams.length === 0 && !typing) {
    return (
      <div
        id="log"
        className="flex-1 overflow-y-auto flex items-center justify-center p-6 text-center select-none"
      >
        <div className="max-w-md mx-auto space-y-4">
          <div className="flex items-center justify-center w-12 h-12 rounded-2xl bg-zinc-900 border border-zinc-800 text-violet-400 mx-auto shadow-sm">
            <MessageSquare className="w-6 h-6" />
          </div>
          <div className="space-y-1">
            <h2 className="text-base font-semibold text-zinc-100">
              {channelName ? `#${channelName}` : "This room"}
            </h2>
            <p className="text-xs text-zinc-400 leading-relaxed">
              @mention a teammate or type a prompt. Teammates collaborate and keep a visible audit trail.
            </p>
          </div>

          {onQuickStart && quickStarts.length > 0 && (
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 pt-2 text-left">
              {quickStarts.map((item) => (
                <button
                  key={item.id}
                  type="button"
                  onClick={() => onQuickStart(item.text)}
                  className="flex flex-col gap-0.5 p-2.5 rounded-xl border border-zinc-800 bg-zinc-900/60 hover:border-zinc-700 hover:bg-zinc-850 transition-all text-xs group"
                >
                  <span className="font-semibold text-zinc-200 group-hover:text-violet-300 transition-colors">
                    {item.label}
                  </span>
                  {item.hint && (
                    <span className="text-[11px] text-zinc-500">{item.hint}</span>
                  )}
                </button>
              ))}
            </div>
          )}
        </div>
      </div>
    );
  }

  return (
    <div
      id="log"
      ref={logRef}
      onScroll={handleScroll}
      className="flex-1 overflow-y-auto relative scroll-smooth [scrollbar-width:thin] [scrollbar-color:rgba(255,255,255,0.1)_transparent]"
    >
      {!pinned && roots.length > 0 && (
        <button
          type="button"
          onClick={jumpToLatest}
          className="fixed bottom-24 right-8 z-30 inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full bg-zinc-900 text-zinc-200 border border-zinc-750 shadow-xl text-xs font-medium hover:bg-zinc-850 hover:text-white transition-all animate-in fade-in-0 duration-150"
        >
          <ArrowDown className="w-3.5 h-3.5" />
          <span>Latest</span>
        </button>
      )}

      <div className="max-w-4xl mx-auto w-full px-4 sm:px-6 py-6 space-y-4">
        {hasMore && (
          <div className="flex justify-center pb-2">
            <button
              onClick={onLoadMore}
              disabled={loadingMore}
              className="text-xs text-zinc-500 hover:text-zinc-300 py-1 px-3 rounded-md hover:bg-zinc-900 transition-colors"
            >
              {loadingMore ? "Loading earlier messages…" : "Load earlier messages"}
            </button>
          </div>
        )}

        {roots.map((m, i) => {
          const prev = roots[i - 1];
          const dayLabel = daySeparator(prev, m);
          const grouped = groupedWith(prev, m);
          const label =
            m.author_kind === "agent"
              ? allAgents.find((a) => a.name === m.author)?.display_name ||
                m.author
              : m.author;
          const agent =
            m.author_kind === "agent"
              ? allAgents.find((a) => a.name === m.author)
              : undefined;

          return (
            <React.Fragment key={m.id}>
              {dayLabel && (
                <div className="flex items-center justify-center my-6 select-none" role="separator">
                  <div className="h-px bg-zinc-850 flex-1" />
                  <span className="px-3 text-[11px] font-medium text-zinc-500 uppercase tracking-wider">
                    {dayLabel}
                  </span>
                  <div className="h-px bg-zinc-850 flex-1" />
                </div>
              )}
              <MessageRow
                m={m}
                grouped={grouped}
                label={label}
                agent={agent}
                reactions={reactions[m.id] || []}
                replyCount={replyCounts[m.id] || 0}
                onReply={onReply}
                onReact={onReact}
                onUnreact={onUnreact}
                onDelete={onDelete}
                onOpenThread={onOpenThread}
                onRetry={onRetry}
                retrying={retryingId === m.id}
                myHandle={user?.handle}
                streaming={!!(streamingAgents && streamingAgents[m.author])}
                work={workByMessage?.[m.id]}
                workEvents={
                  (workByMessage?.[m.id] && eventsByWork?.[workByMessage[m.id].id]) || []
                }
                canDelete={m.author === user?.handle || !!canModerate}
              />
            </React.Fragment>
          );
        })}

        {/* Live streaming bubbles */}
        {liveStreams.map(([author, text]) => {
          const who = allAgents.find((a) => a.name === author);
          return (
            <div className="flex gap-3 text-xs" key={`stream-${author}`}>
              <Avatar
                name={who?.display_name || author}
                kind="agent"
                size="md"
                avatar={who?.avatar}
                className="shrink-0 mt-0.5"
              />
              <div className="flex-1 space-y-1.5 min-w-0">
                <div className="flex items-center gap-2">
                  <span className="font-semibold text-zinc-100">
                    {who?.display_name || author}
                  </span>
                  <span className="inline-flex items-center gap-1 text-[10px] text-violet-400 font-mono">
                    <span className="w-1.5 h-1.5 rounded-full bg-violet-400 animate-pulse" />
                    <Shimmer>streaming</Shimmer>
                  </span>
                </div>
                <div className="rounded-xl border border-zinc-800 bg-zinc-900/60 p-3.5 text-zinc-200 leading-relaxed">
                  <RichBody body={text} />
                  <span className="inline-block w-1.5 h-3.5 ml-0.5 bg-violet-400 animate-pulse align-middle" />
                </div>
              </div>
            </div>
          );
        })}

        {typing && !liveStreams.length && (
          <div className="py-2 text-xs text-zinc-500">
            <LoadingState label={`${typing} is typing…`} startedAt={Date.now()} />
          </div>
        )}

        <div ref={endRef} />
      </div>
    </div>
  );
}

function MessageRow({
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

  const kind = m.author_kind || "human";
  const avatarKind = kind === "system" ? "system" : agent ? "agent" : "human";

  if (kind === "system") {
    return (
      <div className="flex items-center gap-2 py-1 px-3 text-[11px] text-zinc-500 font-mono">
        <Activity className="w-3 h-3 text-zinc-600 shrink-0" />
        <RichBody body={m.body || ""} />
      </div>
    );
  }

  // Human user message (me)
  if (isMe) {
    return (
      <div className={`flex flex-col items-end group ${grouped ? "mt-1" : "mt-3"}`}>
        <div className="relative max-w-2xl">
          <div className="rounded-2xl rounded-tr-sm bg-gradient-to-br from-violet-600 to-indigo-600 px-4 py-2.5 text-xs text-white shadow-sm leading-relaxed">
            <RichBody body={m.body || ""} />
          </div>

          {/* Quick hover action bar */}
          <div className="absolute right-0 -bottom-6 hidden group-hover:flex group-focus-within:flex items-center gap-1 bg-zinc-900/90 border border-zinc-800 rounded-md px-1 py-0.5 shadow-md backdrop-blur-sm z-10 text-[11px]">
            <button
              onClick={() => setPickOpen((o) => !o)}
              className="p-1 hover:text-white text-zinc-400 rounded hover:bg-zinc-800"
              title="React"
            >
              <Smile className="w-3 h-3" />
            </button>
            {!m.parent_id && onOpenThread && (
              <button
                onClick={() => onOpenThread(m.id)}
                className="p-1 hover:text-white text-zinc-400 rounded hover:bg-zinc-800"
                title="Thread reply"
              >
                <MessageSquare className="w-3 h-3" />
              </button>
            )}
            {onDelete && canDelete && (
              <button
                onClick={() => {
                  if (window.confirm("Delete this message?")) onDelete(m);
                }}
                className="p-1 hover:text-rose-400 text-zinc-400 rounded hover:bg-zinc-800"
                title="Delete"
              >
                <Trash2 className="w-3 h-3" />
              </button>
            )}
            {pickOpen && (
              <ReactPicker
                onPick={(emoji) => onReact(m.id, emoji)}
                onClose={() => setPickOpen(false)}
              />
            )}
          </div>
        </div>

        {/* Reactions & Thread count */}
        <ReactionChips
          counts={counts}
          byEmoji={byEmoji}
          myHandle={myHandle}
          onToggle={toggleReact}
        />
        {!grouped && !m.parent_id && replyCount > 0 && onOpenThread && (
          <button
            onClick={() => onOpenThread(m.id)}
            className="mt-1 text-[11px] text-violet-400 hover:underline inline-flex items-center gap-1"
          >
            <CornerDownRight className="w-3 h-3" />
            <span>
              {replyCount} {replyCount === 1 ? "reply" : "replies"}
            </span>
          </button>
        )}
      </div>
    );
  }

  // Teammate / Other user message
  return (
    <div className={`flex gap-3 text-xs group ${grouped ? "mt-1.5" : "mt-4"}`}>
      {!grouped ? (
        <Avatar
          name={label}
          kind={avatarKind}
          size="md"
          avatar={agent?.avatar}
          className="shrink-0 mt-0.5"
        />
      ) : (
        <div className="w-8 shrink-0" />
      )}

      <div className="flex-1 space-y-1 min-w-0">
        {!grouped && (
          <div className="flex items-center gap-2 flex-wrap">
            <span className="font-semibold text-zinc-100">{label}</span>
            {agent?.job && (
              <span className="text-[11px] text-zinc-400">{agent.job}</span>
            )}
            {m.model && (
              <span
                className="inline-flex items-center gap-1 text-[10px] font-mono text-zinc-400 px-1.5 py-0.2 rounded bg-zinc-900 border border-zinc-800"
                title={`Answered with ${m.model}`}
              >
                <Sparkles className="w-2.5 h-2.5 text-violet-400" />
                {shortModel(m.model, 20)}
              </span>
            )}
            {work && (
              <span
                className={`text-[10px] font-mono px-1.5 py-0.2 rounded ${
                  work.status === "waiting_for_approval"
                    ? "bg-amber-500/10 text-amber-300 border border-amber-500/20"
                    : "bg-zinc-900 text-zinc-400"
                }`}
              >
                {work.status === "waiting_for_approval"
                  ? "needs approval"
                  : work.status}
              </span>
            )}
            <span className="text-[10px] text-zinc-400 ml-auto font-mono">
              {fmtTime(m.created_at)}
            </span>
          </div>
        )}

        {/* Message bubble */}
        <div
          className={`relative rounded-xl border p-3.5 leading-relaxed text-zinc-200 transition-all ${
            failedAgent
              ? "border-rose-500/30 bg-rose-500/5 text-rose-200"
              : stoppedAgent
              ? "border-amber-500/30 bg-amber-500/5"
              : "border-zinc-800/80 bg-zinc-900/60"
          }`}
        >
          <RichBody body={m.body || ""} />

          {/* Collapsible OpenCode-style Tool / Work Trace */}
          {workEvents.length > 0 && <AgentTrace events={workEvents} />}

          {/* Retry / Resume Button */}
          {resumable && onRetry && (
            <div className="mt-2.5 pt-2 border-t border-zinc-800/80 flex items-center gap-2">
              <button
                type="button"
                onClick={() => onRetry(m)}
                disabled={retrying}
                className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md text-xs font-medium bg-zinc-800 text-zinc-200 hover:bg-zinc-700 hover:text-white border border-zinc-700 transition-colors"
              >
                <RotateCcw className="w-3 h-3" />
                <span>{retrying ? "Retrying…" : stoppedAgent ? "Resume" : "Retry"}</span>
              </button>
            </div>
          )}

          {/* Action trigger buttons on hover */}
          <div className="absolute right-2 top-2 hidden group-hover:flex group-focus-within:flex items-center gap-1 bg-zinc-900/90 border border-zinc-800 rounded-md px-1 py-0.5 shadow-md backdrop-blur-sm z-10 text-[11px]">
            {onReply && (
              <button
                onClick={() => onReply(m.parent_id || m.id)}
                className="p-1 hover:text-white text-zinc-400 rounded hover:bg-zinc-800"
                title="Reply"
              >
                <MessageSquare className="w-3 h-3" />
              </button>
            )}
            <div className="relative">
              <button
                onClick={() => setPickOpen((o) => !o)}
                className="p-1 hover:text-white text-zinc-400 rounded hover:bg-zinc-800"
                title="React"
              >
                <Smile className="w-3 h-3" />
              </button>
              {pickOpen && (
                <ReactPicker
                  onPick={(emoji) => onReact(m.id, emoji)}
                  onClose={() => setPickOpen(false)}
                />
              )}
            </div>
            {onDelete && canDelete && (
              <button
                onClick={() => {
                  if (window.confirm("Delete this message?")) onDelete(m);
                }}
                className="p-1 hover:text-rose-400 text-zinc-400 rounded hover:bg-zinc-800"
                title="Delete"
              >
                <Trash2 className="w-3 h-3" />
              </button>
            )}
          </div>
        </div>

        {/* Reactions */}
        <ReactionChips
          counts={counts}
          byEmoji={byEmoji}
          myHandle={myHandle}
          onToggle={toggleReact}
        />

        {/* Reply thread indicator */}
        {!grouped && !m.parent_id && replyCount > 0 && onOpenThread && (
          <button
            onClick={() => onOpenThread(m.id)}
            className="mt-1 text-[11px] text-violet-400 hover:underline inline-flex items-center gap-1"
          >
            <CornerDownRight className="w-3 h-3" />
            <span>
              {replyCount} {replyCount === 1 ? "reply" : "replies"}
            </span>
          </button>
        )}
      </div>
    </div>
  );
}

function AgentTrace({ events }: { events: WorkEvent[] }) {
  const [open, setOpen] = React.useState(false);
  const tools: string[] = [];
  const seen = new Set<string>();

  for (const e of events) {
    const t =
      e.payload?.tool || (e.type.startsWith("tool_") ? e.step_id : null);
    if (t && !seen.has(t)) {
      seen.add(t);
      tools.push(t);
    }
  }

  if (tools.length === 0 && events.length === 0) return null;

  return (
    <div className="mt-2.5 rounded-lg border border-zinc-800 bg-zinc-950/60 overflow-hidden text-xs">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="w-full flex items-center justify-between px-3 py-1.5 text-zinc-400 hover:text-zinc-200 hover:bg-zinc-900/60 transition-colors"
      >
        <div className="flex items-center gap-2">
          <Terminal className="w-3.5 h-3.5 text-violet-400 shrink-0" />
          <span className="font-mono text-[11px]">
            {tools.length > 0 ? tools.slice(0, 3).join(", ") : "Execution trace"}
            {tools.length > 3 && ` +${tools.length - 3}`}
          </span>
        </div>
        <div className="flex items-center gap-1 text-[11px] text-zinc-400">
          <span>
            {events.length} step{events.length === 1 ? "" : "s"}
          </span>
          {open ? (
            <ChevronUp className="w-3 h-3" />
          ) : (
            <ChevronDown className="w-3 h-3" />
          )}
        </div>
      </button>

      {open && (
        <ol className="divide-y divide-zinc-850 border-t border-zinc-850 px-3 py-2 space-y-1 font-mono text-[11px]">
          {events.map((e) => (
            <li key={e.seq} className="flex items-center gap-2 text-zinc-400 pt-1">
              <span className="text-zinc-600">#{e.seq}</span>
              <span className="truncate">{traceLabel(e)}</span>
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}

function ReactPicker({
  onPick,
  onClose,
}: {
  onPick: (emoji: string) => void;
  onClose: () => void;
}) {
  return (
    <div
      className="emoji-picker absolute bottom-full right-0 mb-1 z-50 grid w-52 grid-cols-6 gap-1.5 p-2 rounded-xl border border-zinc-800 bg-zinc-900/95 shadow-xl backdrop-blur-md animate-in fade-in-0 zoom-in-95"
      role="menu"
    >
      {EMOJI.map((emoji: string) => (
        <button
          key={emoji}
          type="button"
          onClick={() => {
            onPick(emoji);
            onClose();
          }}
          className="emoji flex h-8 w-8 items-center justify-center rounded-lg p-0 text-base transition-transform hover:bg-zinc-800 hover:scale-110 active:scale-125"
        >
          {emoji}
        </button>
      ))}
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
    <div className="flex flex-wrap gap-1.5 mt-1.5">
      {emojis.map((emoji) => {
        const mine = (byEmoji[emoji] || []).includes(myHandle || "");
        return (
          <button
            key={emoji}
            onClick={() => onToggle(emoji)}
            className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs transition-colors border ${
              mine
                ? "bg-violet-500/15 text-violet-300 border-violet-500/30 font-medium"
                : "bg-zinc-900 text-zinc-400 border-zinc-800 hover:border-zinc-700"
            }`}
          >
            <span className="emoji whitespace-nowrap">{emoji}</span>
            {counts[emoji] > 1 && (
              <span className="text-[10px] text-zinc-400">{counts[emoji]}</span>
            )}
          </button>
        );
      })}
    </div>
  );
}

function toDayKey(m?: Message): string {
  if (!m?.created_at) return "";
  const ms =
    typeof m.created_at === "number"
      ? m.created_at * 1000
      : Date.parse(m.created_at);
  if (!ms) return "";
  const d = new Date(ms);
  return `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`;
}

function daySeparator(prev?: Message, m?: Message): string | null {
  if (!m) return null;
  const cur = toDayKey(m);
  if (!cur) return null;
  if (!prev || toDayKey(prev) !== cur) {
    const ms =
      typeof m.created_at === "number"
        ? m.created_at * 1000
        : Date.parse(m.created_at);
    const d = new Date(ms);
    const today = new Date();
    const yesterday = new Date(today);
    yesterday.setDate(today.getDate() - 1);
    if (d.toDateString() === today.toDateString()) return "Today";
    if (d.toDateString() === yesterday.toDateString()) return "Yesterday";
    return d.toLocaleDateString(undefined, {
      weekday: "long",
      month: "short",
      day: "numeric",
    });
  }
  return null;
}

function buildQuickStarts(
  roomAgents: Agent[],
  allAgents: Agent[],
  channelName?: string
) {
  const pool = (roomAgents && roomAgents.length ? roomAgents : allAgents) || [];
  const picks = pool.slice(0, 3);
  const items = picks.map((a) => ({
    id: `agent-${a.name}`,
    label: `@${a.name}`,
    hint: a.job || "Agent",
    text: `@${a.name} `,
  }));
  if (channelName && channelName !== "general") {
    items.unshift({
      id: "summarize",
      label: "Summarize thread",
      hint: "Catch up",
      text: "@swarm Please summarize what we know so far in this channel. ",
    });
  } else {
    items.unshift({
      id: "standup",
      label: "What’s blocking us?",
      hint: "@swarm",
      text: "@swarm What's blocking us right now? ",
    });
  }
  return items.slice(0, 4);
}

function traceLabel(e: WorkEvent): string {
  const map: Record<string, string> = {
    work_queued: "Queued",
    work_started: "Started",
    agent_started: `Agent ${e.payload?.agent || "working"}`,
    tool_started: `Tool ${e.payload?.tool || e.step_id || ""} started`,
    tool_finished: `Tool ${e.payload?.tool || e.step_id || ""} finished`,
    approval_requested: "Approval requested — action needed",
    approval_resolved: `Approval ${e.payload?.decision || "resolved"}`,
    message_linked: "Reply posted",
    artifact_created: "Artifact created",
    work_completed: "Completed",
    work_failed: `Failed${
      e.payload?.error ? `: ${e.payload.error.slice(0, 120)}` : ""
    }`,
    work_cancelled: "Cancelled",
  };
  return map[e.type] || e.type;
}
