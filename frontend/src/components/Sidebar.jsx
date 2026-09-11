import { useState } from "react";
import { Avatar, Badge, Tooltip, ScrollArea, Dropdown } from "../ui.jsx";
import { initials, statusLabel } from "../lib.js";

const PRIMARY_NAV = [
  { id: "home", label: "Home", icon: "⌂" },
  { id: "work", label: "Work", icon: "◉" },
  { id: "chat", label: "Chat", icon: "✎" },
  { id: "agents", label: "Agents", icon: "⚙" },
  { id: "knowledge", label: "Knowledge", icon: "▤" },
];

export function Sidebar({ user, channels, agents, teams, onSelectChannel, activeChannel, wsStatus, meRole, onNewChannel, onNewDM, onNewGroup, onNewTeam, onNewAgent, onLogout, onOpenSettings, onDeleteChannel, currentView, onViewChange, workAttentionCount, pinned = [] }) {
  const [search, setSearch] = useState("");
  const [collapsed, setCollapsed] = useState(false);

  const q = search.trim().toLowerCase();
  const matchChannel = (c) => !q || (c.name || "").toLowerCase().includes(q);
  const matchAgent = (a) => !q || (a.name || "").toLowerCase().includes(q)
    || (a.display_name || "").toLowerCase().includes(q);

  const rooms = channels.filter(c => (c.kind === "room" || c.kind === undefined) && matchChannel(c));
  const groups = channels.filter(c => c.kind === "group" && matchChannel(c));
  const dms = channels.filter(c => c.kind === "dm" && matchChannel(c));
  const peopleDms = channels.filter(c => c.kind === "people" && matchChannel(c));
  const matchedAgents = (agents || []).filter(matchAgent);
  const matchedTeams = (teams || []).filter(t => !q || (t.name || "").toLowerCase().includes(q));

  return (
    <aside id="sidebar" className={collapsed ? "collapsed" : ""} aria-label="Primary">
      <div className="sidebar-header">
        <div className="brand">
          <span className="brand-mark">swarm</span>
        </div>
        <button className="btn btn-ghost btn-icon" onClick={() => setCollapsed(c => !c)} aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"} aria-expanded={!collapsed}>⇥</button>
        <button className="btn btn-ghost btn-icon tooltip-trigger" data-tooltip="New message" onClick={onNewDM} aria-label="New message">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d="M12 5v14M5 12h14"/></svg>
        </button>
      </div>

      <nav className="primary-nav" aria-label="Primary destinations">
        {PRIMARY_NAV.map(item => (
          <button
            key={item.id}
            className={`primary-link ${currentView === item.id || (item.id === "chat" && currentView === "talk") ? "active" : ""}`}
            onClick={() => onViewChange?.(item.id === "chat" ? "talk" : item.id)}
            title={collapsed ? item.label : undefined}
          >
            <span aria-hidden>{item.icon}</span>
            {!collapsed && <span>{item.label}</span>}
            {!collapsed && item.id === "work" && (workAttentionCount || 0) > 0 && (
              <span className="nav-count" role="status">{workAttentionCount} need you</span>
            )}
          </button>
        ))}
      </nav>

      {!collapsed && (
      <div className="sidebar-search">
        <input
          type="search"
          placeholder="Search channels, agents…"
          value={search}
          onChange={e => setSearch(e.target.value)}
          className="input input-search"
          aria-label="Filter channels and agents"
        />
      </div>
      )}

      <ScrollArea className="sidebar-scroll">
        <nav className="sidebar-nav" aria-label="Channels and agents">
          {rooms.length > 0 && (
            <Section
              title="Channels"
              action={onNewChannel}
              actionLabel="New channel"
            >
              {rooms.map(c => (
                <ChannelItem
                  key={c.id}
                  channel={c}
                  active={activeChannel === c.id}
                  onClick={() => onSelectChannel(c.id)}
                  onDelete={onDeleteChannel ? () => onDeleteChannel(c.id) : null}
                />
              ))}
            </Section>
          )}

          {groups.length > 0 && (
            <Section title="Groups" action={onNewGroup} actionLabel="New group">
              {groups.map(c => (
                <ChannelItem key={c.id} channel={c} active={activeChannel === c.id} onClick={() => onSelectChannel(c.id)} onDelete={onDeleteChannel ? () => onDeleteChannel(c.id) : null} />
              ))}
            </Section>
          )}

          {peopleDms.length > 0 && (
            <Section title="Direct Messages" action={onNewDM} actionLabel="New DM">
              {peopleDms.map(c => (
                <ChannelItem key={c.id} channel={c} active={activeChannel === c.id} onClick={() => onSelectChannel(c.id)} dm />
              ))}
            </Section>
          )}

          {matchedAgents.length > 0 && (
            <Section title="Agents" action={onNewAgent} actionLabel="New agent">
              {matchedAgents.map(a => (
                <AgentItem key={a.name} agent={a} active={activeChannel === a.dm_channel_id} onClick={() => onSelectChannel(a.dm_channel_id)} />
              ))}
            </Section>
          )}

          {matchedTeams.length > 0 && (
            <Section title="Teams" action={onNewTeam} actionLabel="New team">
              {matchedTeams.map(t => (
                <ChannelItem key={t.id} channel={{ id: t.id, name: t.name, kind: "team" }} active={activeChannel === t.id} onClick={() => onSelectChannel(t.id)} />
              ))}
            </Section>
          )}

          {q && rooms.length === 0 && groups.length === 0 && peopleDms.length === 0
            && matchedAgents.length === 0 && matchedTeams.length === 0 && (
            <p className="sidebar-empty" role="status">No matches for “{search.trim()}”.</p>
          )}
        </nav>
      </ScrollArea>

      <div id="me">
        <Avatar name={user?.handle || "U"} kind="human" size="sm" />
        <div className="me-meta">
          <span className="handle">{user?.handle}</span>
          <span className={`sub ${wsStatus === "connected" ? "online" : wsStatus === "connecting" ? "connecting" : "offline"}`}>
            {wsStatus === "connected" ? "Online" : wsStatus === "connecting" ? "Connecting" : "Offline"}
          </span>
        </div>
        {meRole === "admin" && <Badge variant="warning" className="role-pill">Admin</Badge>}
        <Dropdown
          trigger={<button className="btn btn-ghost btn-icon btn-sm" aria-label="Account menu"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="12" cy="5" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="12" cy="19" r="1"/></svg></button>}
          items={[
            { label: "Settings", icon: "⚙", onClick: onOpenSettings },
            "divider",
            { label: "Sign out", icon: "↩", danger: true, onClick: onLogout },
          ]}
        />
      </div>
    </aside>
  );
}

