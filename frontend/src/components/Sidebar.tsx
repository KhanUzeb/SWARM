import * as React from "react";
import { Avatar } from "@/components/ui/avatar";
import { Dropdown } from "@/components/ui/dropdown-menu";
import { Tooltip } from "@/components/ui/tooltip";
import { Flap, Lamp } from "@/components/Flap";
import {
  Activity,
  Bot,
  BookOpen,
  Home,
  LogOut,
  MessageSquare,
  PanelLeft,
  PanelLeftClose,
  Plus,
  Search,
  Settings,
  SquarePen,
  Users,
  X,
} from "lucide-react";

interface Channel {
  id: string;
  name: string;
  kind?: string;
}

interface Agent {
  name: string;
  display_name?: string;
  avatar?: string;
  status?: string;
  job?: string;
  dm_channel_id?: string;
}

interface Team {
  id: string;
  name: string;
}

interface SidebarProps {
  user?: { handle?: string };
  channels: Channel[];
  agents: Agent[];
  teams?: Team[];
  onSelectChannel: (id?: string) => void;
  activeChannel?: string;
  wsStatus?: string;
  meRole?: string;
  onNewChannel?: () => void;
  onNewDM?: () => void;
  onNewGroup?: () => void;
  onNewTeam?: () => void;
  onNewAgent?: () => void;
  onLogout?: () => void;
  onOpenSettings?: () => void;
  onDeleteChannel?: (id: string) => void;
  currentView?: string;
  onViewChange?: (view: string) => void;
  workAttentionCount?: number;
  open?: boolean;
  onClose?: () => void;
}

const PRIMARY_NAV = [
  { id: "home", label: "Board", icon: Home },
  { id: "work", label: "Work", icon: Activity },
  { id: "chat", label: "Talk", icon: MessageSquare },
  { id: "agents", label: "Roster", icon: Bot },
  { id: "knowledge", label: "Knowledge", icon: BookOpen },
];

/**
 * A teammate's state, as a flap face. The board's whole promise is that
 * you can read this column without reading a word of it, so the state
 * gets the machine register and the name gets ordinary sentence case.
 */
function flapFor(status?: string): { value: string; tone: "go" | "amber" | "hold" | "unlit" } {
  switch (status) {
    case "working":
    case "running":
      return { value: "Work", tone: "amber" };
    case "needs_approval":
    case "waiting_for_approval":
      return { value: "Hold", tone: "hold" };
    case "idle":
    case "online":
      return { value: "Ready", tone: "go" };
    default:
      return { value: "Off", tone: "unlit" };
  }
}

function lampFor(status?: string): "go" | "amber" | "hold" | "off" {
  switch (status) {
    case "working":
    case "running":
      return "amber";
    case "needs_approval":
    case "waiting_for_approval":
      return "hold";
    case "idle":
    case "online":
      return "go";
    default:
      return "off";
  }
}

