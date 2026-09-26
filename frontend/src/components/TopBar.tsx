import * as React from "react";
import { Button } from "@/components/ui/button";
import { Dropdown } from "@/components/ui/dropdown-menu";
import { Tooltip } from "@/components/ui/tooltip";
import { Flap, Lamp } from "@/components/Flap";
import {
  Activity,
  Bot,
  Menu,
  PanelRight,
  Search,
  ShieldAlert,
  Terminal,
  Users,
} from "lucide-react";

interface TopBarProps {
  channel?: { id?: string; name?: string; topic?: string; kind?: string };
  agents: Array<{ name: string; status?: string; display_name?: string }>;
  onToggleComputer: () => void;
  computerOpen: boolean;
  onOpenCommandPalette: () => void;
  onViewChange: (view: string) => void;
  currentView: string;
  approvals: Array<{
    id: string;
    status: string;
    channel_id?: string;
    agent_name?: string;
    action?: string;
  }>;
  onResolveApproval: (id: string, decision: string) => void;
  wsStatus?: string;
  workActive?: unknown[];
  workAttention?: unknown[];
  workConnected?: boolean;
  onToggleWorkRail: () => void;
  workRailOpen?: boolean;
  participants?: string[];
  onOpenSidebar?: () => void;
}

const VIEW_ALIASES: Record<string, string> = {
  dashboard: "home",
  home: "home",
  workflows: "work",
  runs: "work",
  work: "work",
  talk: "chat",
  chat: "chat",
  agents: "agents",
  knowledge: "knowledge",
};

const VIEW_TARGETS: Record<string, string> = {
  home: "dashboard",
  work: "work",
  chat: "talk",
  agents: "agents",
  knowledge: "knowledge",
};

const VIEWS: Array<[string, string]> = [
  ["home", "Board"],
  ["work", "Work"],
  ["chat", "Talk"],
  ["agents", "Roster"],
  ["knowledge", "Knowledge"],
];

export function TopBar({
  channel,
  agents,
  onToggleComputer,
  computerOpen,
  onOpenCommandPalette,
  onViewChange,
  currentView,
  approvals,
  onResolveApproval,
  wsStatus,
  workActive,
  workAttention,
  workConnected,
  onToggleWorkRail,
  workRailOpen,
  onOpenSidebar,
}: TopBarProps) {
  const normalizedView = VIEW_ALIASES[currentView] || currentView;
  const go = (v: string) => onViewChange(VIEW_TARGETS[v] || v);

  const pendingApprovals = approvals.filter(
    (a) => a.status === "pending" && a.channel_id === channel?.id,
  );
  const working = agents.filter((a) => a.status === "working").length;
  const attention = (workAttention || []).length;
  const activeRuns = (workActive || []).length;

  const isDm = channel?.kind === "dm";
  const isAgent = isDm && agents.some((a) => a.name === channel?.name);
  const link = wsStatus === "connected" ? "connected" : wsStatus === "connecting" ? "connecting" : "offline";

  return (
    <header id="topbar">
      <div className="topbar-left">
        {onOpenSidebar && (
          <button
            type="button"
            className="btn btn-ghost btn-icon btn-sm topbar-sidebar-trigger"
            onClick={onOpenSidebar}
            aria-label="Open roster"
          >
            <Menu size={15} />
          </button>
        )}

        <div className="channel-avatar" aria-hidden="true">
          {isAgent ? (
            <Bot size={12} />
          ) : isDm ? (
            <Users size={12} />
          ) : (
            (channel?.name || "?").slice(0, 2).toUpperCase()
          )}
        </div>

        <div className="channel-title-row">
          <h1 className="channel-name">{channel?.name || "No room selected"}</h1>
          {channel?.topic && <span className="channel-topic">{channel.topic}</span>}
        </div>

        {/* The connection lamp is the only always-lit element on the housing. */}
        <span
          className="status-chip"
          title={
            link === "connected"
              ? "Linked to the workspace"
              : link === "connecting"
                ? "Reconnecting"
                : "No link to the workspace"
          }
        >
          <Lamp tone={link === "connected" ? "go" : link === "connecting" ? "amber" : "off"} />
          <span className="reg">{link}</span>
        </span>
      </div>

      <div className="topbar-center">
        <div className="view-tabs" role="tablist" aria-label="Views">
          {VIEWS.map(([v, label]) => {
            const active = normalizedView === v;
            return (
              <button
                key={v}
                role="tab"
                aria-selected={active}
                onClick={() => go(v)}
                className={`view-tab ${active ? "active" : ""}`}
              >
                {label}
              </button>
            );
          })}
        </div>
      </div>

      <div className="topbar-right">
        {activeRuns > 0 && (
          <Flap
            value={`${activeRuns} run${activeRuns === 1 ? "" : "s"}`}
            tone="amber"
            title={workConnected ? "Runs in progress" : "Replaying run history"}
          />
        )}

        {pendingApprovals.length > 0 && (
          <Dropdown
            label={`${pendingApprovals.length} approvals waiting`}
            trigger={
              <button className="chip chip-hold" type="button">
                <ShieldAlert size={12} />
                {pendingApprovals.length} to clear
              </button>
            }
            items={pendingApprovals.map((a) => ({
              id: a.id,
              label: `${a.agent_name || "Teammate"}: ${a.action || "action"}`,
              onClick: () => onResolveApproval(a.id, "approved"),
            }))}
          />
        )}

        {attention > 0 && (
          <button onClick={onToggleWorkRail} className="chip chip-hold" type="button">
            <Activity size={12} />
            {attention} need{attention === 1 ? "s" : ""} you
          </button>
        )}

        <Tooltip content="Command palette">
          <Button variant="ghost" size="icon-sm" onClick={onOpenCommandPalette} aria-label="Command palette">
            <Search size={14} />
          </Button>
        </Tooltip>

        <Tooltip content={workRailOpen ? "Hide work rail" : "Show work rail"}>
          <Button
            variant={workRailOpen ? "secondary" : "ghost"}
            size="icon-sm"
            onClick={onToggleWorkRail}
            aria-expanded={workRailOpen}
            aria-label="Toggle work rail"
          >
            <PanelRight size={14} />
          </Button>
        </Tooltip>

        <Tooltip content={computerOpen ? "Hide computer" : "Open computer"}>
          <Button
            variant={computerOpen ? "secondary" : "ghost"}
            size="icon-sm"
            onClick={onToggleComputer}
            aria-expanded={computerOpen}
            aria-label="Toggle computer"
          >
            <Terminal size={14} />
          </Button>
        </Tooltip>
      </div>
    </header>
  );
}