function Section({ title, children, action, actionLabel }) {
  return (
    <div className="sidebar-section">
      <div className="section-header">
        <span className="section-label">{title}</span>
        {action && (
          <button className="section-action" onClick={action} title={actionLabel} aria-label={actionLabel}>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d="M12 5v14M5 12h14"/></svg>
          </button>
        )}
      </div>
      <ul className="channel-list">{children}</ul>
    </div>
  );
}

function ChannelItem({ channel, active, onClick, dm, onDelete }) {
  return (
    <li className="channel-item">
      <button className={`channel-link ${active ? "active" : ""}`} onClick={onClick}>
        <span className={`channel-dot ${dm ? "dm" : ""}`} />
        <span className="channel-name truncate">{channel.name}</span>
      </button>
      {onDelete && !dm && (
        <button
          className="channel-delete"
          onClick={(e) => { e.stopPropagation(); onDelete(); }}
          title={`Delete #${channel.name}`}
          aria-label={`Delete channel ${channel.name}`}
        >
          ×
        </button>
      )}
    </li>
  );
}

function AgentItem({ agent, active, onClick }) {
  const statusColor = { idle: "offline", working: "online", needs_approval: "warning" }[agent.status] || "offline";
  return (
    <li className="channel-item">
      <button className={`channel-link agent-link ${active ? "active" : ""}`} onClick={onClick}>
        <span className={`status-dot ${statusColor}`} />
        <span className="channel-name truncate">{agent.display_name || agent.name}</span>
        {agent.status === "needs_approval" && <Badge variant="warning" className="ml-auto">!</Badge>}
      </button>
    </li>
  );
}
