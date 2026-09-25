import { useEffect, useState } from "react";
import { Avatar, Badge, Button, Card, Tooltip } from "./ui.jsx";

/** Wall-clock elapsed label for in-flight agent work (Rakazo-style). */
export function formatElapsed(startedAtMs, nowMs) {
  const totalTenths = Math.round(Math.max(0, nowMs - startedAtMs) / 100);
  const minutes = Math.floor(totalTenths / 600);
  const seconds = (totalTenths % 600) / 10;
  if (minutes === 0) return `${seconds.toFixed(1)}s`;
  return `${minutes}m ${seconds.toFixed(1)}s`;
}

function useElapsed(startedAtMs) {
  const [mountedAt] = useState(() => Date.now());
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 100);
    return () => clearInterval(timer);
  }, []);
  return formatElapsed(startedAtMs ?? mountedAt, now);
}

/** Shimmer sweep across a short status label while an agent is working. */
export function Shimmer({ children }) {
  return <span className="bui-shimmer">{children}</span>;
}

/** Compact loading row: pixel grid + shimmer label + elapsed timer. */
export function LoadingState({ label = "working", startedAt }) {
  const elapsed = useElapsed(startedAt);
  return (
    <span className="bui-loading" role="status">
      <span className="sr-only">{label}</span>
      <span className="bui-pixel-grid" aria-hidden>
        {Array.from({ length: 9 }).map((_, i) => (
          <span key={i} className="bui-pixel" style={{ animationDelay: `${(i % 3 + Math.floor(i / 3)) * 90}ms` }} />
        ))}
      </span>
      <span className="bui-loading-label"><Shimmer>{label}</Shimmer></span>
      <span className="bui-loading-elapsed">{elapsed}</span>
    </span>
  );
}

/* ───────────────────────────────────────────────────────────
 * beautifului.dev primitives — AI-native interface components
 * Sourced from beautifului.dev, adapted for swarm
 * ─────────────────────────────────────────────────────────── */

/**
 * 02 · Thinking — expandable traces (steps, reasoning, search, coding)
 * 03 · Streaming Text — streamed answer with inline sources & actions
 * 05 · Tool Chips — code edits and tool calls as compact chips
 * Combined into a single agent message surface.
 */
export function AgentMessage({ agent, body, streaming, tools = [], thinking, sources = [], onOpenThread, replyCount, onReply, onReact, onDelete }) {
  const [expanded, setExpanded] = useState(false);
  const trace = thinking || tools;
  const hasTrace = trace && trace.length > 0;

  return (
    <div className={`agent-msg ${streaming ? "streaming" : ""}`}>
      <div className="agent-msg-head">
        <Avatar name={agent?.display_name || agent?.name} kind="agent" size="sm" avatar={agent?.avatar} />
        <span className="agent-msg-name">{agent?.display_name || agent?.name}</span>
        <Badge variant="brand" className="agent-msg-job">{agent?.job || "agent"}</Badge>
        {streaming && <span className="agent-msg-streaming"><span className="pulse-dot" /> thinking…</span>}
      </div>

      {hasTrace && (
        <button className="thinking-toggle" onClick={() => setExpanded(!expanded)}>
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" style={{ transform: expanded ? "rotate(90deg)" : "none" }}><path d="M9 18l6-6-6-6"/></svg>
          <span className="thinking-label">{streaming ? "Working" : "Thought"} · {trace.length} step{trace.length !== 1 ? "s" : ""}</span>
        </button>
      )}

      {hasTrace && expanded && (
        <div className="thinking-trace">
          {trace.map((step, i) => (
            <ThinkingStep key={i} step={step} />
          ))}
        </div>
      )}

      {hasTrace && !expanded && (
        <div className="thinking-preview">
          {trace.slice(0, 3).map((step, i) => (
            <span key={i} className={`tool-chip tool-${step.kind || "tool"}`}>
              <span className="tool-chip-icon">{toolIcon(step.kind)}</span>
              {step.label || step.action || step.kind}
            </span>
          ))}
          {trace.length > 3 && <span className="tool-chip-more">+{trace.length - 3}</span>}
        </div>
      )}

      <div className="agent-msg-body">
        {body || (streaming && <span className="streaming-cursor" />)}
        {sources.length > 0 && (
          <div className="msg-sources">
            {sources.map((s, i) => (
              <a key={i} className="source-chip" href={s.url} target="_blank" rel="noopener">
                <span className="source-num">{i + 1}</span>
                <span className="source-title truncate">{s.title}</span>
              </a>
            ))}
          </div>
        )}
      </div>

      <div className="agent-msg-actions">
        <Tooltip content="Reply"><button onClick={() => onReply?.()}>↩</button></Tooltip>
        <Tooltip content="React"><button onClick={(e) => onReact?.(e.currentTarget)}>😊</button></Tooltip>
        {onDelete && <Tooltip content="Delete"><button className="danger" onClick={() => onDelete()}>🗑</button></Tooltip>}
        {replyCount > 0 && <button className="thread-link" onClick={() => onOpenThread?.()}>💬 {replyCount} replies</button>}
      </div>
    </div>
  );
}