export function Sidebar({
  user,
  channels = [],
  agents = [],
  teams = [],
  onSelectChannel,
  activeChannel,
  wsStatus,
  meRole,
  onNewChannel,
  onNewDM,
  onNewGroup,
  onNewTeam,
  onNewAgent,
  onLogout,
  onOpenSettings,
  onDeleteChannel,
  currentView,
  onViewChange,
  workAttentionCount,
  open = false,
}: SidebarProps) {
  const [search, setSearch] = React.useState("");
  const [collapsed, setCollapsed] = React.useState(false);

  const q = search.trim().toLowerCase();
  const matchChannel = (c: Channel) => !q || (c.name || "").toLowerCase().includes(q);
  const matchAgent = (a: Agent) =>
    !q ||
    (a.name || "").toLowerCase().includes(q) ||
    (a.display_name || "").toLowerCase().includes(q);

  const rooms = channels.filter(
    (c) => (c.kind === "room" || c.kind === undefined) && matchChannel(c),
  );
  const groups = channels.filter((c) => c.kind === "group" && matchChannel(c));
  const peopleDms = channels.filter((c) => c.kind === "people" && matchChannel(c));
  const matchedAgents = (agents || []).filter(matchAgent);
  const matchedTeams = (teams || []).filter(
    (t) => !q || (t.name || "").toLowerCase().includes(q),
  );

  return (
    <aside
      id="sidebar"
      className={open ? "open" : ""}
      style={collapsed && !open ? { width: 56 } : undefined}
      aria-label="Roster"
    >
      <div className="sidebar-header">
        {!collapsed && (
          <div className="brand">
            <span className="brand-mark">Swarm</span>
            <span className="brand-sub">ops desk</span>
          </div>
        )}

        <div style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: 2 }}>
          <Tooltip content={collapsed ? "Expand roster" : "Collapse roster"}>
            <button
              type="button"
              className="btn btn-ghost btn-icon btn-sm"
              onClick={() => setCollapsed((c) => !c)}
              aria-label={collapsed ? "Expand roster" : "Collapse roster"}
            >
              {collapsed ? <PanelLeft size={14} /> : <PanelLeftClose size={14} />}
            </button>
          </Tooltip>

          {!collapsed && onNewDM && (
            <Tooltip content="New message">
              <button
                type="button"
                className="btn btn-ghost btn-icon btn-sm"
                onClick={onNewDM}
                aria-label="New message"
              >
                <SquarePen size={14} />
              </button>
            </Tooltip>
          )}
        </div>
      </div>

      <nav className="sidebar-section" style={{ padding: "8px 8px 4px" }} aria-label="Destinations">
        {PRIMARY_NAV.map((item) => {
          const Icon = item.icon;
          const isActive =
            currentView === item.id || (item.id === "chat" && currentView === "talk");
          return (
            <button
              key={item.id}
              onClick={() => onViewChange?.(item.id === "chat" ? "talk" : item.id)}
              title={collapsed ? item.label : undefined}
              className={`channel-link ${isActive ? "active" : ""}`}
              style={collapsed ? { justifyContent: "center", padding: 0 } : undefined}
              aria-current={isActive ? "page" : undefined}
            >
              <Icon size={14} className="shrink-0" />
              {!collapsed && <span className="channel-name">{item.label}</span>}
              {!collapsed && item.id === "work" && (workAttentionCount || 0) > 0 && (
                <span className="flap flap-hold" style={{ marginLeft: "auto" }}>
                  <span>{workAttentionCount}</span>
                </span>
              )}
            </button>
          );
        })}
      </nav>

      {!collapsed && (
        <div className="sidebar-search">
          <div style={{ position: "relative", display: "flex", alignItems: "center" }}>
            <Search
              size={13}
              aria-hidden="true"
              style={{ position: "absolute", left: 8, color: "var(--ink-3)", pointerEvents: "none" }}
            />
            <input
              type="search"
              placeholder="Filter the board"
              aria-label="Filter rooms and teammates"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="input"
              style={{ height: 28, paddingLeft: 26, fontSize: "var(--t-sm)" }}
            />
          </div>
        </div>
      )}

      <div className="sidebar-scroll scroll-y">
        {matchedAgents.length > 0 && (
          <Section
            title="Teammates"
            count={matchedAgents.length}
            action={onNewAgent}
            actionLabel="New teammate"
          >
            {matchedAgents.map((a) => (
              <AgentItem
                key={a.name}
                agent={a}
                collapsed={collapsed}
                active={activeChannel === a.dm_channel_id}
                onClick={() => onSelectChannel(a.dm_channel_id)}
              />
            ))}
          </Section>
        )}

        {rooms.length > 0 && (
          <Section
            title="Rooms"
            count={rooms.length}
            action={onNewChannel}
            actionLabel="New room"
          >
            {rooms.map((c) => (
              <ChannelItem
                key={c.id}
                channel={c}
                collapsed={collapsed}
                active={activeChannel === c.id}
                onClick={() => onSelectChannel(c.id)}
                onDelete={onDeleteChannel ? () => onDeleteChannel(c.id) : undefined}
              />
            ))}
          </Section>
        )}

        {groups.length > 0 && (
          <Section
            title="Groups"
            count={groups.length}
            action={onNewGroup}
            actionLabel="New group"
          >
            {groups.map((c) => (
              <ChannelItem
                key={c.id}
                channel={c}
                collapsed={collapsed}
                active={activeChannel === c.id}
                onClick={() => onSelectChannel(c.id)}
                onDelete={onDeleteChannel ? () => onDeleteChannel(c.id) : undefined}
              />
            ))}
          </Section>
        )}

        {peopleDms.length > 0 && (
          <Section
            title="People"
            count={peopleDms.length}
            action={onNewDM}
            actionLabel="New message"
          >
            {peopleDms.map((c) => (
              <ChannelItem
                key={c.id}
                channel={c}
                collapsed={collapsed}
                dm
                active={activeChannel === c.id}
                onClick={() => onSelectChannel(c.id)}
              />
            ))}
          </Section>
        )}

        {matchedTeams.length > 0 && (
          <Section
            title="Teams"
            count={matchedTeams.length}
            action={onNewTeam}
            actionLabel="New team"
          >
            {matchedTeams.map((t) => (
              <ChannelItem
                key={t.id}
                channel={{ id: t.id, name: t.name, kind: "team" }}
                collapsed={collapsed}
                active={activeChannel === t.id}
                onClick={() => onSelectChannel(t.id)}
              />
            ))}
          </Section>
        )}

        {q &&
          rooms.length === 0 &&
          groups.length === 0 &&
          peopleDms.length === 0 &&
          matchedAgents.length === 0 &&
          matchedTeams.length === 0 && (
            <p className="sidebar-empty">Nothing on the board matches “{search.trim()}”.</p>
          )}
      </div>

      <div id="me">
        <Avatar name={user?.handle || "User"} kind="human" size="sm" />
        <div className="me-meta">
          <span className="handle">{user?.handle}</span>
          <span className={`sub ${wsStatus === "connected" ? "online" : wsStatus === "connecting" ? "connecting" : ""}`}>
            {wsStatus === "connected"
              ? "Live"
              : wsStatus === "connecting"
                ? "Linking"
                : "No link"}
          </span>
        </div>
        {meRole === "admin" && <span className="role-pill">Admin</span>}
        <Dropdown
          align="right"
          label="Account"
          trigger={
            <button type="button" className="btn btn-ghost btn-icon btn-sm" aria-label="Account options">
              <Settings size={14} />
            </button>
          }
          items={[
            { label: "Settings", icon: <Settings size={14} />, onClick: onOpenSettings },
            "divider",
            { label: "Sign out", icon: <LogOut size={14} />, danger: true, onClick: onLogout },
          ]}
        />
      </div>
    </aside>
  );
}

