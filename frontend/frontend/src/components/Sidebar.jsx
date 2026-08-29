import { useState } from "react";
import { Avatar, Badge, Tooltip, ScrollArea, Dropdown } from "../ui.jsx";
import { initials, statusLabel } from "../lib.js";

export function Sidebar({ user, channels, agents, teams, onSelectChannel, activeChannel, wsStatus, meRole, onNewChannel, onNewDM, onNewGroup, onNewTeam, onNewAgent, onLogout, onOpenSettings }) {
  const [search, setSearch] = useState("");
  const [section, setSection] = useState("channels");

  const rooms = channels.filter(c => c.kind === "room" || c.kind === undefined);
  const groups = channels.filter(c => c.kind === "group");
  const dms = channels.filter(c => c.kind === "dm");
  const peopleDms = channels.filter(c => c.kind === "people");

  return (
    <aside id="sidebar">
      <div className="sidebar-header">
        <div className="brand">
          <span className="brand-mark">swarm</span>
          <span className="brand-sub">AI NESTED TEAMWORK</span>
        </div>
        <button className="btn btn-ghost btn-icon tooltip-trigger" data-tooltip="New message" onClick={onNewDM}>
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d="M12 5v14M5 12h14"/></svg>
        </button>
      </div>

      <div className="sidebar-search">
        <input
          type="search"
          placeholder="Search channels, people, messages"
          value={search}
          onChange={e => setSearch(e.target.value)}
          className="input input-search"
          aria-label="Search"
        />
      </div>

      <ScrollArea className="sidebar-scroll">
        <nav className="sidebar-nav">
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
              />
            ))}
          </Section>

          {groups.length > 0 && (
            <Section title="Groups" action={onNewGroup} actionLabel="New group">
              {groups.map(c => (
                <ChannelItem key={c.id} channel={c} active={activeChannel === c.id} onClick={() => onSelectChannel(c.id)} />
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

          <Section title="Agents" action={onNewAgent} actionLabel="New agent">
            {agents.map(a => (
              <AgentItem key={a.name} agent={a} active={activeChannel === a.dm_channel_id} onClick={() => onSelectChannel(a.dm_channel_id)} />
            ))}
          </Section>

          {teams.length > 0 && (
            <Section title="Teams" action={onNewTeam} actionLabel="New team">
              {teams.map(t => (
                <ChannelItem key={t.id} channel={{ id: t.id, name: t.name, kind: "team" }} active={activeChannel === t.id} onClick={() => onSelectChannel(t.id)} />
              ))}
            </Section>
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

function ChannelItem({ channel, active, onClick, dm }) {
  return (
    <li className="channel-item">
      <button className={`channel-link ${active ? "active" : ""}`} onClick={onClick}>
        <span className="channel-icon">{dm ? "@" : "#"}</span>
        <span className="channel-name truncate">{channel.name}</span>
      </button>
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
