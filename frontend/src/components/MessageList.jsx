import { useEffect, useRef } from "react";
import { Avatar, RichBody, Tooltip } from "../ui.jsx";
import { fmtTime, isAgentError } from "../lib.js";

export function MessageList({ messages, order, agents, allAgents, user, onReply, onReact, onDelete, onOpenThread, onRetry, replyCounts, reactions, channelId, onLoadMore, hasMore, loadingMore, typing, groupedWith, retryingId }) {
  const endRef = useRef(null);
  const logRef = useRef(null);

  useEffect(() => {
    if (endRef.current && !hasMore) {
      endRef.current.scrollIntoView({ behavior: "smooth", block: "end" });
    }
  }, [order.length, hasMore]);

  const roots = order.map(id => messages[id]).filter(Boolean);

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
    <div id="log" ref={logRef}>
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
              onDelete={onDelete}
              onOpenThread={onOpenThread}
              onRetry={onRetry}
              retrying={retryingId === m.id}
              myHandle={user?.handle}
            />
          );
        })}

        {typing && (
          <div className="typing-indicator">
            <span className="typing-dots"><span /><span /><span /></span>
            <span className="typing-text">{typing} is typing…</span>
          </div>
        )}

        <div ref={endRef} />
      </div>
    </div>
  );
}

function MessageRow({ m, grouped, label, agent, reactions, replyCount, onReply, onReact, onDelete, onOpenThread, onRetry, retrying, myHandle }) {
  const counts = {};
  for (const r of reactions || []) counts[r.emoji] = (counts[r.emoji] || 0) + 1;
  const isMe = m.author === myHandle;
  const failedAgent = m.author_kind === "agent" && isAgentError(m.body);

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
          {!m.streaming && m.id != null && Object.keys(counts).length > 0 && (
            <div className="msg-reactions inline">
              {Object.entries(counts).map(([emoji, count]) => (
                <button key={emoji} className="reaction-chip" onClick={() => onReact(m.id, emoji)}>
                  {emoji} {count > 1 && <span className="reaction-count">{count}</span>}
                </button>
              ))}
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
    <div className={`msg-row ${kind} ${grouped ? "grouped" : ""} ${m.streaming ? "streaming" : ""} ${failedAgent ? "failed" : ""}`}>
      <div className={`msg-bubble agent-bubble${failedAgent ? " error-bubble" : ""}`}>
        {!grouped && (
          <div className="msg-bubble-header">
            <Avatar name={label} kind={avatarKind} size="sm" />
            <span className="msg-author">{label}</span>
            {agent?.job && <span className="msg-role">{agent.job}</span>}
            <span className="msg-time">{fmtTime(m.created_at)}</span>
          </div>
        )}
        <RichBody body={m.body || ""} />

        {failedAgent && onRetry && (
          <div className="msg-retry-row">
            <button type="button" className="msg-retry-btn" onClick={() => onRetry(m)} disabled={retrying}>
              {retrying ? "Retrying…" : "Retry"}
            </button>
          </div>
        )}

        {kind !== "system" && !m.streaming && m.id != null && !failedAgent && (
          <div className="msg-actions">
            <Tooltip content="Reply">
              <button onClick={() => onReply(m.parent_id || m.id)} aria-label="Reply">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M9 14L4 9l5-5"/><path d="M20 20v-7a4 4 0 0 0-4-4H4"/></svg>
              </button>
            </Tooltip>
            <Tooltip content="React">
              <button onClick={() => onReact(m.id, "👍")} aria-label="React">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="12" cy="12" r="10"/><path d="M8 14s1.5 2 4 2 4-2 4-2"/><line x1="9" y1="9" x2="9.01" y2="9"/><line x1="15" y1="9" x2="15.01" y2="9"/></svg>
              </button>
            </Tooltip>
            {onDelete && isMe && (
              <Tooltip content="Delete">
                <button className="danger" onClick={() => onDelete(m)} aria-label="Delete">
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M3 6h18M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>
                </button>
              </Tooltip>
            )}
          </div>
        )}

        {!m.streaming && m.id != null && Object.keys(counts).length > 0 && (
          <div className="msg-reactions inline">
            {Object.entries(counts).map(([emoji, count]) => (
              <button key={emoji} className="reaction-chip" onClick={() => onReact(m.id, emoji)}>
                {emoji} {count > 1 && <span className="reaction-count">{count}</span>}
              </button>
            ))}
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
