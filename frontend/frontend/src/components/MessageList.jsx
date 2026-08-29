import { useEffect, useRef } from "react";
import { Avatar, Badge, RichBody, Tooltip } from "../ui.jsx";
import { fmtTime, initials } from "../lib.js";

export function MessageList({ messages, order, agents, allAgents, user, onReply, onReact, onDelete, onOpenThread, replyCounts, reactions, channelId, onLoadMore, hasMore, loadingMore, typing, groupedWith }) {
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
          <div className="empty-state-icon">💬</div>
          <div className="empty-state-title">No messages yet</div>
          <div className="empty-state-message">Start the conversation — @mention an agent to bring them in.</div>
        </div>
      </div>
    );
  }

  return (
    <div id="log" ref={logRef}>
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
  );
}

function MessageRow({ m, grouped, label, agent, reactions, replyCount, onReply, onReact, onDelete, onOpenThread, myHandle }) {
  const counts = {};
  for (const r of reactions || []) counts[r.emoji] = (counts[r.emoji] || 0) + 1;
  const isMe = m.author === myHandle;

  const kind = m.author_kind || "human";
  const avatarKind = kind === "system" ? "system" : agent ? "agent" : "human";

  return (
    <div className={`msg-row ${kind} ${grouped ? "grouped" : ""} ${m.streaming ? "streaming" : ""}`}>
      <div className="msg-main">
        {!grouped && (
          <Avatar name={label} kind={avatarKind} size="md" />
        )}
        {grouped && <div className="msg-gutter" />}

        <div className="msg-content">
          {!grouped && (
            <div className="msg-meta">
              <span className="msg-author">{label}</span>
              {agent && <Badge variant="brand" className="msg-badge">{agent.job || "agent"}</Badge>}
              <span className="msg-time">{fmtTime(m.created_at)}</span>
            </div>
          )}
          <RichBody body={m.body || ""} />
        </div>
      </div>

      {kind !== "system" && !m.streaming && m.id != null && (
        <div className="msg-actions">
          <Tooltip content="Reply"><button onClick={() => onReply(m.parent_id || m.id)} aria-label="Reply">↩</button></Tooltip>
          <Tooltip content="React"><button onClick={(ev) => onReact(m.id, ev.currentTarget)} aria-label="React">😊</button></Tooltip>
          {onDelete && isMe && <Tooltip content="Delete"><button className="danger" onClick={() => onDelete(m)} aria-label="Delete">🗑</button></Tooltip>}
        </div>
      )}

      {!m.streaming && m.id != null && counts && Object.keys(counts).length > 0 && (
        <div className="msg-reactions">
          {Object.entries(counts).map(([emoji, count]) => (
            <button key={emoji} className="reaction-chip" onClick={() => onReact(m.id, emoji)}>
              {emoji} <span className="reaction-count">{count}</span>
            </button>
          ))}
        </div>
      )}

      {!grouped && !m.parent_id && !m.streaming && replyCount > 0 && (
        <button className="thread-count" onClick={() => onOpenThread(m.id)}>
          💬 {replyCount === 1 ? "1 reply" : `${replyCount} replies`}
        </button>
      )}
    </div>
  );
}