function ThinkingStep({ step }) {
  const statusColor = { done: "success", running: "brand", error: "error", pending: "subtle" }[step.status] || "subtle";
  return (
    <div className={`thinking-step status-${statusColor}`}>
      <span className="thinking-step-icon">{toolIcon(step.kind)}</span>
      <div className="thinking-step-body">
        <span className="thinking-step-label">{step.label || step.action || step.kind}</span>
        {step.detail && <span className="thinking-step-detail text-mono-xs text-subtle">{step.detail}</span>}
      </div>
      <Badge variant={statusColor === "success" ? "success" : statusColor === "error" ? "error" : statusColor === "brand" ? "brand" : "subtle"} className="thinking-step-status">
        {step.status || "done"}
      </Badge>
    </div>
  );
}

function toolIcon(kind) {
  const icons = {
    search: "🔍", read: "📖", write: "✏️", edit: "✏️", shell: "⚡",
    code: "💻", browser: "🌐", fetch: "🔗", think: "🧠", tool: "🔧",
    approval: "✋", memory: "🧩", error: "❌", done: "✅",
  };
  return icons[kind] || icons.tool;
}

/**
 * 04 · Approval Card — human-in-the-loop question before acting
 */
export function ApprovalCard({ approval, onResolve }) {
  return (
    <div className="approval-card">
      <div className="approval-head">
        <Avatar name={approval.agent_name} kind="agent" size="sm" />
        <div>
          <span className="approval-agent">{approval.agent_name}</span>
          <span className="approval-sub text-mono-xs text-subtle">needs your approval</span>
        </div>
      </div>
      <p className="approval-action">{approval.action}</p>
      {approval.detail && <p className="approval-detail text-mono-xs">{approval.detail}</p>}
      <div className="approval-actions">
        <Button variant="primary" size="sm" onClick={() => onResolve(approval.id, "approved")}>Allow once</Button>
        <Button variant="ghost" size="sm" onClick={() => onResolve(approval.id, "denied")}>Deny</Button>
      </div>
    </div>
  );
}

/**
 * 06 · Task Rows — live agent task status
 */
export function TaskRow({ task }) {
  const statusColor = { running: "brand", completed: "success", failed: "error", pending: "subtle" }[task.status] || "subtle";
  return (
    <div className={`task-row status-${statusColor}`}>
      <span className="task-status-icon">
        {task.status === "running" && <span className="spinner spinner-sm" />}
        {task.status === "completed" && "✅"}
        {task.status === "failed" && "❌"}
        {task.status === "pending" && "○"}
      </span>
      <span className="task-name">{task.name}</span>
      {task.progress != null && (
        <div className="task-progress"><div className="task-progress-bar" style={{ width: `${task.progress * 100}%` }} /></div>
      )}
      {task.meta && <span className="task-meta text-mono-xs text-subtle">{task.meta}</span>}
    </div>
  );
}

/**
 * 09 · Recommendation Card — agent suggestion with confidence meter
 */
export function RecommendationCard({ title, detail, confidence, actions = [], onAccept, onDismiss }) {
  return (
    <div className="recommendation-card">
      <div className="rec-head">
        <span className="rec-icon">💡</span>
        <span className="rec-title">{title}</span>
        {confidence != null && (
          <span className={`rec-confidence conf-${confidence > 0.7 ? "high" : confidence > 0.4 ? "mid" : "low"}`}>
            {confidence > 0.7 ? "High" : confidence > 0.4 ? "Medium" : "Low"} confidence
          </span>
        )}
      </div>
      <p className="rec-detail">{detail}</p>
      {actions.length > 0 && (
        <div className="rec-actions">
          {actions.map((a, i) => (
            <button key={i} className="rec-action" onClick={a.onClick}>{a.label}</button>
          ))}
        </div>
      )}
      <div className="rec-footer">
        <Button variant="primary" size="sm" onClick={onAccept}>Accept</Button>
        <Button variant="ghost" size="sm" onClick={onDismiss}>Dismiss</Button>
      </div>
    </div>
  );
}

/**
 * 10 · Context Cards — retrieved knowledge chunks with sources
 */
export function ContextCard({ chunk }) {
  return (
    <div className="context-card">
      <div className="context-head">
        <Badge variant="subtle">{chunk.source_type || "source"}</Badge>
        <span className="context-source truncate">{chunk.source}</span>
        {chunk.chars && <span className="context-chars text-mono-xs text-subtle">{chunk.chars} chars</span>}
      </div>
      <p className="context-text">{chunk.text}</p>
    </div>
  );
}

/**
 * 01 · Loading State — pixel-grid loader with shimmer
 */
export function PixelLoader({ label = "Loading", startedAt, elapsed }) {
  const liveElapsed = useElapsed(startedAt);
  const shown = elapsed != null ? `${elapsed.toFixed(1)}s` : liveElapsed;
  return (
    <div className="pixel-loader">
      <div className="pixel-grid">
        {Array.from({ length: 16 }).map((_, i) => (
          <span key={i} className="pixel" style={{ animationDelay: `${i * 60}ms` }} />
        ))}
      </div>
      <span className="pixel-label"><Shimmer>{label}</Shimmer><span className="pixel-elapsed text-mono-xs"> · {shown}</span></span>
    </div>
  );
}
