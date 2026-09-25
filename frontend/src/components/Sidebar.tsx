import * as React from "react";
import { Avatar } from "@/components/ui/avatar";
import { Badge } from "@/components/ui/badge";
import { Dropdown } from "@/components/ui/dropdown-menu";
import { Tooltip } from "@/components/ui/tooltip";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  Home,
  Activity,
  MessageSquare,
  Bot,
  BookOpen,
  Search,
  Plus,
  Hash,
  Users,
  Settings,
  LogOut,
  PanelLeftClose,
  PanelLeft,
  X,
  SquarePen,
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
  pinned?: unknown[];
  open?: boolean;
  onClose?: () => void;
}

const PRIMARY_NAV = [
  { id: "home", label: "Home", icon: Home },
  { id: "work", label: "Work", icon: Activity },
  { id: "chat", label: "Chat", icon: MessageSquare },
  { id: "agents", label: "Agents", icon: Bot },
  { id: "knowledge", label: "Knowledge", icon: BookOpen },
];

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
  onClose,
}: SidebarProps) {
  const [search, setSearch] = React.useState("");
  const [collapsed, setCollapsed] = React.useState(false);

  const q = search.trim().toLowerCase();
  const matchChannel = (c: Channel) =>
    !q || (c.name || "").toLowerCase().includes(q);
  const matchAgent = (a: Agent) =>
    !q ||
    (a.name || "").toLowerCase().includes(q) ||
    (a.display_name || "").toLowerCase().includes(q);

  const rooms = channels.filter(
    (c) => (c.kind === "room" || c.kind === undefined) && matchChannel(c)
  );
  const groups = channels.filter((c) => c.kind === "group" && matchChannel(c));
  const peopleDms = channels.filter(
    (c) => c.kind === "people" && matchChannel(c)
  );
  const matchedAgents = (agents || []).filter(matchAgent);
  const matchedTeams = (teams || []).filter(
    (t) => !q || (t.name || "").toLowerCase().includes(q)
  );

  return (
    <aside
      id="sidebar"
      className={`relative flex flex-col h-full border-r border-zinc-800/80 bg-zinc-950/80 backdrop-blur-md select-none transition-all duration-200 z-30 shrink-0 ${
        collapsed ? "w-16" : "w-64"
      } ${open ? "translate-x-0" : ""}`}
      aria-label="Primary navigation"
    >
      {/* Sidebar Header */}
      <div className="flex items-center justify-between h-12 px-3 border-b border-zinc-850 shrink-0">
        {!collapsed && (
          <div className="flex items-center gap-2">
            <div className="flex items-center justify-center w-6 h-6 rounded-md bg-gradient-to-br from-violet-600 to-indigo-700 text-white font-bold text-xs shadow-sm shadow-violet-950/40">
              S
            </div>
            <span className="font-semibold text-xs tracking-tight text-zinc-100">
              swarm
            </span>
          </div>
        )}

        <div className={`flex items-center gap-1 ${collapsed ? "mx-auto" : ""}`}>
          <Tooltip content={collapsed ? "Expand sidebar" : "Collapse sidebar"}>
            <button
              type="button"
              onClick={() => setCollapsed((c) => !c)}
              className="p-1.5 rounded-md text-zinc-400 hover:text-zinc-100 hover:bg-zinc-850 transition-colors"
              aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
            >
              {collapsed ? (
                <PanelLeft className="w-4 h-4" />
              ) : (
                <PanelLeftClose className="w-4 h-4" />
              )}
            </button>
          </Tooltip>

          {!collapsed && onNewDM && (
            <Tooltip content="New message">
              <button
                type="button"
                onClick={onNewDM}
                className="p-1.5 rounded-md text-zinc-400 hover:text-zinc-100 hover:bg-zinc-850 transition-colors"
                aria-label="New message"
              >
                <SquarePen className="w-3.5 h-3.5" />
              </button>
            </Tooltip>
          )}
        </div>
      </div>

      {/* Primary Destinations Nav */}
      <nav className="flex flex-col gap-0.5 p-2 border-b border-zinc-850 shrink-0">
        {PRIMARY_NAV.map((item) => {
          const Icon = item.icon;
          const isActive =
            currentView === item.id ||
            (item.id === "chat" && currentView === "talk");
          return (
            <button
              key={item.id}
              onClick={() =>
                onViewChange?.(item.id === "chat" ? "talk" : item.id)
              }
              title={collapsed ? item.label : undefined}
              className={`flex items-center gap-2.5 px-2.5 py-1.5 rounded-lg text-xs font-medium transition-all duration-150 text-left ${
                isActive
                  ? "bg-zinc-850 text-zinc-100 font-semibold shadow-xs"
                  : "text-zinc-400 hover:text-zinc-200 hover:bg-zinc-900/80"
              } ${collapsed ? "justify-center px-0" : ""}`}
            >
              <Icon
                className={`w-4 h-4 shrink-0 ${
                  isActive ? "text-violet-400" : "text-zinc-400"
                }`}
              />
              {!collapsed && <span>{item.label}</span>}
              {!collapsed &&
                item.id === "work" &&
                (workAttentionCount || 0) > 0 && (
                  <span className="ml-auto px-1.5 py-0.2 rounded-full text-[10px] font-medium bg-rose-500/15 text-rose-300 border border-rose-500/20">
                    {workAttentionCount}
                  </span>
                )}
            </button>
          );
        })}
      </nav>

      {/* Search Filter */}
      {!collapsed && (
        <div className="p-2 border-b border-zinc-850 shrink-0">
          <div className="relative flex items-center">
            <Search className="absolute left-2.5 w-3.5 h-3.5 text-zinc-500 pointer-events-none" />
            <input
              type="search"
              placeholder="Search..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="w-full h-7 pl-8 pr-2 text-xs rounded-md bg-zinc-900/80 border border-zinc-800 text-zinc-200 placeholder:text-zinc-500 focus:outline-none focus:border-violet-500/60 transition-colors"
            />
          </div>
        </div>
      )}

      {/* Scrollable Navigation Channels & Agents */}
      <ScrollArea className="flex-1 p-2 space-y-4">
        {/* Channels */}
        {rooms.length > 0 && (
          <Section
            title="Channels"
            collapsed={collapsed}
            action={onNewChannel}
            actionLabel="New channel"
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

        {/* AI Agents / Teammates */}
        {matchedAgents.length > 0 && (
          <Section
            title="Teammates"
            collapsed={collapsed}
            action={onNewAgent}
            actionLabel="New agent"
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

        {/* Groups */}
        {groups.length > 0 && (
          <Section
            title="Groups"
            collapsed={collapsed}
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

        {/* Direct Messages */}
        {peopleDms.length > 0 && (
          <Section
            title="Direct Messages"
            collapsed={collapsed}
            action={onNewDM}
            actionLabel="New DM"
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

        {/* Teams */}
        {matchedTeams.length > 0 && (
          <Section
            title="Teams"
            collapsed={collapsed}
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
            <p className="px-2 py-4 text-xs text-zinc-500 text-center">
              No matches for “{search.trim()}”
            </p>
          )}
      </ScrollArea>

      {/* User Footer Profile Dock */}
      <div className="flex items-center justify-between p-2.5 border-t border-zinc-850 bg-zinc-950/60 shrink-0">
        <div className="flex items-center gap-2 min-w-0">
          <Avatar
            name={user?.handle || "User"}
            kind="human"
            size="sm"
            className="shrink-0"
          />
          {!collapsed && (
            <div className="flex flex-col min-w-0">
              <span className="text-xs font-semibold text-zinc-200 truncate">
                {user?.handle}
              </span>
              <div className="flex items-center gap-1.5 text-[10px] text-zinc-400">
                <span
                  className={`w-1.5 h-1.5 rounded-full ${
                    wsStatus === "connected"
                      ? "bg-emerald-400"
                      : wsStatus === "connecting"
                      ? "bg-amber-400 animate-pulse"
                      : "bg-zinc-600"
                  }`}
                />
                <span>
                  {wsStatus === "connected"
                    ? "Online"
                    : wsStatus === "connecting"
                    ? "Connecting"
                    : "Offline"}
                </span>
                {meRole === "admin" && (
                  <Badge variant="warning" size="sm" className="ml-1 text-[9px] py-0 px-1">
                    Admin
                  </Badge>
                )}
              </div>
            </div>
          )}
        </div>

        {!collapsed && (
          <Dropdown
            align="right"
            trigger={
              <button
                type="button"
                className="p-1 rounded-md text-zinc-400 hover:text-zinc-100 hover:bg-zinc-850 transition-colors"
                aria-label="Account options"
              >
                <Settings className="w-3.5 h-3.5" />
              </button>
            }
            items={[
              {
                label: "Settings",
                icon: <Settings className="w-3.5 h-3.5" />,
                onClick: onOpenSettings,
              },
              "divider",
              {
                label: "Sign out",
                icon: <LogOut className="w-3.5 h-3.5 text-rose-400" />,
                danger: true,
                onClick: onLogout,
              },
            ]}
          />
        )}
      </div>
    </aside>
  );
}

function Section({
  title,
  children,
  action,
  actionLabel,
  collapsed,
}: {
  title: string;
  children: React.ReactNode;
  action?: () => void;
  actionLabel?: string;
  collapsed?: boolean;
}) {
  return (
    <div className="space-y-1">
      {!collapsed && (
        <div className="flex items-center justify-between px-2 py-1 text-[11px] font-semibold text-zinc-400 tracking-wider">
          <span>{title}</span>
          {action && (
            <button
              onClick={action}
              className="p-0.5 rounded hover:text-zinc-100 hover:bg-zinc-800 transition-colors"
              title={actionLabel}
              aria-label={actionLabel}
            >
              <Plus className="w-3.5 h-3.5" />
            </button>
          )}
        </div>
      )}
      <ul className="space-y-0.5">{children}</ul>
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
    <li className="relative group">
      <button
        onClick={onClick}
        title={collapsed ? channel.name : undefined}
        className={`w-full flex items-center gap-2 px-2 py-1.5 rounded-md text-xs font-medium transition-all text-left ${
          active
            ? "bg-zinc-850 text-zinc-100 font-semibold shadow-xs"
            : "text-zinc-400 hover:text-zinc-200 hover:bg-zinc-900/80"
        } ${collapsed ? "justify-center px-0" : ""}`}
      >
        {dm ? (
          <Users className="w-3.5 h-3.5 shrink-0 text-zinc-400" />
        ) : (
          <Hash className="w-3.5 h-3.5 shrink-0 text-zinc-400" />
        )}
        {!collapsed && <span className="truncate">{channel.name}</span>}
      </button>
      {onDelete && !dm && !collapsed && (
        <button
          onClick={(e) => {
            e.stopPropagation();
            onDelete();
          }}
          className="absolute right-1.5 top-1/2 -translate-y-1/2 p-0.5 rounded text-zinc-500 hover:text-rose-400 hover:bg-zinc-800 opacity-0 group-hover:opacity-100 transition-opacity"
          title={`Delete #${channel.name}`}
          aria-label={`Delete channel ${channel.name}`}
        >
          <X className="w-3 h-3" />
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
  const isWorking = agent.status === "working";
  const needsApproval = agent.status === "needs_approval";

  return (
    <li>
      <button
        onClick={onClick}
        title={collapsed ? agent.display_name || agent.name : undefined}
        className={`w-full flex items-center gap-2 px-2 py-1.5 rounded-md text-xs font-medium transition-all text-left ${
          active
            ? "bg-zinc-850 text-zinc-100 font-semibold shadow-xs"
            : "text-zinc-400 hover:text-zinc-200 hover:bg-zinc-900/80"
        } ${collapsed ? "justify-center px-0" : ""}`}
      >
        <div className="relative shrink-0">
          <Avatar
            name={agent.display_name || agent.name}
            kind="agent"
            size="xs"
            avatar={agent.avatar}
          />
          <span
            className={`absolute -bottom-0.5 -right-0.5 w-2 h-2 rounded-full border border-zinc-950 ${
              isWorking
                ? "bg-violet-400 animate-ping"
                : needsApproval
                ? "bg-amber-400"
                : "bg-emerald-400"
            }`}
          />
        </div>
        {!collapsed && (
          <span className="truncate">{agent.display_name || agent.name}</span>
        )}
        {!collapsed && needsApproval && (
          <Badge variant="warning" size="sm" className="ml-auto text-[9px] py-0 px-1">
            !
          </Badge>
        )}
      </button>
    </li>
  );
}
