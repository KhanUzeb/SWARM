import * as React from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dropdown } from "@/components/ui/dropdown-menu";
import { Tooltip } from "@/components/ui/tooltip";
import {
  Menu,
  Hash,
  Bot,
  Users,
  Search,
  Terminal,
  PanelRight,
  ShieldAlert,
  Sparkles,
  Activity,
} from "lucide-react";

interface TopBarProps {
  channel?: { id?: string; name?: string; topic?: string; kind?: string };
  agents: Array<{ name: string; status?: string; display_name?: string }>;
  onToggleComputer: () => void;
  computerOpen: boolean;
  onOpenCommandPalette: () => void;
  onViewChange: (view: string) => void;
  currentView: string;
  approvals: Array<{ id: string; status: string; channel_id?: string; agent_name?: string; action?: string }>;
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

const VIEW_LABELS: Record<string, string> = {
  home: "Home",
  work: "Work",
  chat: "Chat",
  agents: "Agents",
  knowledge: "Knowledge",
};

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
  participants,
  onOpenSidebar,
}: TopBarProps) {
  const normalizedView = VIEW_ALIASES[currentView] || currentView;
  const go = (v: string) => onViewChange(VIEW_TARGETS[v] || v);
  const pendingApprovals = approvals.filter(
    (a) => a.status === "pending" && a.channel_id === channel?.id
  );
  const working = agents.filter((a) => a.status === "working").length;
  const attention = (workAttention || []).length;

  const isDm = channel?.kind === "dm";
  const isAgent = isDm && agents.some((a) => a.name === channel?.name);

  return (
    <header
      id="topbar"
      className="flex items-center justify-between h-12 px-4 border-b border-zinc-800/80 bg-zinc-950/70 backdrop-blur-md z-20 shrink-0 select-none transition-colors"
    >
      {/* Left: Breadcrumbs & Channel Topic */}
      <div className="flex items-center gap-3 min-w-0 flex-1">
        {onOpenSidebar && (
          <button
            type="button"
            className="topbar-sidebar-trigger flex items-center justify-center p-1.5 rounded-md text-zinc-400 hover:text-zinc-100 hover:bg-zinc-850 transition-colors"
            onClick={onOpenSidebar}
            aria-label="Open navigation"
          >
            <Menu className="w-4 h-4" />
          </button>
        )}

        <div className="flex items-center gap-2 min-w-0">
          {/* Breadcrumb Workspace */}
          <div className="hidden sm:flex items-center gap-1.5 text-xs font-semibold text-zinc-400">
            <span className="flex h-2 w-2 rounded-full bg-violet-500 shadow-[0_0_8px_rgba(139,92,246,0.5)]" />
            <span>Swarm</span>
            <span className="text-zinc-600">/</span>
          </div>

          {/* Current Channel / Destination */}
          <div className="flex items-center gap-2 min-w-0">
            <div className="flex items-center justify-center w-6 h-6 rounded-md bg-zinc-900 border border-zinc-800 text-zinc-400 shrink-0">
              {isAgent ? (
                <Bot className="w-3.5 h-3.5 text-violet-400" />
              ) : isDm ? (
                <Users className="w-3.5 h-3.5 text-zinc-400" />
              ) : (
                <Hash className="w-3.5 h-3.5 text-zinc-400" />
              )}
            </div>

            <div className="flex items-baseline gap-2 min-w-0 truncate">
              <h1 className="text-xs font-semibold text-zinc-100 truncate tracking-tight">
                {channel?.name || "Select a channel"}
              </h1>

              {channel?.topic && (
                <span className="hidden xl:inline text-[11px] text-zinc-400 truncate max-w-[280px]">
                  {channel.topic}
                </span>
              )}
            </div>
          </div>

          {/* Connection Status or Active Session pill */}
          {wsStatus && wsStatus !== "connected" && (
            <span className="hidden sm:inline-flex items-center gap-1 px-1.5 py-0.5 rounded-full text-[10px] font-medium bg-amber-500/10 text-amber-400 border border-amber-500/20">
              <span className="w-1.5 h-1.5 rounded-full bg-amber-400 animate-pulse" />
              {wsStatus === "connecting" ? "Connecting" : "Offline"}
            </span>
          )}

          {(workActive || []).length > 0 && (
            <span className="hidden md:inline-flex items-center gap-1 px-1.5 py-0.5 rounded-full text-[10px] font-mono bg-violet-500/10 text-violet-300 border border-violet-500/20">
              <span className="w-1.5 h-1.5 rounded-full bg-violet-400 animate-ping" />
              {workActive?.length} run{!workConnected ? " (replaying)" : ""}
            </span>
          )}
        </div>
      </div>

      {/* Center: OpenCode-style View Navigation Tabs */}
      <div className="hidden lg:flex items-center justify-center">
        <div
          role="tablist"
          className="flex items-center gap-0.5 p-0.5 rounded-lg bg-zinc-900/90 border border-zinc-800/80 backdrop-blur-sm"
        >
          {[
            ["home", "Home"],
            ["work", "Work"],
            ["chat", "Chat"],
            ["agents", "Agents"],
            ["knowledge", "Knowledge"],
          ].map(([v, label]) => {
            const active = normalizedView === v;
            return (
              <button
                key={v}
                role="tab"
                aria-selected={active}
                onClick={() => go(v)}
                className={`px-3 py-1 rounded-md text-xs font-medium transition-all duration-150 select-none ${
                  active
                    ? "bg-zinc-800 text-zinc-100 shadow-sm font-semibold"
                    : "text-zinc-400 hover:text-zinc-200 hover:bg-zinc-850/50"
                }`}
              >
                {label}
              </button>
            );
          })}
        </div>
      </div>

      {/* Right: Actions, Badges & Toggles */}
      <div className="flex items-center gap-1.5 shrink-0">
        {/* Working status */}
        {working > 0 && (
          <Badge variant="success" className="gap-1 font-mono text-[10px]">
            <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse" />
            {working} working
          </Badge>
        )}

        {/* Pending approvals */}
        {pendingApprovals.length > 0 && (
          <Dropdown
            label={`${pendingApprovals.length} pending approvals`}
            trigger={
              <button className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-medium bg-amber-500/15 text-amber-300 border border-amber-500/30 hover:bg-amber-500/25 transition-colors">
                <ShieldAlert className="w-3 h-3 text-amber-400" />
                <span>
                  {pendingApprovals.length} approval
                  {pendingApprovals.length !== 1 ? "s" : ""}
                </span>
              </button>
            }
            items={pendingApprovals.map((a) => ({
              id: a.id,
              label: `${a.agent_name || "Agent"}: ${a.action || "Action"}`,
              onClick: () => onResolveApproval(a.id, "approved"),
            }))}
          />
        )}

        {/* Attention badge */}
        {attention > 0 && (
          <button
            onClick={onToggleWorkRail}
            className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-medium bg-rose-500/15 text-rose-300 border border-rose-500/30 hover:bg-rose-500/25 transition-colors"
            title="Open work needing attention"
          >
            <Activity className="w-3 h-3 text-rose-400" />
            <span>
              {attention} need{attention === 1 ? "s" : ""} you
            </span>
          </button>
        )}

        {/* Command Search Trigger */}
        <Tooltip content="Quick command palette (⌘K)">
          <Button
            variant="ghost"
            size="icon-sm"
            onClick={onOpenCommandPalette}
            className="text-zinc-400 hover:text-zinc-100 hover:bg-zinc-850"
            aria-label="Command palette"
          >
            <Search className="w-3.5 h-3.5" />
          </Button>
        </Tooltip>

        {/* Work Rail Toggle */}
        <Tooltip content={workRailOpen ? "Hide work rail" : "Show work rail"}>
          <Button
            variant={workRailOpen ? "secondary" : "ghost"}
            size="icon-sm"
            onClick={onToggleWorkRail}
            className={`transition-colors ${
              workRailOpen
                ? "bg-zinc-800 text-zinc-100 border-zinc-700"
                : "text-zinc-400 hover:text-zinc-100 hover:bg-zinc-850"
            }`}
            aria-expanded={workRailOpen}
            aria-label="Toggle work rail"
          >
            <PanelRight className="w-3.5 h-3.5" />
          </Button>
        </Tooltip>

        {/* Computer Sandbox Toggle */}
        <Tooltip content={computerOpen ? "Hide computer sandbox" : "Open computer sandbox"}>
          <Button
            variant={computerOpen ? "primary" : "ghost"}
            size="icon-sm"
            onClick={onToggleComputer}
            className={`transition-colors ${
              computerOpen
                ? "bg-violet-600 text-white shadow-sm"
                : "text-zinc-400 hover:text-zinc-100 hover:bg-zinc-850"
            }`}
            aria-expanded={computerOpen}
            aria-label="Toggle computer sandbox"
          >
            <Terminal className="w-3.5 h-3.5" />
          </Button>
        </Tooltip>
      </div>
    </header>
  );
}
