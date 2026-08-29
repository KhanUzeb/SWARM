import { useState } from "react";
import { Badge, Button, Input, Textarea, Avatar, ScrollArea, Tooltip } from "../ui.jsx";
import { statusLabel } from "../lib.js";

export function TopBar({ channel, agents, onToggleComputer, computerOpen, onTogglePanel, panelOpen, wsStatus, onOpenCommandPalette, onViewChange, currentView, approvals, onResolveApproval }) {
  const pendingApprovals = approvals.filter(a => a.status === "pending" && a.channel_id === channel?.id);
  const working = agents.filter(a => a.status === "working").length;

  return (
    <header id="topbar">
      <div className="topbar-left">
        <div className="channel-meta">
          <div className="channel-title-row">
            <span className="channel-prefix">{channel?.kind === "dm" || channel?.kind === "people" ? "@" : "#"}</span>
            <h1 className="channel-name">{channel?.name || "Select a channel"}</h1>
            {channel?.topic && <span className="channel-topic text-mono-xs text-subtle">{channel.topic}</span>}
          </div>
        </div>
      </div>

      <div className="topbar-center">
        <div className="view-tabs" role="tablist">
          {["dashboard", "workflows", "runs", "talk"].map(v => (
            <button
              key={v}
              role="tab"
              aria-selected={currentView === v}
              className={`view-tab ${currentView === v ? "active" : ""}`}
              onClick={() => onViewChange(v)}
            >
              {v === "dashboard" ? "Overview" : v.charAt(0).toUpperCase() + v.slice(1)}
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
            trigger={
              <Badge variant="warning" className="status-chip clickable">
                ⚠ {pendingApprovals.length} approval{pendingApprovals.length !== 1 ? "s" : ""}
              </Badge>
            }
            items={pendingApprovals.map(a => ({
              id: a.id,
              label: `${a.agent_name}: ${a.action}`,
              onClick: () => onResolveApproval(a.id, "approved"),
            }))}
          />
        )}

        <Tooltip content="Toggle computer panel (⌘C)">
          <Button variant={computerOpen ? "primary" : "ghost"} size="sm" onClick={onToggleComputer} className="topbar-btn">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><rect x="2" y="3" width="20" height="14" rx="2"/><line x1="8" y1="21" x2="16" y2="21"/><line x1="12" y1="17" x2="12" y2="21"/></svg>
          </Button>
        </Tooltip>

        <Tooltip content="Command palette (⌘K)">
          <Button variant="ghost" size="sm" onClick={onOpenCommandPalette} className="topbar-btn">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="11" cy="11" r="8"/><path d="M21 21l-4.3-4.3"/></svg>
          </Button>
        </Tooltip>
      </div>
    </header>
  );
}

function Dropdown({ trigger, items, align = "right" }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="dropdown" style={{ position: "relative" }}>
      <span onClick={() => setOpen(!open)} style={{ cursor: "pointer" }}>{trigger}</span>
      {open && (
        <div className="dropdown-menu" style={{ [align]: 0, position: "absolute", top: "calc(100% + 8px)" }}>
          {items.map(item => (
            <button key={item.id} className="dropdown-item" onClick={() => { item.onClick?.(); setOpen(false); }}>
              {item.label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
