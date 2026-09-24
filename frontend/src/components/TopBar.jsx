import { useRef } from "react";
import { Badge, Button, Dropdown, Tooltip } from "../ui.jsx";

const VIEW_ALIASES = { dashboard: "home", home: "home", workflows: "work", runs: "work", work: "work", talk: "chat", chat: "chat", agents: "agents", knowledge: "knowledge" };
const VIEW_TARGETS = { home: "dashboard", work: "work", chat: "talk", agents: "agents", knowledge: "knowledge" };
const VIEW_LABELS = { home: "Home", work: "Work", chat: "Chat", agents: "Agents", knowledge: "Knowledge" };

export function TopBar({ channel, agents, onToggleComputer, computerOpen, onOpenCommandPalette, onViewChange, currentView, approvals, onResolveApproval, wsStatus, workActive, workAttention, workConnected, onToggleWorkRail, workRailOpen, participants, onOpenSidebar }) {
  const normalizedView = VIEW_ALIASES[currentView] || currentView;
  const go = (v) => onViewChange(VIEW_TARGETS[v] || v);
  const pendingApprovals = approvals.filter(a => a.status === "pending" && a.channel_id === channel?.id);
  const working = agents.filter(a => a.status === "working").length;
  const attention = (workAttention || []).length;
  const tabRefs = useRef([]);
  const viewKeys = ["home", "work", "chat", "agents", "knowledge"];

  // WAI-APG tabs pattern: arrows move between views, Home/End jump.
  function onTabsKey(e) {
    const idx = tabRefs.current.indexOf(document.activeElement);
    if (idx === -1) return;
    let next = null;
    if (e.key === "ArrowRight") next = (idx + 1) % viewKeys.length;
    else if (e.key === "ArrowLeft") next = (idx - 1 + viewKeys.length) % viewKeys.length;
    else if (e.key === "Home") next = 0;
    else if (e.key === "End") next = viewKeys.length - 1;
    if (next !== null) {
      e.preventDefault();
      tabRefs.current[next]?.focus();
      go(viewKeys[next]);
    }
  }

  return (
    <header id="topbar" aria-label="Conversation header">
      <div className="topbar-left">
        {onOpenSidebar && (
          <button type="button" className="sidebar-menu-btn" onClick={onOpenSidebar} aria-label="Open navigation">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d="M4 6h16M4 12h16M4 18h16" /></svg>
          </button>
        )}
        <div className="channel-meta">
          <div className="channel-title-row">
            <span className="channel-avatar" aria-hidden>
              {(channel?.name || "?").charAt(0).toUpperCase()}
            </span>
            <h1 className="channel-name">{channel?.name || "Select a channel"}</h1>
            <span className="mobile-view-label">{VIEW_LABELS[normalizedView] || "Chat"}</span>
            {channel?.topic && <span className="channel-topic">{channel.topic}</span>}
          </div>
          <div className="channel-presence-row">
            {wsStatus !== "connected" && (
              <span className={`conn-state conn-${wsStatus || "offline"}`} role="status" title={`Connection: ${wsStatus || "offline"}`}>
                <span className="conn-dot" aria-hidden />
                {wsStatus === "connecting" ? "Connecting…" : "Offline"}
              </span>
            )}
            {(participants || []).length > 0 && (
              <span className="presence-avatars" role="img" aria-label={`${participants.length} participant${participants.length === 1 ? "" : "s"}: ${participants.slice(0, 4).join(", ")}`}>
                {participants.slice(0, 4).map(p => (
                  <span key={p} className="presence-avatar" aria-hidden>{p.charAt(0).toUpperCase()}</span>
                ))}
                {participants.length > 4 && <span className="presence-more" aria-hidden>+{participants.length - 4}</span>}
              </span>
            )}
            {(workActive || []).length > 0 && (
              <span className="work-session-status" title={`${workActive.length} active work session${workActive.length === 1 ? "" : "s"}`}>
                <span className="pulse-dot violet" aria-hidden />
                {workActive.length} active{!workConnected ? " · replaying" : ""}
              </span>
            )}
          </div>
        </div>
      </div>

      <div className="topbar-center">
        <div className="view-tabs" role="tablist" aria-label="Primary views" onKeyDown={onTabsKey}>
          {[["home", "Home"], ["work", "Work"], ["chat", "Chat"], ["agents", "Agents"], ["knowledge", "Knowledge"]].map(([v, label], i) => (
            <button
              key={v}
              ref={el => { tabRefs.current[i] = el; }}
              role="tab"
              tabIndex={normalizedView === v ? 0 : -1}
              aria-selected={normalizedView === v}
              className={`view-tab ${normalizedView === v ? "active" : ""}`}
              onClick={() => go(v)}
            >
              {label}
            </button>
          ))}
        </div>
      </div>

      <div className="topbar-right">
        {working > 0 && (
          <Badge variant="success" className="status-chip">
            <span className="pulse-dot" /> {working} working
          </Badge>
        )}

        {pendingApprovals.length > 0 && (
          <Dropdown
            label={`${pendingApprovals.length} pending approvals`}
            trigger={
              <Badge variant="warning" className="status-chip clickable">
                {pendingApprovals.length} approval{pendingApprovals.length !== 1 ? "s" : ""}
              </Badge>
            }
            items={pendingApprovals.map(a => ({
              id: a.id,
              label: `${a.agent_name}: ${a.action}`,
              onClick: () => onResolveApproval(a.id, "approved"),
            }))}
          />
        )}

        {attention > 0 && (
          <button className="status-chip attention-chip" onClick={onToggleWorkRail} title="Open work needing attention">
            {attention} need{attention === 1 ? "s" : ""} you
          </button>
        )}

        <Tooltip content={workRailOpen ? "Hide work rail" : "Show work rail"}>
          <Button variant={workRailOpen ? "primary" : "ghost"} size="sm" onClick={onToggleWorkRail} className="topbar-btn" aria-pressed={workRailOpen} aria-label="Toggle work rail">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden><rect x="3" y="3" width="18" height="18" rx="2"/><line x1="15" y1="3" x2="15" y2="21"/></svg>
          </Button>
        </Tooltip>

        <Tooltip content="Toggle computer panel">
          <Button variant={computerOpen ? "primary" : "ghost"} size="sm" onClick={onToggleComputer} className="topbar-btn" aria-pressed={computerOpen} aria-label="Toggle computer panel">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden><rect x="2" y="3" width="20" height="14" rx="2"/><line x1="8" y1="21" x2="16" y2="21"/><line x1="12" y1="17" x2="12" y2="21"/></svg>
          </Button>
        </Tooltip>

        <Tooltip content="Search">
          <Button variant="ghost" size="sm" onClick={onOpenCommandPalette} className="topbar-btn" aria-label="Open command palette">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden><circle cx="11" cy="11" r="8"/><path d="M21 21l-4.3-4.3"/></svg>
          </Button>
        </Tooltip>
      </div>
    </header>
  );
}


