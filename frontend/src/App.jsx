import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import "./components.css";
import {
  api, apiJson, authHeaders, CACHE_TTL, HISTORY_LIMIT, DEFAULT_MODEL, ALL_TOOLS,
  escapeHtml, fmtTime, fmtBytes, initials, botLabel, slugFromName, statusLabel, renderMath,
  Avatar, Badge, Button, Input, Textarea, Card, Dropdown, Tooltip, ToastContainer, Modal, Skeleton, Spinner, EmptyState, ScrollArea, Divider,
  RichBody, CodeBlock,
} from "./ui.jsx";
import { ApprovalCard, AgentMessage, TaskRow, PixelLoader } from "./beautifului.jsx";
import { Sidebar } from "./components/Sidebar.jsx";
import { TopBar } from "./components/TopBar.jsx";
import { MessageList } from "./components/MessageList.jsx";
import { Composer } from "./components/Composer.jsx";
import { ComputerPanel } from "./components/ComputerPanel.jsx";
import { CommandPalette } from "./components/CommandPalette.jsx";
import { LoginScreen } from "./components/LoginScreen.jsx";
import { CommandCenter, RunMonitor } from "./components/CommandCenter.jsx";

const PANEL_GROUPS = [
  { id: "places", label: "Places", tabs: [
    { id: "files", label: "Sandbox" }, { id: "system", label: "System" }, { id: "browser", label: "Browser" },
  ]},
  { id: "connect", label: "Connect", tabs: [
    { id: "ai", label: "AI" }, { id: "apps", label: "Apps" }, { id: "tools", label: "Tools" }, { id: "plugins", label: "Plugins" },
  ]},
  { id: "automate", label: "Automate", tabs: [
    { id: "skills", label: "Skills" }, { id: "routines", label: "Routines" }, { id: "approvals", label: "Approvals" },
  ]},
];

