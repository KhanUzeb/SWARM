import { useEffect, useState } from "react";
import { Avatar, Badge, Button, EmptyState, fmtTime } from "../ui.jsx";
import { WORK_ACTIVE, cancelWork, fetchWorkMessages, workStatusLabel, workStatusTone } from "../work/sessionStore.js";

const EVENT_LABEL = {
  work_queued: "Queued", work_started: "Started", agent_started: "Agent working",
  tool_started: "Tool started", tool_finished: "Tool finished",
  approval_requested: "Approval requested", approval_resolved: "Approval resolved",
  message_linked: "Reply posted", artifact_created: "Artifact created",
  work_completed: "Completed", work_failed: "Failed", work_cancelled: "Cancelled",
};

export function WorkRail({
  sessions, eventsByWork, agents, approvals, token, channelId,
  selectedWork, onSelectWork, onCancelWork, onResolveApproval, onOpenThread,
  collapsed, onToggle, connected, error, onRetry,
}) {
  const [filter, setFilter] = useState("active");
  const [source, setSource] = useState("all");
  const [busy, setBusy] = useState(null);

  if (collapsed) {
    return (
      <aside id="work-rail" className="collapsed" aria-label="Work rail collapsed">
        <button className="work-rail-expand" onClick={onToggle} aria-label="Open work rail">
          <span className="work-rail-expand-icon" aria-hidden>▸</span>
          <span className="work-rail-expand-label">Work</span>
          {attentionCount(sessions) > 0 && (
            <span className="work-rail-count">{attentionCount(sessions)}</span>
          )}
        </button>
      </aside>
    );
  }

  const visible = (sessions || []).filter(s => {
    if (source !== "all" && (s.source || "chat") !== source) return false;
    if (filter === "active") return WORK_ACTIVE.has(s.status);
    if (filter === "attention") return s.requires_action || s.status === "failed";
    if (filter === "mine") return channelId ? s.channel_id === channelId : true;
    return true;
  });

  return (
    <aside id="work-rail" aria-label="Active work">
      <div className="work-rail-head">
        <div className="work-rail-title">
          <h2>Work</h2>
          <span className={`work-rail-conn ${connected ? "on" : "off"}`} title={connected ? "Work feed live" : "Work feed reconnecting"}>
            <span className="conn-dot" aria-hidden />
            {connected ? "Live" : "Retrying"}
          </span>
        </div>
        <button className="panel-close" onClick={onToggle} aria-label="Collapse work rail">▸</button>
      </div>

      <div className="work-rail-filters" role="tablist" aria-label="Work filters">
        {[["active", "Active"], ["attention", "Needs you"], ["mine", "This room"], ["all", "All"]].map(([id, label]) => (
          <button key={id} role="tab" aria-selected={filter === id}
            className={`work-filter ${filter === id ? "active" : ""}`} onClick={() => setFilter(id)}>
            {label}
          </button>
        ))}
        <select className="work-source-filter" value={source} onChange={e => setSource(e.target.value)} aria-label="Filter by source">
          <option value="all">All sources</option>
          <option value="chat">Chat</option>
          <option value="run">Workflow</option>
          <option value="routine">Routine</option>
          <option value="handoff">Handoff</option>
        </select>
      </div>

      <div className="work-rail-body">
        {error && (
          <div className="work-error" role="alert">
            <span>Work feed failed{error ? `: ${String(error).slice(0, 120)}` : ""}</span>
            <button className="work-retry" onClick={onRetry}>Retry</button>
          </div>
        )}
        {visible.length === 0 && !error && (
          <EmptyState kind="work" title={filter === "attention" ? "Nothing needs you" : "No active work"}
            message={filter === "attention" ? "Approvals and failures will surface here." : "Mention an agent in chat or launch a run to see live progress."} />
        )}
        {!connected && !error && (sessions || []).length === 0 && (
          <div className="work-skeletons" aria-label="Loading work">
            {[0, 1, 2].map(i => (
              <div key={i} className="work-skeleton-card" aria-hidden>
                <span className="work-skeleton-line short" />
                <span className="work-skeleton-line" />
                <span className="work-skeleton-line half" />
              </div>
            ))}
          </div>
        )}
        {visible.map(s => (
          <WorkCard key={s.id} session={s} events={eventsByWork[s.id] || []}
            agents={agents} approvals={approvals} token={token}
            selected={selectedWork === s.id} onSelect={() => onSelectWork?.(s.id === selectedWork ? null : s.id)}
            busy={busy === s.id}
            onCancel={async () => {
              setBusy(s.id);
              try { await cancelWork(token, s.id); onCancelWork?.(); }
              catch { /* surfaced by next poll */ }
              finally { setBusy(null); }
            }}
            onResolveApproval={onResolveApproval} onOpenThread={onOpenThread} />
        ))}
      </div>
    </aside>
  );
}

function attentionCount(sessions) {
  return (sessions || []).filter(s => s.requires_action || s.status === "waiting_for_approval").length;
}

