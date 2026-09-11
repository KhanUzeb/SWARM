import { useEffect, useRef, useState } from "react";
import { Avatar, RichBody, Tooltip } from "../ui.jsx";
import { fmtTime, isAgentError, isResumable, shortModel, EMOJI } from "../lib.js";

export function MessageList({ messages, order, agents, allAgents, user, onReply, onReact, onUnreact, onDelete, onOpenThread, onRetry, replyCounts, reactions, channelId, onLoadMore, hasMore, loadingMore, typing, groupedWith, retryingId, streamingAgents, streamText = {}, workByMessage, eventsByWork, canModerate }) {
  const endRef = useRef(null);
  const logRef = useRef(null);
  const [pinned, setPinned] = useState(true);

  // Stick-to-bottom: only auto-scroll when the reader is already at the
  // bottom; otherwise show a "jump to latest" pill.
  useEffect(() => { setPinned(true); }, [channelId]);

  function handleScroll() {
    const el = logRef.current;
    if (!el) return;
    setPinned(el.scrollHeight - el.scrollTop - el.clientHeight < 80);
  }

  function jumpToLatest() {
    setPinned(true);
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }

  const streamChars = Object.values(streamText).reduce((n, t) => n + String(t || "").length, 0);

  useEffect(() => {
    if (endRef.current && pinned && !hasMore) {
      endRef.current.scrollIntoView({ behavior: "smooth", block: "end" });
    }
  }, [order.length, hasMore, pinned, streamChars]);

  const roots = order.map(id => messages[id]).filter(Boolean);
  const liveStreams = Object.entries(streamText).filter(([, text]) => text && text.length > 0);

  if (roots.length === 0 && !loadingMore) {
    return (
      <div id="log" className="log-empty">
        <div className="empty-state">
          <div className="empty-state-icon" aria-hidden>
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
              <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
            </svg>
          </div>
          <div className="empty-state-title">Start a conversation</div>
          <div className="empty-state-message">Message your team or @mention an agent to get started.</div>
        </div>
      </div>
    );
  }

  return (
    <div id="log" ref={logRef} onScroll={handleScroll}>
      {!pinned && roots.length > 0 && (
        <button type="button" className="jump-latest" onClick={jumpToLatest}>
          ↓ Latest
        </button>
      )}
      <div className="log-inner">
        {hasMore && (
          <button className="load-earlier" onClick={onLoadMore} disabled={loadingMore}>
            {loadingMore ? "Loading…" : "Load earlier messages"}
          </button>
        )}

        {roots.map((m, i) => {
          const prev = roots[i - 1];
          const grouped = groupedWith(prev, m);
          const label = m.author_kind === "agent"
            ? (allAgents.find(a => a.name === m.author)?.display_name || m.author)
            : m.author;
          const agent = m.author_kind === "agent" ? allAgents.find(a => a.name === m.author) : null;

          return (
            <MessageRow
              key={m.id}
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
              workEvents={(workByMessage?.[m.id] && eventsByWork?.[workByMessage[m.id].id]) || []}
              canDelete={m.author === user?.handle || !!canModerate}
            />
          );
        })}

        {liveStreams.map(([author, text]) => {
          const who = (allAgents || []).find(a => a.name === author);
          return (
            <div className="msg-row agent streaming-live" key={`stream-${author}`}>
              <div className="msg-bubble agent-bubble">
                <div className="msg-bubble-header">
                  <Avatar name={who?.display_name || author} kind="agent" size="sm" />
                  <span className="msg-author">{who?.display_name || author}</span>
                  <span className="streaming-badge" role="status" aria-label={`${author} is replying`}>
                    <span className="pulse-dot violet" aria-hidden /> streaming
                  </span>
                </div>
                <RichBody body={text} />
                <span className="stream-caret" aria-hidden />
              </div>
            </div>
          );
        })}

        {typing && !liveStreams.length && (
          <div className="typing-indicator" role="status" aria-live="polite">
            <span className="typing-dots"><span /><span /><span /></span>
            <span className="typing-text">{typing} is typing…</span>
          </div>
        )}

        <div ref={endRef} />
      </div>
    </div>
  );
}

function ReactPicker({ onPick, onClose }) {
  return (
    <div className="react-picker" role="menu" aria-label="Pick a reaction"
      onKeyDown={(e) => { if (e.key === "Escape") onClose(); }}>
      {EMOJI.map(emoji => (
        <button key={emoji} type="button" role="menuitem" className="react-pick"
          aria-label={`React with ${emoji}`}
          onClick={() => { onPick(emoji); onClose(); }}>
          {emoji}
        </button>
      ))}
    </div>
  );
}