export default function App() {
  const [user, setUser] = useState(null);
  const [token, setToken] = useState(null);
  const [channel, setChannel] = useState("dm-swarm");
  const [channels, setChannels] = useState([]);
  const [agents, setAgents] = useState([]);
  const [allAgents, setAllAgents] = useState([]);
  const [teams, setTeams] = useState([]);
  const [approvals, setApprovals] = useState([]);
  const [computer, setComputer] = useState(null);
  const [messages, setMessages] = useState({});
  const [order, setOrder] = useState([]);
  const [replyCounts, setReplyCounts] = useState({});
  const [reactions, setReactions] = useState({});
  const [hasMore, setHasMore] = useState(false);
  const [loadingLog, setLoadingLog] = useState(false);
  const [threadId, setThreadId] = useState(null);
  const [threadParent, setThreadParent] = useState(null);
  const [threadReplies, setThreadReplies] = useState([]);
  const [wsStatus, setWsStatus] = useState("offline");
  const [toast, setToast] = useState(null);
  const [loginErr, setLoginErr] = useState("");
  const [loginBusy, setLoginBusy] = useState(false);
  const [demoMode, setDemoMode] = useState(false);
  const [computerOpen, setComputerOpen] = useState(() => localStorage.getItem("swarm_computer") !== "0");
  const [panelTab, setPanelTab] = useState("files");
  const [mainView, setMainView] = useState("dashboard");
  const [cmdOpen, setCmdOpen] = useState(false);
  const [typing, setTyping] = useState("");
  const [meRole, setMeRole] = useState("member");
  const [searchOpen, setSearchOpen] = useState(false);
  const [selectedRun, setSelectedRun] = useState(null);
  const [quickAction, setQuickAction] = useState(null);

  const wsRef = useRef(null);
  const wsGen = useRef(0);
  const lastSeenId = useRef(0);
  const reconnectAttempt = useRef(0);
  const reconnectTimer = useRef(null);
  const intentionalClose = useRef(false);
  const tokenRef = useRef(token);
  const channelRef = useRef(channel);
  tokenRef.current = token; channelRef.current = channel;

  const current = channels.find(c => c.id === channel) || { id: channel, name: channel, topic: "" };
  const bot = allAgents.find(a => a.dm_channel_id === channel);
  const rooms = channels.filter(c => c.kind !== "dm" && c.kind !== "group" && c.kind !== "people");
  const groups = channels.filter(c => c.kind === "group");
  const peopleDms = channels.filter(c => c.kind === "people");
  const roots = order.map(id => messages[id]).filter(Boolean);
  const pendingHere = approvals.filter(a => a.status === "pending" && a.channel_id === channel);

  const flash = useCallback((msg, type = "info", title) => {
    setToast({ msg, type, title });
    setTimeout(() => setToast(null), 3500);
  }, []);

  const remember = useCallback((m) => {
    if (m.id && m.id > lastSeenId.current) lastSeenId.current = m.id;
    setMessages(prev => ({ ...prev, [m.id]: m }));
    setReactions(prev => {
      if (m.reactions) return { ...prev, [m.id]: m.reactions.slice() };
      if (prev[m.id]) return prev;
      return { ...prev, [m.id]: [] };
    });
  }, []);

  const ingestLive = useCallback((raw) => {
    if (raw?.type === "message_deleted") {
      const deleted = new Set(raw.ids || [raw.message_id]);
      setMessages(prev => Object.fromEntries(Object.entries(prev).filter(([id]) => !deleted.has(Number(id)))));
      setOrder(prev => prev.filter(id => !deleted.has(id)));
      setReplyCounts(prev => Object.fromEntries(Object.entries(prev).filter(([id]) => !deleted.has(Number(id)))));
      return;
    }
    if (raw?.type === "reaction") {
      setReactions(prev => ({
        ...prev,
        [raw.message_id]: [...(prev[raw.message_id] || []), { author: raw.author, emoji: raw.emoji }],
      }));
      return;
    }
    const m = { reactions: [], ...raw };
    setMessages(prev => {
      if (prev[m.id]) return prev;
      return { ...prev, [m.id]: m };
    });
    if (m.id && m.id > lastSeenId.current) lastSeenId.current = m.id;
    setReactions(prev => (prev[m.id] ? prev : { ...prev, [m.id]: m.reactions || [] }));
    if (m.parent_id) {
      setReplyCounts(prev => ({ ...prev, [m.parent_id]: (prev[m.parent_id] || 0) + 1 }));
      setThreadId(tid => {
        if (tid === m.parent_id) setThreadReplies(list => list.some(x => x.id === m.id) ? list : [...list, m]);
        return tid;
      });
      return;
    }
    if (m.author_kind === "agent") { setTyping(""); }
    setOrder(prev => (prev.includes(m.id) ? prev : [...prev, m.id]));
    if (m.author_kind === "system") loadComputer();
  }, []);

  // ── Data Loaders ──
  async function loadChannels() {
    if (!tokenRef.current) return [];
    const res = await apiJson("/api/channels", { token: tokenRef.current, cacheTtl: CACHE_TTL.list });
    if (!res.ok) throw new Error("channels");
    setChannels(res.data);
    return res.data;
  }

  async function loadAllAgents() {
    if (!tokenRef.current) return;
    try {
      const res = await apiJson("/api/agents", { token: tokenRef.current, cacheTtl: CACHE_TTL.list });
      setAllAgents(res.ok ? res.data : []);
    } catch { setAllAgents([]); }
  }

  async function loadAgents(channelId) {
    if (!tokenRef.current) return;
    try {
      const path = `/api/agents?channel_id=${encodeURIComponent(channelId)}`;
      const res = await apiJson(path, { token: tokenRef.current, cacheTtl: CACHE_TTL.list });
      setAgents(res.ok ? res.data : []);
    } catch { setAgents([]); }
  }

  async function loadGroqStatus() {
    try {
      const pub = await apiJson("/api/status", { cacheTtl: CACHE_TTL.status });
      const authed = tokenRef.current ? await apiJson("/api/status", { token: tokenRef.current, cacheTtl: CACHE_TTL.status }) : pub;
      const data = authed.ok ? authed.data : (pub.data || {});
      setDemoMode(!!data.demo);
      if (data.me?.role) setMeRole(data.me.role);
    } catch { setDemoMode(false); }
  }

  async function loadApprovals() {
    if (!tokenRef.current) return;
    try {
      const res = await apiJson("/api/approvals?status=pending", { token: tokenRef.current, cacheTtl: 10_000 });
      setApprovals(res.ok ? res.data : []);
    } catch { setApprovals([]); }
  }

  async function loadComputer() {
    if (!tokenRef.current) return;
    try {
      const res = await apiJson("/api/computer", { token: tokenRef.current, cacheTtl: CACHE_TTL.list });
      setComputer(res.ok ? res.data : null);
    } catch { setComputer(null); }
  }

  async function loadTeams() {
    if (!tokenRef.current) return;
    try {
      const res = await apiJson("/api/teams", { token: tokenRef.current, cacheTtl: CACHE_TTL.list });
      setTeams(res.ok ? res.data : []);
    } catch { setTeams([]); }
  }

  async function loadHistory(channelId, beforeId) {
    if (!tokenRef.current) throw new Error("auth");
    const params = new URLSearchParams({ limit: String(HISTORY_LIMIT) });
    if (beforeId) params.set("before_id", String(beforeId));
    const res = await apiJson(`/api/channels/${channelId}/messages?${params}`, { token: tokenRef.current });
    if (!res.ok) throw new Error("history");
    return res.data;
  }

  function applyHistory(history, prepend = false) {
    setHasMore(history.length === HISTORY_LIMIT);
    const nextMsgs = {}, nextReact = {}, counts = {};
    for (const m of history) {
      nextMsgs[m.id] = m;
      nextReact[m.id] = m.reactions ? m.reactions.slice() : [];
      if (m.parent_id) counts[m.parent_id] = (counts[m.parent_id] || 0) + 1;
      if (m.id > lastSeenId.current) lastSeenId.current = m.id;
    }
    setMessages(prev => ({ ...prev, ...nextMsgs }));
    setReactions(prev => ({ ...prev, ...nextReact }));
    setReplyCounts(prev => ({ ...prev, ...counts }));
    setOrder(prev => {
      const ids = history.map(m => m.id);
      return prepend ? [...ids, ...prev] : [...prev, ...ids.filter(id => !prev.includes(id))];
    });
  }

  async function loadChannelMessages(channelId) {
    setLoadingLog(true);
    setMessages({});
    setOrder([]);
    setReplyCounts({});
    setReactions({});
    setThreadId(null);
    setThreadReplies([]);
    try {
      const history = await loadHistory(channelId);
      applyHistory(history);
      await Promise.all([loadAgents(channelId), loadComputer(), loadApprovals()]);
    } catch (e) {
      flash("Failed to load messages", "error");
    } finally { setLoadingLog(false); }
  }

  // ── WebSocket ──
  function connectWs() {
    if (!tokenRef.current) return;
    const t = tokenRef.current;
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const url = `${proto}://${location.host}/ws/${encodeURIComponent(channelRef.current)}`;
    intentionalClose.current = false;
    setWsStatus("connecting");
    const ws = new WebSocket(url);
    wsRef.current = ws;

    ws.onopen = () => {
      reconnectAttempt.current = 0;
      setWsStatus("connected");
      ws.send(JSON.stringify({ token: t, last_seen_id: lastSeenId.current || null }));
    };
    ws.onmessage = (ev) => {
      let msg; try { msg = JSON.parse(ev.data); } catch { return; }
      if (msg.type === "message" || msg.type === "live") ingestLive(msg.message || msg.data || msg);
      else if (msg.type === "message_deleted" || msg.type === "reaction") ingestLive(msg);
      else if (msg.type === "typing") setTyping(msg.author);
      else if (msg.type === "status") { /* agent status updates */ }
      else if (msg.type === "error") flash(msg.detail || "Error", "error");
    };
    ws.onclose = (ev) => {
      if (intentionalClose.current || ev.code === 4001) { setWsStatus("offline"); return; }
      setWsStatus("offline");
      const delay = Math.min(1000 * 2 ** reconnectAttempt.current, 15000);
      reconnectAttempt.current++;
      reconnectTimer.current = setTimeout(connectWs, delay);
    };
    ws.onerror = () => { ws.close(); };
  }

  function disconnectWs() {
    intentionalClose.current = true;
    if (reconnectTimer.current) clearTimeout(reconnectTimer.current);
    if (wsRef.current) wsRef.current.close();
  }

  // ── Auth ──
  // The backend has a single unified auth endpoint: POST /api/register.
  // For an existing handle it returns a fresh token (login); for a new
  // handle it creates the user (first user becomes admin). There is no
  // separate /api/login route.
  async function handleLogin(handle, password) {
    const res = await apiJson("/api/register", { method: "POST", body: { handle, password } });
    if (!res.ok) throw new Error(res.data.detail || "Login failed");
    setUser({ handle }); setToken(res.data.token);
    localStorage.setItem("swarm_token", res.data.token);
    localStorage.setItem("swarm_handle", handle);
  }

  async function handleSetup(handle, password) {
    const res = await apiJson("/api/register", { method: "POST", body: { handle, password } });
    if (!res.ok) throw new Error(res.data.detail || "Setup failed");
    setUser({ handle }); setToken(res.data.token);
    localStorage.setItem("swarm_token", res.data.token);
    localStorage.setItem("swarm_handle", handle);
  }

  function handleLogout() {
    disconnectWs();
    localStorage.removeItem("swarm_token");
    localStorage.removeItem("swarm_handle");
    setUser(null); setToken(null); setChannels([]); setAllAgents([]); setMessages({}); setOrder([]);
    setWsStatus("offline");
  }

  // ── Effects ──
  useEffect(() => {
    const savedToken = localStorage.getItem("swarm_token");
    const savedHandle = localStorage.getItem("swarm_handle");
    if (savedToken && savedHandle) { setToken(savedToken); setUser({ handle: savedHandle }); }
    loadGroqStatus();
  }, []);

  useEffect(() => {
    if (token) {
      (async () => {
        try {
          await Promise.all([loadChannels(), loadAllAgents(), loadTeams()]);
          await loadChannelMessages(channelRef.current);
          connectWs();
        } catch (e) { flash("Failed to initialize", "error"); }
      })();
    }
    return () => disconnectWs();
  }, [token]);

  useEffect(() => {
    if (token && channel) {
      loadChannelMessages(channel);
      disconnectWs();
      connectWs();
    }
  }, [channel]);

  // ── Keyboard shortcuts ──
  useEffect(() => {
    function onKey(e) {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") { e.preventDefault(); setCmdOpen(true); }
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "c" && user) { e.preventDefault(); setComputerOpen(o => !o); }
      if (e.key === "Escape") { setCmdOpen(false); setThreadId(null); }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [user]);

  // ── Send ──
  async function sendMessage(text, parentId = null) {
    if (!tokenRef.current) return;
    const body = { author: user.handle, body: text, author_kind: "human", parent_id: parentId };
    const res = await apiJson(`/api/channels/${channelRef.current}/messages`, {
      token: tokenRef.current, method: "POST", body,
    });
    if (!res.ok) { flash("Failed to send", "error"); return; }
    remember(res.data);
  }

  async function onReply(parentId) {
    setThreadId(parentId);
    try {
      const res = await apiJson(`/api/messages/${parentId}/thread`, { token: tokenRef.current });
      if (res.ok) setThreadReplies(res.data.replies || []);
    } catch { setThreadReplies([]); }
  }
  async function onReact(messageId, target) {
    const emoji = typeof target === "string" ? target : "👍";
    const res = await apiJson(`/api/messages/${messageId}/reactions`, {
      token: tokenRef.current,
      method: "POST",
      body: { author: user.handle, emoji },
    });
    if (res.ok) flash("Reaction added", "success");
    else flash("Could not add reaction", "error");
  }
  async function onDelete(m) {
    const res = await apiJson(`/api/messages/${m.id}`, { token: tokenRef.current, method: "DELETE" });
    if (res.ok) flash("Message deleted", "info");
    else flash("Could not delete message", "error");
  }

  async function onResolveApproval(id, decision) {
    const res = await apiJson(`/api/approvals/${id}`, { token: tokenRef.current, method: "PATCH", body: { status: decision } });
    if (res.ok) { flash(`Approval ${decision}`, decision === "approved" ? "success" : "warning"); loadApprovals(); }
  }

  // ── Command palette commands ──
  const commands = useMemo(() => [
    { id: "cmd-new-channel", group: "Create", label: "New channel", icon: "#", hint: "room", shortcut: "⌘⇧C", keywords: ["channel", "room"], action: () => setQuickAction("channel") },
    { id: "cmd-new-agent", group: "Create", label: "New agent", icon: "🤖", hint: "bot", keywords: ["agent", "bot", "teammate"], action: () => setQuickAction("agent") },
    { id: "cmd-new-group", group: "Create", label: "New group", icon: "👥", hint: "pod", keywords: ["group", "team", "pod"], action: () => setQuickAction("group") },
    { id: "cmd-new-dm", group: "Create", label: "New direct message", icon: "@", hint: "person", keywords: ["dm", "message", "person"], action: () => setQuickAction("dm") },
    { id: "cmd-toggle-computer", group: "View", label: "Toggle computer panel", icon: "🖥", hint: "", keywords: ["computer", "sandbox", "screen"], action: () => setComputerOpen(o => !o) },
    { id: "cmd-view-talk", group: "View", label: "Go to Talk", icon: "💬", keywords: ["chat", "message"], action: () => setMainView("talk") },
    { id: "cmd-view-paper", group: "View", label: "Go to Paper", icon: "📄", keywords: ["paper", "latex", "doc"], action: () => setMainView("paper") },
    { id: "cmd-view-files", group: "View", label: "Go to Files", icon: "📁", keywords: ["files", "sandbox"], action: () => setMainView("files") },
    ...rooms.map(c => ({ id: `ch-${c.id}`, group: "Channels", label: `#${c.name}`, icon: "#", keywords: [c.name], action: () => setChannel(c.id) })),
    ...allAgents.map(a => ({ id: `ag-${a.name}`, group: "Agents", label: `@${a.name}`, icon: "🤖", keywords: [a.name, a.job], action: () => setChannel(a.dm_channel_id) })),
  ], [rooms, allAgents]);

  // ── Render ──
  if (!user || !token) {
    return <LoginScreen onLogin={handleLogin} onSetup={handleSetup} status={wsStatus} demoMode={demoMode} />;
  }

  return (
    <div className={`app ${threadId ? "thread-open" : ""} ${computerOpen ? "computer-open" : ""}`}>
      <Sidebar
        user={user}
        channels={channels}
        agents={allAgents}
        teams={teams}
        onSelectChannel={setChannel}
        activeChannel={channel}
        wsStatus={wsStatus}
        meRole={meRole}
        onNewChannel={() => setQuickAction("channel")}
        onNewDM={() => setQuickAction("dm")}
        onNewGroup={() => setQuickAction("group")}
        onNewTeam={() => setQuickAction("team")}
        onNewAgent={() => setQuickAction("agent")}
        onLogout={handleLogout}
        onOpenSettings={() => setMainView("dashboard")}
      />

      <TopBar
        channel={current}
        agents={agents}
        onToggleComputer={() => setComputerOpen(o => !o)}
        computerOpen={computerOpen}
        onOpenCommandPalette={() => setCmdOpen(true)}
        onViewChange={setMainView}
        currentView={mainView}
        approvals={approvals}
        onResolveApproval={onResolveApproval}
      />

      {["dashboard", "workflows", "runs"].includes(mainView) && <CommandCenter token={token} agents={allAgents} flash={flash} onOpenRun={setSelectedRun} />}
      {mainView === "talk" && (
        <MessageList
          messages={messages}
          order={order}
          agents={agents}
          allAgents={allAgents}
          user={user}
          onReply={onReply}
          onReact={onReact}
          onDelete={onDelete}
          onOpenThread={onReply}
          replyCounts={replyCounts}
          reactions={reactions}
          channelId={channel}
          onLoadMore={async () => {
            if (loadingLog || order.length === 0) return;
            setLoadingLog(true);
            try {
              const history = await loadHistory(channelRef.current, order[0]);
              applyHistory(history, true);
            } catch { flash("Failed to load earlier messages", "error"); }
            finally { setLoadingLog(false); }
          }}
          hasMore={hasMore}
          loadingMore={loadingLog}
          typing={typing}
          groupedWith={(prev, m) => {
            if (!prev) return false;
            if (prev.author !== m.author) return false;
            if (prev.author_kind !== m.author_kind) return false;
            if (m.parent_id) return false;
            const dt = (m.created_at - prev.created_at) * 1000;
            return dt < 5 * 60 * 1000;
          }}
        />
      )}

      {mainView === "paper" && <PaperView messages={roots} title={current.name} />}
      {mainView === "files" && <FilesView computer={computer} />}
      {mainView === "agents" && <AgentsView agents={allAgents} onOpenChannel={(id) => setChannel(id)} />}

      {selectedRun && <RunMonitor token={token} run={selectedRun} onClose={() => setSelectedRun(null)} />}
      {quickAction && <QuickCreateModal action={quickAction} token={token} agents={allAgents} onClose={() => setQuickAction(null)} onCreated={async (id) => { setQuickAction(null); await loadChannels(); await loadAllAgents(); await loadTeams(); if (id) setChannel(id); flash("Created", "success"); }} />}

      {mainView === "talk" && <Composer
        onSend={(text) => sendMessage(text)}
        channelName={current.name}
        agents={agents}
        placeholder={`Message ${current.name}…`}
      />}

      {computerOpen && <ComputerPanel computer={computer} onClose={() => setComputerOpen(false)} onRefresh={loadComputer} />}

      {threadId && (
        <ThreadPanel
          parentId={threadId}
          messages={messages}
          threadReplies={threadReplies}
          allAgents={allAgents}
          onClose={() => setThreadId(null)}
          onSend={(text) => sendMessage(text, threadId)}
          onReact={onReact}
          reactions={reactions}
        />
      )}

      <CommandPalette open={cmdOpen} onClose={() => setCmdOpen(false)} commands={commands} onCommand={(c) => c.action?.()} />

      {toast && (
        <div className={`toast-container ${toast.type === "error" ? "error" : ""}`}>
          <div className={`toast ${toast.type}`}>
            {toast.title && <div className="toast-title">{toast.title}</div>}
            <div className="toast-message">{toast.msg}</div>
          </div>
        </div>
      )}
    </div>
  );
}

// ── Views ──

function PaperView({ messages, title }) {
  return (
    <div id="log" className="paper-log">
      <article className="paper-doc">
        <p className="paper-kicker">swarm preprint</p>
        <h1>{title}</h1>
        <p className="paper-meta">A compiled view of this channel · talk stays in Talk</p>
        <section>
          <h2>Code</h2>
          {messages.filter(m => m.body?.includes("```")).length === 0 && <p className="paper-empty">No code listings yet.</p>}
        </section>
        <section>
          <h2>Mathematics</h2>
          {messages.filter(m => m.body?.includes("$")).length === 0 && <p className="paper-empty">No TeX yet.</p>}
        </section>
      </article>
    </div>
  );
}

function FilesView({ computer }) {
  if (!computer) return <EmptyState icon="📁" title="No sandbox" message="Computer panel is off. Toggle it from the top bar." />;
  const files = computer.files || [];
  return (
    <div id="log" className="files-view">
      <div className="files-header">
        <span className="text-mono-xs text-subtle">{computer.cwd}</span>
      </div>
      <ScrollArea className="files-list">
        {files.map(f => (
          <div key={f.name} className="file-row">
            <span className="file-icon">{f.is_dir ? "📁" : "📄"}</span>
            <span className="file-name">{f.name}</span>
          </div>
        ))}
      </ScrollArea>
    </div>
  );
}

function AgentsView({ agents, onOpenChannel }) {
  return (
    <div id="log" className="agents-view">
      <ScrollArea className="agents-grid">
        {agents.map(a => (
          <Card key={a.name} interactive padded onClick={() => onOpenChannel(a.dm_channel_id)}>
            <div className="agent-card-head">
              <Avatar name={a.display_name || a.name} kind="agent" size="lg" />
              <div className="agent-card-meta">
                <span className="agent-card-name">{a.display_name || a.name}</span>
                <span className="agent-card-handle text-mono-xs text-subtle">@{a.name}</span>
              </div>
              <Badge variant={a.status === "working" ? "success" : a.status === "needs_approval" ? "warning" : "subtle"}>
                {statusLabel(a.status)}
              </Badge>
            </div>
            <p className="agent-card-job text-sm text-secondary">{a.job}</p>
            <p className="agent-card-model text-mono-xs text-subtle">{a.model}</p>
          </Card>
        ))}
      </ScrollArea>
    </div>
  );
}

function ThreadPanel({ parentId, messages, threadReplies, allAgents, onClose, onSend, onReact, reactions }) {
  const parent = messages[parentId];
  const replies = threadReplies.length > 0 ? threadReplies : Object.values(messages).filter(m => m.parent_id === parentId);
  return (
    <aside id="thread-panel">
      <div className="panel-header">
        <div className="panel-header-main">
          <span className="panel-icon">💬</span>
          <div>
            <h2 className="panel-title">Thread</h2>
            <span className="panel-subtitle text-mono-xs text-subtle">{replies.length} repl{replies.length === 1 ? "y" : "ies"}</span>
          </div>
        </div>
        <button className="panel-close" onClick={onClose} aria-label="Close">×</button>
      </div>
      <ScrollArea className="thread-body">
        {parent && (
          <div className="thread-parent">
            <MessageList messages={messages} order={[parentId]} agents={[]} allAgents={allAgents} user={{ handle: "you" }} onReply={() => {}} onReact={onReact} onDelete={() => {}} onOpenThread={() => {}} replyCounts={{}} reactions={reactions} channelId="" groupedWith={() => false} />
          </div>
        )}
        <Divider />
        {replies.map(r => (
          <div key={r.id} className="thread-reply">
            <Avatar name={r.author} kind={r.author_kind === "agent" ? "agent" : "human"} size="sm" />
            <div className="thread-reply-body">
              <div className="msg-meta"><span className="msg-author">{r.author}</span><span className="msg-time text-mono-xs text-subtle">{fmtTime(r.created_at)}</span></div>
              <RichBody body={r.body || ""} />
            </div>
          </div>
        ))}
      </ScrollArea>
      <Composer onSend={onSend} placeholder="Reply in thread…" compact threadParent />
    </aside>
  );
}

function QuickCreateModal({ action, token, agents, onClose, onCreated }) {
  const [name, setName] = useState("");
  const [detail, setDetail] = useState("");
  const [members, setMembers] = useState([]);
  const [busy, setBusy] = useState(false);
  const labels = { channel: "New channel", dm: "New direct message", group: "New group", team: "New agent team", agent: "New agent" };
  const needsMembers = action === "group" || action === "team";
  const needsDetail = action !== "dm";

  async function submit(event) {
    event.preventDefault();
    if (!name.trim() || (needsMembers && !members.length)) return;
    setBusy(true);
    let path = "/api/channels";
    let body = { name: name.trim() };
    if (action === "dm") { path = "/api/dms"; body = { handle: name.trim() }; }
    if (action === "group") { body = { name: name.trim(), topic: detail.trim(), kind: "group", members }; }
    if (action === "team") { path = "/api/teams"; body = { name: name.trim(), description: detail.trim(), members }; }
    if (action === "agent") { path = "/api/agents"; body = { name: name.trim(), display_name: name.trim(), system_prompt: detail.trim() || `You are ${name.trim()}, a helpful specialist teammate.`, job: "Teammate" }; }
    const response = await apiJson(path, { token, method: "POST", body });
    setBusy(false);
    if (response.ok) onCreated(response.data?.id || response.data?.dm_channel_id);
  }

  return <div className="quick-create-backdrop"><section className="quick-create"><header className="run-monitor-head"><div><span className="eyebrow">WORKSPACE</span><h2>{labels[action]}</h2></div><button className="panel-close" onClick={onClose}>×</button></header><form className="quick-create-form" onSubmit={submit}><Input autoFocus value={name} onChange={e => setName(e.target.value)} placeholder={action === "dm" ? "Person handle" : action === "agent" ? "Agent handle" : "Name"} />{needsDetail && <Textarea value={detail} onChange={e => setDetail(e.target.value)} placeholder={action === "agent" ? "What should this agent specialize in?" : "Description or topic (optional)"} rows={3} />}{needsMembers && <label className="quick-members">{agents.map(agent => <span key={agent.name}><input type="checkbox" checked={members.includes(agent.name)} onChange={e => setMembers(value => e.target.checked ? [...value, agent.name] : value.filter(item => item !== agent.name))} /> {agent.display_name || agent.name}</span>)}</label>}<footer className="workflow-editor-actions"><Button variant="ghost" type="button" onClick={onClose}>Cancel</Button><Button variant="primary" type="submit" disabled={busy || !name.trim() || (needsMembers && !members.length)}>{busy ? "Creating…" : "Create"}</Button></footer></form></section></div>;
}