function WorkCard({ session, events, agents, approvals, selected, onSelect, onCancel, busy, onResolveApproval, onOpenThread }) {
  const [expanded, setExpanded] = useState(false);
  const tone = workStatusTone(session.status);
  const agentName = agentOf(session, events);
  const agent = (agents || []).find(a => a.name === agentName);
  const tools = events.filter(e => e.type === "tool_started" || e.type === "tool_finished");
  const linked = events.filter(e => e.type === "message_linked");
  const approvalEvents = events.filter(e => e.type === "approval_requested");
  const pendingApproval = (approvals || []).find(a =>
    a.status === "pending" && a.channel_id === session.channel_id && (!agentName || a.agent_name === agentName));

  return (
    <article className={`work-card tone-${tone}${selected ? " selected" : ""}`} aria-live={session.requires_action ? "assertive" : "polite"}>
      <button className="work-card-main" onClick={onSelect} aria-expanded={selected}>
        <span className="work-card-top">
          <Badge variant={tone}>{workStatusLabel(session.status)}</Badge>
          <span className="work-source">{sourceLabel(session.source)}</span>
        </span>
        <span className="work-objective">{session.objective || "Working…"}</span>
        {agentName && (
          <span className="work-agent">
            <Avatar name={agent?.display_name || agentName} kind="agent" size="sm" />
            <span className="work-agent-name">{agent?.display_name || agentName}</span>
            {agent?.job && <span className="work-agent-role">{agent.job}</span>}
          </span>
        )}
        {tools.length > 0 && (
          <span className="work-tools" aria-label={`${tools.length} tool activities`}>
            {uniqueTools(tools).slice(0, 3).map(t => (
              <span key={t} className="tool-chip">{t}</span>
            ))}
            {uniqueTools(tools).length > 3 && <span className="tool-chip more">+{uniqueTools(tools).length - 3}</span>}
          </span>
        )}
      </button>

      {session.requires_action && (
        <div className="work-attention" role="alert">
          <span className="attention-dot" aria-hidden />
          {session.status === "waiting_for_approval" ? "Waiting on your approval" : "Needs your attention"}
        </div>
      )}

      <div className="work-card-actions">
        <button className="work-timeline-toggle" onClick={() => setExpanded(e => !e)} aria-expanded={expanded}>
          {expanded ? "Hide trace" : `Trace (${events.length})`}
        </button>
        {linked.length > 0 && (
          <button className="work-link" onClick={() => onOpenThread?.(linked[linked.length - 1].payload?.message_id)}>
            View reply
          </button>
        )}
        {(pendingApproval || approvalEvents.length > 0) && session.status === "waiting_for_approval" && (
          <>
            <button className="work-approve" onClick={() => onResolveApproval?.(pendingApproval?.id, "approved", session)} disabled={!pendingApproval}>
              Approve
            </button>
            <button className="work-deny" onClick={() => onResolveApproval?.(pendingApproval?.id, "denied", session)} disabled={!pendingApproval}>
              Deny
            </button>
          </>
        )}
        {["queued", "running", "waiting_for_approval"].includes(session.status) && (
          <button className="work-cancel" onClick={onCancel} disabled={busy}>
            {busy ? "Cancelling…" : "Cancel"}
          </button>
        )}
      </div>

      {expanded && (
        <ol className="work-timeline">
          {events.length === 0 && <li className="work-timeline-empty">No events yet — replaying…</li>}
          {events.map(e => (
            <li key={e.seq} className={`work-timeline-row type-${e.type}`}>
              <span className="work-timeline-seq">#{e.seq}</span>
              <span className="work-timeline-main">
                <span className="work-timeline-label">{EVENT_LABEL[e.type] || e.type}</span>
                {eventDetail(e) && <span className="work-timeline-detail">{eventDetail(e)}</span>}
              </span>
              <span className="work-timeline-time">{e.created_at ? fmtTime(e.created_at) : ""}</span>
            </li>
          ))}
        </ol>
      )}
    </article>
  );
}

function agentOf(session, events) {
  for (const e of events) {
    if (e.payload?.agent) return e.payload.agent;
  }
  return session.active_step && !session.active_step.startsWith("step_") ? session.active_step : null;
}

function uniqueTools(events) {
  const names = [];
  for (const e of events) {
    const t = e.payload?.tool || e.step_id;
    if (t && !names.includes(t)) names.push(t);
  }
  return names;
}

function sourceLabel(source) {
  const map = { chat: "Chat", run: "Workflow", routine: "Routine", handoff: "Handoff" };
  return map[source] || source || "Work";
}

function eventDetail(e) {
  const p = e.payload || {};
  if (e.type === "tool_started" || e.type === "tool_finished") {
    return [p.tool || e.step_id, p.result ? `→ ${String(p.result).slice(0, 160)}` : ""].filter(Boolean).join(" ");
  }
  if (e.type === "agent_started") return p.role ? `${p.agent || ""} · ${p.role}` : (p.agent || "");
  if (e.type === "approval_requested") return p.label || "";
  if (e.type === "approval_resolved") return p.decision || "";
  if (e.type === "work_failed") return (p.error || "").slice(0, 200);
  if (e.type === "work_completed") return (p.summary || "").slice(0, 200);
  if (e.type === "work_started") return (p.objective || "").slice(0, 160);
  return "";
}