function Section({
  title,
  count,
  children,
  action,
  actionLabel,
}: {
  title: string;
  count?: number;
  children: React.ReactNode;
  action?: () => void;
  actionLabel?: string;
}) {
  return (
    <div className="sidebar-section">
      <div className="section-header">
        <span className="section-label">
          {title}
          {typeof count === "number" && <span style={{ opacity: 0.55 }}> {count}</span>}
        </span>
        {action && (
          <button
            onClick={action}
            className="section-action"
            title={actionLabel}
            aria-label={actionLabel}
          >
            <Plus size={13} />
          </button>
        )}
      </div>
      <ul className="channel-list">{children}</ul>
    </div>
  );
}

function ChannelItem({
  channel,
  active,
  onClick,
  dm,
  onDelete,
  collapsed,
}: {
  channel: Channel;
  active?: boolean;
  onClick: () => void;
  dm?: boolean;
  onDelete?: () => void;
  collapsed?: boolean;
}) {
  return (
    <li className="channel-item group">
      <button
        onClick={onClick}
        title={collapsed ? channel.name : undefined}
        className={`channel-link ${active ? "active" : ""}`}
        style={collapsed ? { justifyContent: "center", padding: 0 } : undefined}
        aria-current={active ? "page" : undefined}
      >
        {dm ? (
          <Users size={13} className="shrink-0" />
        ) : (
          <span className="channel-dot" aria-hidden="true" />
        )}
        {!collapsed && <span className="channel-name">{channel.name}</span>}
      </button>
      {onDelete && !dm && !collapsed && (
        <button
          onClick={(e) => {
            e.stopPropagation();
            onDelete();
          }}
          className="section-action"
          style={{ position: "absolute", right: 6, top: "50%", transform: "translateY(-50%)" }}
          title={`Delete ${channel.name}`}
          aria-label={`Delete room ${channel.name}`}
        >
          <X size={12} />
        </button>
      )}
    </li>
  );
}

function AgentItem({
  agent,
  active,
  onClick,
  collapsed,
}: {
  agent: Agent;
  active?: boolean;
  onClick: () => void;
  collapsed?: boolean;
}) {
  const { value, tone } = flapFor(agent.status);
  const lamp = lampFor(agent.status);
  const label = agent.display_name || agent.name;

  return (
    <li className="channel-item">
      <button
        onClick={onClick}
        title={collapsed ? `${label} — ${value}` : undefined}
        className={`channel-link ${active ? "active" : ""}`}
        style={collapsed ? { justifyContent: "center", padding: 0 } : undefined}
        aria-current={active ? "page" : undefined}
      >
        {collapsed ? (
          <Lamp tone={lamp} title={`${label} — ${value}`} />
        ) : (
          <>
            <Avatar
              name={label}
              kind="agent"
              size="xs"
              avatar={agent.avatar}
              className="shrink-0"
            />
            <span className="channel-name" style={{ fontWeight: 500 }}>
              {label}
            </span>
            <Flap value={value} tone={tone} />
          </>
        )}
      </button>
    </li>
  );
}