function ReactionChips({ counts, byEmoji, myHandle, onToggle }) {
  const emojis = Object.keys(counts);
  if (!emojis.length) return null;
  return (
    <div className="msg-reactions inline">
      {emojis.map((emoji) => {
        const mine = (byEmoji[emoji] || []).includes(myHandle);
        return (
          <button key={emoji} className={`reaction-chip${mine ? " mine" : ""}`}
            onClick={() => onToggle(emoji)}
            title={(byEmoji[emoji] || []).join(", ")}
            aria-pressed={mine}>
            {emoji} {counts[emoji] > 1 && <span className="reaction-count">{counts[emoji]}</span>}
          </button>
        );
      })}
    </div>
  );
}

function MessageRow({ m, grouped, label, agent, reactions, replyCount, onReply, onReact, onUnreact, onDelete, onOpenThread, onRetry, retrying, myHandle, streaming, work, workEvents, canDelete }) {
  const [pickOpen, setPickOpen] = useState(false);
  const counts = {};
  const byEmoji = {};
  for (const r of reactions || []) {
    counts[r.emoji] = (counts[r.emoji] || 0) + 1;
    (byEmoji[r.emoji] = byEmoji[r.emoji] || []).push(r.author);
  }
  const toggleReact = (emoji) => {
    const mine = (byEmoji[emoji] || []).includes(myHandle);
    if (mine && onUnreact) onUnreact(m.id, emoji);
    else onReact(m.id, emoji);
  };
  const isMe = m.author === myHandle;
  const failedAgent = m.author_kind === "agent" && isAgentError(m.body);
  const stoppedAgent = m.author_kind === "agent" && !failedAgent && String(m.body || "").includes("[reply cut off");
  const resumable = m.author_kind === "agent" && isResumable(m.body);

  const kind = m.author_kind || "human";
  const avatarKind = kind === "system" ? "system" : agent ? "agent" : "human";

  if (kind === "system") {
    return (
      <div className="msg-row system">
        <div className="msg-system-line">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
            <circle cx="12" cy="12" r="10" /><path d="M12 6v6l4 2" />
          </svg>
          <RichBody body={m.body || ""} />
        </div>
      </div>
    );
  }

  if (isMe) {
    return (
      <div className={`msg-row human me ${grouped ? "grouped" : ""} ${m.streaming ? "streaming" : ""}`}>
        <div className="msg-bubble user-bubble">
          <RichBody body={m.body || ""} />
          {!m.streaming && m.id != null && (
            <ReactionChips counts={counts} byEmoji={byEmoji} myHandle={myHandle} onToggle={toggleReact} />
          )}
          {!m.streaming && m.id != null && (
            <div className="msg-actions own">
              <div className="react-wrap">
                <button className="msg-mini-action" onClick={() => setPickOpen(o => !o)}
                  aria-label="Add reaction" aria-expanded={pickOpen} aria-haspopup="menu">
                  ☺ React
                </button>
                {pickOpen && <ReactPicker onPick={(emoji) => onReact(m.id, emoji)} onClose={() => setPickOpen(false)} />}
              </div>
              {!m.parent_id && (
                <button className="msg-mini-action" onClick={() => onOpenThread(m.id)} aria-label="Open thread">
                  Thread{replyCount > 0 ? ` (${replyCount})` : ""}
                </button>
              )}
              {onDelete && (
                <button
                  className="msg-mini-action danger"
                  onClick={() => { if (window.confirm("Delete this message? Replies to it will also be removed.")) onDelete(m); }}
                  aria-label="Delete message"
                >
                  Delete
                </button>
              )}
            </div>
          )}
        </div>
        {!grouped && !m.parent_id && !m.streaming && replyCount > 0 && (
          <button className="thread-count" onClick={() => onOpenThread(m.id)}>
            {replyCount === 1 ? "1 reply" : `${replyCount} replies`}
          </button>
        )}
      </div>
    );
  }

  return (
    <div className={`msg-row ${kind} ${grouped ? "grouped" : ""} ${m.streaming || streaming ? "streaming" : ""}${failedAgent ? " failed" : ""}${stoppedAgent ? " stopped" : ""}`}>
      <div className={`msg-bubble agent-bubble${failedAgent ? " error-bubble" : ""}${stoppedAgent ? " stopped-bubble" : ""}`}>
        {!grouped && (
          <div className="msg-bubble-header">
            <Avatar name={label} kind={avatarKind} size="sm" />
            <span className="msg-author">{label}</span>
            {agent?.job && <span className="msg-role">{agent.job}</span>}
            {m.model && (
              <span className="msg-model" title={`Answered with ${m.model}`}>
                <span className="model-chip-dot" aria-hidden />
                {shortModel(m.model, 22)}
              </span>
            )}
            {(m.streaming || streaming) && (
              <span className="streaming-badge" role="status" aria-label={`${label} is replying`}>
                <span className="pulse-dot violet" aria-hidden /> streaming
              </span>
            )}
            {work && (
              <span className="msg-work-link" title={`Work: ${work.objective || work.id}`}>
                {work.status === "waiting_for_approval" ? "· needs approval" : `· ${work.status}`}
              </span>
            )}
            <span className="msg-time">{fmtTime(m.created_at)}</span>
          </div>
        )}
        {(m.streaming || streaming) && !m.body && (
          <div className="streaming-placeholder" aria-hidden>
            <span className="typing-dots"><span /><span /><span /></span>
          </div>
        )}
        <RichBody body={m.body || ""} />

        {workEvents.length > 0 && (
          <AgentTrace events={workEvents} />
        )}

        {resumable && onRetry && (
          <div className="msg-retry-row">
            <button type="button" className="msg-retry-btn" onClick={() => onRetry(m)} disabled={retrying}>
              {retrying ? "Retrying…" : stoppedAgent ? "Resume" : "Retry"}
            </button>
          </div>
        )}

        {kind !== "system" && !m.streaming && m.id != null && !resumable && (
          <div className="msg-actions">
            <Tooltip content="Reply">
              <button onClick={() => onReply(m.parent_id || m.id)} aria-label="Reply">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M9 14L4 9l5-5"/><path d="M20 20v-7a4 4 0 0 0-4-4H4"/></svg>
              </button>
            </Tooltip>
            <div className="react-wrap">
              <Tooltip content="React">
                <button onClick={() => setPickOpen(o => !o)} aria-label="React" aria-expanded={pickOpen} aria-haspopup="menu">
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="12" cy="12" r="10"/><path d="M8 14s1.5 2 4 2 4-2 4-2"/><line x1="9" y1="9" x2="9.01" y2="9"/><line x1="15" y1="9" x2="15.01" y2="9"/></svg>
                </button>
              </Tooltip>
              {pickOpen && <ReactPicker onPick={(emoji) => onReact(m.id, emoji)} onClose={() => setPickOpen(false)} />}
            </div>
            {onDelete && canDelete && (
              <Tooltip content="Delete">
                <button className="danger" onClick={() => { if (window.confirm("Delete this message? Replies to it will also be removed.")) onDelete(m); }} aria-label="Delete">
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M3 6h18M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>
                </button>
              </Tooltip>
            )}
          </div>
        )}

        {!m.streaming && m.id != null && (
          <ReactionChips counts={counts} byEmoji={byEmoji} myHandle={myHandle} onToggle={toggleReact} />
        )}
      </div>

      {!grouped && !m.parent_id && !m.streaming && replyCount > 0 && (
        <button className="thread-count" onClick={() => onOpenThread(m.id)}>
          {replyCount === 1 ? "1 reply" : `${replyCount} replies`}
        </button>
      )}
    </div>
  );
}