export function WorkDetail({ session, events, token, agents, approvals, busy, onCancel, onResolveApproval, onOpenThread, onClose }) {
  const [messages, setMessages] = useState(null);
  const [failed, setFailed] = useState(false);
  const tone = workStatusTone(session.status);
  const agentName = agentOf(session, events || []);
  const agent = (agents || []).find(a => a.name === agentName);
  const pendingApproval = (approvals || []).find(a =>
    a.status === "pending" && a.channel_id === session.channel_id && (!agentName || a.agent_name === agentName));

  useEffect(() => {
    let live = true;
    setMessages(null); setFailed(false);
    fetchWorkMessages(token, session.id)
      .then(data => { if (live) setMessages(data || []); })
      .catch(() => { if (live) setFailed(true); });
    return () => { live = false; };
  }, [token, session.id]);

  return (
    <div className="work-detail">
      <div className="work-detail-head">
        <Badge variant={tone}>{workStatusLabel(session.status)}</Badge>
        <span className="work-source">{sourceLabel(session.source)}</span>
        <span className="work-detail-time">{session.created_at ? fmtTime(session.created_at) : ""}</span>
      </div>
      <h2 className="work-detail-objective">{session.objective || "Working…"}</h2>
      {agentName && (
        <div className="work-agent">
          <Avatar name={agent?.display_name || agentName} kind="agent" size="sm" />
          <span className="work-agent-name">{agent?.display_name || agentName}</span>
          {agent?.job && <span className="work-agent-role">{agent.job}</span>}
        </div>
      )}
      {session.requires_action && (
        <div className="work-attention" role="alert">
          <span className="attention-dot" aria-hidden />
          {session.status === "waiting_for_approval" ? "Waiting on your approval" : "Needs your attention"}
        </div>
      )}
      <div className="work-detail-actions">
        {session.status === "waiting_for_approval" && (
          <>
            <button className="work-approve" onClick={() => onResolveApproval?.(pendingApproval?.id, "approved", session)} disabled={!pendingApproval}>Approve</button>
            <button className="work-deny" onClick={() => onResolveApproval?.(pendingApproval?.id, "denied", session)} disabled={!pendingApproval}>Deny</button>
          </>
        )}
        {WORK_ACTIVE.has(session.status) && (
          <button className="work-cancel" onClick={onCancel} disabled={busy}>{busy ? "Cancelling…" : "Cancel"}</button>
        )}
      </div>
      <h3 className="work-detail-section">Replies</h3>
      {messages === null && !failed && <p className="work-timeline-empty">Loading replies…</p>}
      {failed && <p className="work-timeline-empty">Could not load replies.</p>}
      {messages && messages.length === 0 && <p className="work-timeline-empty">No replies yet.</p>}
      {(messages || []).map(m => (
        <button key={m.id} className="work-detail-reply" onClick={() => { onOpenThread?.(m.parent_id || m.id); onClose?.(); }}>
          <span className="work-detail-reply-author">{m.author}</span>
          <span className="work-detail-reply-body">{(m.body || "").slice(0, 240)}</span>
          <span className="work-detail-reply-time">{m.created_at ? fmtTime(m.created_at) : ""}</span>
        </button>
      ))}
      <h3 className="work-detail-section">Timeline ({(events || []).length})</h3>
      <ol className="work-timeline">
        {(events || []).length === 0 && <li className="work-timeline-empty">No events yet — replaying…</li>}
        {(events || []).map(e => (
          <li key={e.seq} className={`work-timeline-row type-${e.type}`}>
            <span className="work-timeline-seq">#{e.seq}</span>
            <span className="work-timeline-main">
              <span className="work-timeline-label">{EVENT_LABEL[e.type] || e.type}</span>
              {eventDetail(e) && <span className="work-timeline-detail">{eventDetail(e)}</span>}
            </span>
            <span className="work-timeline-time">{e.created_at ? fmtTime(e.created_at) : ""}</span>
          </li>
        ))}
      </ol>
    </div>
  );
}

export function WorkRailSheet({ open, onClose, children }) {
  if (!open) return null;
  return (
    <div className="work-sheet-backdrop" onClick={onClose}>
      <div className="work-sheet" role="dialog" aria-label="Work details" onClick={e => e.stopPropagation()}>
        <button className="panel-close" onClick={onClose} aria-label="Close work details">×</button>
        {children}
      </div>
    </div>
  );
}

export function RetryButton({ onClick, busy, label = "Retry" }) {
  return (
    <Button variant="ghost" size="sm" onClick={onClick} disabled={busy}>
      {busy ? "Retrying…" : label}
    </Button>
  );
}