function AgentTrace({ events }) {
  const tools = [];
  const seen = new Set();
  for (const e of events) {
    const t = e.payload?.tool || (e.type.startsWith("tool_") ? e.step_id : null);
    if (t && !seen.has(t)) { seen.add(t); tools.push(t); }
  }
  if (tools.length === 0 && events.length === 0) return null;
  return (
    <details className="agent-trace">
      <summary className="agent-trace-summary">
        {tools.length > 0 ? (
          <span className="work-tools">
            {tools.slice(0, 3).map(t => <span key={t} className="tool-chip">{t}</span>)}
            {tools.length > 3 && <span className="tool-chip more">+{tools.length - 3}</span>}
          </span>
        ) : "Work trace"}
        <span className="agent-trace-count">{events.length} event{events.length === 1 ? "" : "s"}</span>
      </summary>
      <ol className="work-timeline inline">
        {events.map(e => (
          <li key={e.seq} className={`work-timeline-row type-${e.type}`}>
            <span className="work-timeline-seq">#{e.seq}</span>
            <span className="work-timeline-label">{traceLabel(e)}</span>
          </li>
        ))}
      </ol>
    </details>
  );
}

function traceLabel(e) {
  const map = {
    work_queued: "Queued", work_started: "Started", agent_started: `Agent ${e.payload?.agent || "working"}`,
    tool_started: `Tool ${e.payload?.tool || e.step_id || ""} started`,
    tool_finished: `Tool ${e.payload?.tool || e.step_id || ""} finished`,
    approval_requested: "Approval requested — action needed",
    approval_resolved: `Approval ${e.payload?.decision || "resolved"}`,
    message_linked: "Reply posted", artifact_created: "Artifact created",
    work_completed: "Completed", work_failed: `Failed${e.payload?.error ? `: ${e.payload.error.slice(0, 120)}` : ""}`,
    work_cancelled: "Cancelled",
  };
  return map[e.type] || e.type;
}
