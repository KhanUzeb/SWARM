import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import katex from "katex";
import ApiConfigStep from "./ai-support/ApiConfigStep.jsx";
import ProviderPanel from "./ai-support/ProviderPanel.jsx";
import ToolsPanel from "./ai-support/ToolsPanel.jsx";
import {
  ALL_TOOLS, CACHE_TTL, DEFAULT_MODEL, EMOJI, HISTORY_LIMIT, api, apiJson, authHeaders, botLabel, bustCache,
  escapeHtml, extractPaper, fmtTime, formatInline, groupedWith, initials, insertMention,
  mentionQuery, slugFromName, statusLabel, tokenizeBody,
} from "./lib.js";

function renderMath(tex, display) {
  try {
    return katex.renderToString(tex, { throwOnError: false, displayMode: !!display });
  } catch {
    return escapeHtml(tex);
  }
}

function CodeBlock({ lang, text }) {
  const [copied, setCopied] = useState(false);
  return (
    <div className="code-block">
      <div className="code-head">
        <span>{lang || "text"}</span>
        <button type="button" className="btn ghost" onClick={async () => {
          try {
            await navigator.clipboard.writeText(text);
            setCopied(true);
            setTimeout(() => setCopied(false), 1400);
          } catch { /* ignore */ }
        }}>{copied ? "Copied" : "Copy"}</button>
      </div>
      <pre><code>{text}</code></pre>
    </div>
  );
}

function RichBody({ body }) {
  const parts = useMemo(() => tokenizeBody(body), [body]);
  return (
    <div className="body rich">
      {parts.map((part, i) => {
        if (part.type === "code") return <CodeBlock key={i} lang={part.lang} text={part.text} />;
        if (part.type === "math") {
          return (
            <span
              key={i}
              className={part.display ? "math-display" : "math-inline"}
              dangerouslySetInnerHTML={{ __html: renderMath(part.tex, part.display) }}
            />
          );
        }
        return <span key={i} dangerouslySetInnerHTML={{ __html: formatInline(part.text) }} />;
      })}
    </div>
  );
}

function PaperView({ title, messages }) {
  const paper = useMemo(() => extractPaper(messages), [messages]);
  return (
    <article className="paper-doc" aria-label="LaTeX paper">
      <p className="paper-kicker">swarm preprint</p>
      <h1>{title}</h1>
      <p className="paper-meta">A compiled view of this channel · talk stays in Talk</p>

      <section>
        <h2><span className="tex-cmd">{"\\subsection*{Code}"}</span> Code</h2>
        {!paper.code.length && <p className="paper-empty">No listings yet. Ask @coder — they write fenced programs into this subsection.</p>}
        {paper.code.map((block, i) => (
          <figure key={`${block.id}-${i}`} className="listing">
            <figcaption>Listing {i + 1} · {block.lang} · {block.author}</figcaption>
            <CodeBlock lang={block.lang} text={block.text} />
          </figure>
        ))}
      </section>

      <section>
        <h2><span className="tex-cmd">{"\\subsection*{Mathematics}"}</span> Mathematics</h2>
        {!paper.math.length && <p className="paper-empty">No TeX yet. Inline $...$ or display $$...$$ from @coder lands here.</p>}
        {paper.math.map((m, i) => (
          <div
            key={`${m.id}-${i}`}
            className="math-display paper-math"
            dangerouslySetInnerHTML={{ __html: renderMath(m.tex, true) }}
          />
        ))}
      </section>
    </article>
  );
}

function Toast({ toast }) {
  if (!toast) return null;
  return <div id="toast" className={`visible${toast.error ? " error" : ""}`} role="status">{toast.msg}</div>;
}

function MessageRow({ m, grouped, inThread, reactions, replyCount, onReply, onReact, onOpenThread, onToggleEmoji, onDelete, label }) {
  const counts = {};
  for (const r of reactions || []) counts[r.emoji] = (counts[r.emoji] || 0) + 1;
  return (
    <div className={`row ${m.author_kind || "human"}${grouped ? " grouped" : ""}${m.streaming ? " streaming" : ""}`}>
      <div className="row-main">
        <div className="avatar">{initials(label || m.author)}</div>
        <div className="content">
          <div className="meta">
            <span className="who">{label || m.author}</span>
            {m.author_kind === "agent" && <span className="badge">{m.author === "coder" ? "code" : "agent"}</span>}
            <span className="ts">{fmtTime(m.created_at)}</span>
          </div>
          <RichBody body={m.body || ""} />
        </div>
      </div>
      {m.author_kind !== "system" && !m.streaming && m.id != null && (
        <div className="row-actions">
          <button type="button" onClick={() => onReply(m.parent_id || m.id)}>Reply</button>
          <button type="button" onClick={(ev) => onToggleEmoji(m.id, ev.currentTarget)}>React</button>
          {onDelete && <button type="button" className="danger" onClick={() => onDelete(m)}>Delete</button>}
        </div>
      )}
      {!m.streaming && m.id != null && (
        <div className="reactions">
          {Object.entries(counts).map(([emoji, count]) => (
            <button key={emoji} type="button" className="reaction-chip" onClick={() => onReact(m.id, emoji)}>
              {emoji} {count}
            </button>
          ))}
        </div>
      )}
      {!inThread && !m.parent_id && !m.streaming && replyCount > 0 && (
        <button type="button" className="thread-count" onClick={() => onOpenThread(m.id)}>
          {replyCount === 1 ? "1 reply" : `${replyCount} replies`}
        </button>
      )}
    </div>
  );
}

function ApprovalCard({ a, onResolve }) {
  return (
    <div className="approval-card">
      <div className="who">{a.agent_name} needs approval</div>
      <p>{a.detail ? `${a.action} — ${a.detail}` : a.action}</p>
      <div className="actions">
        <button type="button" className="btn primary" onClick={() => onResolve(a.id, "approved")}>Allow once</button>
        <button type="button" className="btn" onClick={() => onResolve(a.id, "denied")}>Deny</button>
      </div>
    </div>
  );
}

export default function App() {
  const [user, setUser] = useState(null);
  const [token, setToken] = useState(null);
  const [channel, setChannel] = useState("dm-swarm");
  const [channels, setChannels] = useState([]);
  const [agents, setAgents] = useState([]);
  const [allAgents, setAllAgents] = useState([]);
  const [jobs, setJobs] = useState([]);
  const [skills, setSkills] = useState([]);
  const [routines, setRoutines] = useState([]);
  const [approvals, setApprovals] = useState([]);
  const [computer, setComputer] = useState(null);
  const [filePreview, setFilePreview] = useState("");
  const [panelTab, setPanelTab] = useState("files");
  const [messages, setMessages] = useState({});
  const [order, setOrder] = useState([]);
  const [replyCounts, setReplyCounts] = useState({});
  const [reactions, setReactions] = useState({});
  const [streams, setStreams] = useState({});
  const [hasMore, setHasMore] = useState(false);
  const [loadingLog, setLoadingLog] = useState(false);
  const [logError, setLogError] = useState("");
  const [threadId, setThreadId] = useState(null);
  const [threadParent, setThreadParent] = useState(null);
  const [threadReplies, setThreadReplies] = useState([]);
  const [wsStatus, setWsStatus] = useState("offline");
  const [agentsReady, setAgentsReady] = useState(false);
  const [toast, setToast] = useState(null);
  const [loginErr, setLoginErr] = useState("");
  const [loginBusy, setLoginBusy] = useState(false);
  const [onboarding, setOnboarding] = useState(null);
  const [demoMode, setDemoMode] = useState(false);
  const [toolCatalog, setToolCatalog] = useState([]);
  const [pluginList, setPluginList] = useState([]);
  const [handleDraft, setHandleDraft] = useState(() => localStorage.getItem("swarm_last_handle") || "");
  const [computerOpen, setComputerOpen] = useState(() => localStorage.getItem("swarm_computer") !== "0");
  const [showTools, setShowTools] = useState(() => localStorage.getItem("swarm_show_tools") === "1");
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [channelModal, setChannelModal] = useState(null);
  const [groupMembers, setGroupMembers] = useState([]);
  const [handleTouched, setHandleTouched] = useState(false);
  const [agentModal, setAgentModal] = useState(null);
  const [mainView, setMainView] = useState("talk");
  const [mention, setMention] = useState({ open: false, index: 0, items: [] });
  const [emoji, setEmoji] = useState(null);
  const [draft, setDraft] = useState("");
  const [threadDraft, setThreadDraft] = useState("");
  const [typing, setTyping] = useState("");
  const [channelErr, setChannelErr] = useState("");
  const [agentErr, setAgentErr] = useState("");
  const [agentForm, setAgentForm] = useState({
    name: "", display_name: "", job: "", prompt: "", model: DEFAULT_MODEL, scope: "", window: 12, tools: ALL_TOOLS, memories: [],
  });
  const [meRole, setMeRole] = useState("member");
  const [people, setPeople] = useState([]);
  const [teams, setTeams] = useState([]);
  const [peopleModal, setPeopleModal] = useState(false);
  const [teamModal, setTeamModal] = useState(false);
  const [teamForm, setTeamForm] = useState({ id: "", name: "", description: "", members: [] });
  const [teamErr, setTeamErr] = useState("");
  const [searchQ, setSearchQ] = useState("");
  const [searchHits, setSearchHits] = useState(null);
  const [searchBusy, setSearchBusy] = useState(false);
  const isAdmin = meRole === "admin";

  const wsRef = useRef(null);
  const wsGen = useRef(0);
  const lastSeenId = useRef(0);
  const reconnectAttempt = useRef(0);
  const reconnectTimer = useRef(null);
  const intentionalClose = useRef(false);
  const logRef = useRef(null);
  const threadLogRef = useRef(null);
  const inputRef = useRef(null);
  const threadInputRef = useRef(null);
  const toastTimer = useRef(null);
  const typingTimer = useRef(null);
  const channelRef = useRef(channel);
  const tokenRef = useRef(token);
  channelRef.current = channel;
  tokenRef.current = token;

  const current = channels.find((c) => c.id === channel) || { id: channel, name: channel, topic: "" };
  const bot = allAgents.find((a) => a.dm_channel_id === channel);
  const rooms = channels.filter((c) => c.kind !== "dm" && c.kind !== "group" && c.kind !== "people");
  const groups = channels.filter((c) => c.kind === "group");
  const peopleDms = channels.filter((c) => c.kind === "people");
  const group = current.kind === "group" ? current : null;
  const peopleChat = current.kind === "people" ? current : null;
  const roots = order.map((id) => messages[id]).filter(Boolean);
  const pendingHere = approvals.filter((a) => a.status === "pending" && a.channel_id === channel);
  const pendingAll = approvals.filter((a) => a.status === "pending");
  const labelFor = (name) => {
    const a = allAgents.find((x) => x.name === name);
    return a ? botLabel(a) : name;
  };

  const flash = useCallback((msg, error = false) => {
    setToast({ msg, error });
    clearTimeout(toastTimer.current);
    toastTimer.current = setTimeout(() => setToast(null), 3200);
  }, []);

  const remember = useCallback((m) => {
    if (m.id && m.id > lastSeenId.current) lastSeenId.current = m.id;
    setMessages((prev) => ({ ...prev, [m.id]: m }));
    setReactions((prev) => {
      if (m.reactions) return { ...prev, [m.id]: m.reactions.slice() };
      if (prev[m.id]) return prev;
      return { ...prev, [m.id]: [] };
    });
  }, []);

  const ingestLive = useCallback((raw) => {
    const m = { reactions: [], ...raw };
    setMessages((prev) => {
      if (prev[m.id]) return prev;
      return { ...prev, [m.id]: m };
    });
    if (m.id && m.id > lastSeenId.current) lastSeenId.current = m.id;
    setReactions((prev) => (prev[m.id] ? prev : { ...prev, [m.id]: m.reactions || [] }));
    if (m.parent_id) {
      setReplyCounts((prev) => ({ ...prev, [m.parent_id]: (prev[m.parent_id] || 0) + 1 }));
      setThreadId((tid) => {
        if (tid === m.parent_id) setThreadReplies((list) => list.some((x) => x.id === m.id) ? list : [...list, m]);
        return tid;
      });
      return;
    }
    if (m.author_kind === "agent") {
      setStreams((prev) => {
        const next = { ...prev };
        delete next[m.author];
        return next;
      });
      setTyping("");
    }
    setOrder((prev) => (prev.includes(m.id) ? prev : [...prev, m.id]));
    if (m.author_kind === "system") loadComputer();
  }, []);

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
    } catch {
      setAllAgents([]);
    }
  }

  async function loadAgents(channelId) {
    if (!tokenRef.current) return;
    try {
      const path = `/api/agents?channel_id=${encodeURIComponent(channelId)}`;
      const res = await apiJson(path, { token: tokenRef.current, cacheTtl: CACHE_TTL.list });
      setAgents(res.ok ? res.data : []);
    } catch {
      setAgents([]);
    }
  }

  async function loadGroqStatus() {
    try {
      const pub = await apiJson("/api/status", { cacheTtl: CACHE_TTL.status });
      const authed = tokenRef.current
        ? await apiJson("/api/status", { token: tokenRef.current, cacheTtl: CACHE_TTL.status })
        : pub;
      const data = authed.ok ? authed.data : (pub.data || {});
      setDemoMode(!!data.demo);
      setAgentsReady(!!(data.llm_ready || data.groq || data.openrouter || data.demo));
      if (data.me?.role) setMeRole(data.me.role);
    } catch {
      setDemoMode(false);
      setAgentsReady(false);
    }
  }

  async function loadToolCatalog() {
    if (!tokenRef.current) return;
    try {
      const res = await apiJson("/api/tools", { token: tokenRef.current, cacheTtl: CACHE_TTL.catalog });
      if (!res.ok) return;
      setToolCatalog(res.data.tools || []);
      setPluginList(res.data.plugins || []);
    } catch {
      setToolCatalog([]);
      setPluginList([]);
    }
  }

  async function loadJobs() {
    if (!tokenRef.current) return;
    try {
      const res = await apiJson("/api/jobs", { token: tokenRef.current, cacheTtl: CACHE_TTL.catalog });
      setJobs(res.ok ? res.data : []);
    } catch {
      setJobs([]);
    }
  }

  async function loadSkills() {
    if (!tokenRef.current) return;
    try {
      const res = await apiJson("/api/skills", { token: tokenRef.current, cacheTtl: CACHE_TTL.list });
      setSkills(res.ok ? res.data : []);
    } catch {
      setSkills([]);
    }
  }

  async function loadRoutines() {
    if (!tokenRef.current) return;
    try {
      const res = await apiJson("/api/routines", { token: tokenRef.current, cacheTtl: CACHE_TTL.list });
      setRoutines(res.ok ? res.data : []);
    } catch {
      setRoutines([]);
    }
  }

  async function loadApprovals() {
    if (!tokenRef.current) return;
    try {
      const res = await apiJson("/api/approvals?status=pending", { token: tokenRef.current, cacheTtl: 10_000 });
      setApprovals(res.ok ? res.data : []);
    } catch {
      setApprovals([]);
    }
  }

  async function loadComputer() {
    if (!tokenRef.current) return;
    try {
      const res = await apiJson("/api/computer", { token: tokenRef.current, cacheTtl: CACHE_TTL.list });
      setComputer(res.ok ? res.data : null);
    } catch {
      setComputer(null);
    }
  }

  async function loadPeople() {
    if (!tokenRef.current) return;
    try {
      const res = await apiJson("/api/people", { token: tokenRef.current, cacheTtl: 8_000 });
      setPeople(res.ok ? res.data : []);
    } catch {
      setPeople([]);
    }
  }

  async function loadTeams() {
    if (!tokenRef.current) return;
    try {
      const res = await apiJson("/api/teams", { token: tokenRef.current, cacheTtl: CACHE_TTL.list });
      setTeams(res.ok ? res.data : []);
    } catch {
      setTeams([]);
    }
  }

  async function loadHistory(channelId, beforeId) {
    if (!tokenRef.current) throw new Error("auth");
    const params = new URLSearchParams({ limit: String(HISTORY_LIMIT) });
    if (beforeId) params.set("before_id", String(beforeId));
    const res = await apiJson(`/api/channels/${channelId}/messages?${params}`, {
      token: tokenRef.current,
    });
    if (!res.ok) throw new Error("history");
    return res.data;
  }

  function applyHistory(history, prepend = false) {
    setHasMore(history.length === HISTORY_LIMIT);
    const nextMsgs = {};
    const nextReact = {};
    const counts = {};
    for (const m of history) {
      nextMsgs[m.id] = m;
      nextReact[m.id] = m.reactions ? m.reactions.slice() : [];
      if (m.parent_id) counts[m.parent_id] = (counts[m.parent_id] || 0) + 1;
      if (m.id > lastSeenId.current) lastSeenId.current = m.id;
    }
    const rootsPage = history.filter((m) => !m.parent_id).map((m) => m.id);
    if (!prepend) {
      setMessages(nextMsgs);
      setReactions(nextReact);
      setReplyCounts(counts);
      setStreams({});
      setOrder(rootsPage);
      return;
    }
    setMessages((prev) => ({ ...nextMsgs, ...prev }));
    setReactions((prev) => ({ ...nextReact, ...prev }));
    setReplyCounts((prev) => {
      const merged = { ...prev };
      for (const [k, v] of Object.entries(counts)) merged[k] = (merged[k] || 0) + v;
      return merged;
    });
    setOrder((prev) => [...rootsPage.filter((id) => !prev.includes(id)), ...prev]);
  }

  async function switchChannel(id) {
    setChannel(id);
    setThreadId(null);
    setThreadParent(null);
    setThreadReplies([]);
    lastSeenId.current = 0;
    setMessages({});
    setReactions({});
    setReplyCounts({});
    setStreams({});
    setOrder([]);
    setSidebarOpen(false);
    setMainView("talk");
    setLoadingLog(true);
    setLogError("");
    setHasMore(false);
    try {
      const history = await loadHistory(id);
      applyHistory(history);
    } catch {
      setLogError("Couldn't load messages.");
      flash("Couldn't load channel history", true);
    }
    setLoadingLog(false);
    await Promise.all([loadAgents(id), loadGroqStatus(), loadApprovals(), loadComputer()]);
  }

  function connectWs(channelId, tok) {
    if (reconnectTimer.current) {
      clearTimeout(reconnectTimer.current);
      reconnectTimer.current = null;
    }
    if (wsRef.current) {
      intentionalClose.current = true;
      wsRef.current.close();
    }
    const gen = ++wsGen.current;
    intentionalClose.current = false;
    setWsStatus("connecting");
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(`${proto}://${location.host}/ws/${channelId}`);
    ws.onopen = () => {
      if (gen !== wsGen.current) {
        ws.close();
        return;
      }
      const payload = { token: tok };
      if (lastSeenId.current) payload.last_seen_id = lastSeenId.current;
      ws.send(JSON.stringify(payload));
      setWsStatus("online");
      reconnectAttempt.current = 0;
    };
    ws.onmessage = (ev) => {
      if (gen !== wsGen.current) return;
      let data;
      try { data = JSON.parse(ev.data); } catch { return; }
      if (data.type === "message") ingestLive(data.message);
      else if (data.type === "typing") {
        setTyping(`${data.author} is typing…`);
        clearTimeout(typingTimer.current);
        typingTimer.current = setTimeout(() => setTyping((t) => (t.startsWith(data.author) ? "" : t)), 15000);
      } else if (data.type === "reaction") {
        setReactions((prev) => {
          const list = (prev[data.message_id] || []).slice();
          if (!list.some((r) => r.author === data.author && r.emoji === data.emoji)) {
            list.push({ author: data.author, emoji: data.emoji });
          }
          return { ...prev, [data.message_id]: list };
        });
      } else if (data.type === "message_deleted") {
        const ids = new Set(data.ids || [data.message_id]);
        setMessages((prev) => {
          const next = { ...prev };
          for (const id of ids) delete next[id];
          return next;
        });
        setOrder((prev) => prev.filter((id) => !ids.has(id)));
        setReactions((prev) => {
          const next = { ...prev };
          for (const id of ids) delete next[id];
          return next;
        });
        setThreadId((tid) => {
          if (tid && ids.has(tid)) {
            setThreadParent(null);
            setThreadReplies([]);
            return null;
          }
          setThreadReplies((list) => list.filter((m) => !ids.has(m.id)));
          return tid;
        });
      } else if (data.type === "channel_deleted") {
        loadChannels().then((chs) => {
          if (channelRef.current === data.channel_id) {
            const roomsLeft = (chs || []).filter((c) => c.kind !== "dm");
            switchChannel(roomsLeft[0]?.id || chs?.[0]?.id || "general");
          }
        });
      } else if (data.type === "error") flash(data.detail || "Something went wrong", true);
      else if (data.type === "agent_stream_start") {
        setStreams((prev) => ({
          ...prev,
          [data.author]: { author: data.author, author_kind: "agent", body: "", streaming: true, created_at: Date.now() / 1000 },
        }));
      } else if (data.type === "agent_token") {
        setStreams((prev) => {
          const cur = prev[data.author] || {
            author: data.author, author_kind: "agent", body: "", streaming: true, created_at: Date.now() / 1000,
          };
          return { ...prev, [data.author]: { ...cur, body: (cur.body || "") + (data.delta || "") } };
        });
      } else if (data.type === "bot_status") {
        setAllAgents((list) => list.map((a) => (a.name === data.name ? { ...a, status: data.status } : a)));
      } else if (data.type === "bot_archived") {
        loadAllAgents();
      } else if (data.type === "presence") {
        setPeople((list) => list.map((p) => (p.handle === data.handle ? { ...p, online: !!data.online } : p)));
      } else if (data.type === "approval") {
        setApprovals((list) => {
          const i = list.findIndex((a) => a.id === data.approval?.id);
          if (i >= 0) {
            const next = list.slice();
            next[i] = data.approval;
            return next;
          }
          return data.approval ? [data.approval, ...list] : list;
        });
      }
    };
    ws.onclose = (ev) => {
      if (gen !== wsGen.current) return;
      setWsStatus("offline");
      if (ev.code === 4001) {
        flash("Session expired — pick a handle again", true);
        logout({ skipClose: true });
        return;
      }
      if (!intentionalClose.current && tokenRef.current && channelRef.current === channelId) {
        const delay = Math.min(15000, 1000 * (2 ** reconnectAttempt.current));
        reconnectAttempt.current += 1;
        flash("Disconnected, reconnecting…");
        reconnectTimer.current = setTimeout(() => connectWs(channelId, tokenRef.current), delay);
      }
    };
    ws.onerror = () => {};
    wsRef.current = ws;
  }

  useEffect(() => {
    if (!token || !channel) return;
    connectWs(channel, token);
    return () => {
      intentionalClose.current = true;
      wsRef.current?.close();
    };
  }, [token, channel]);

  useEffect(() => {
    const node = logRef.current;
    if (node) node.scrollTop = node.scrollHeight;
  }, [order, streams, loadingLog]);

  useEffect(() => {
    const node = threadLogRef.current;
    if (node) node.scrollTop = node.scrollHeight;
  }, [threadReplies, threadParent]);

  useEffect(() => {
    localStorage.setItem("swarm_computer", computerOpen ? "1" : "0");
  }, [computerOpen]);

  useEffect(() => {
    localStorage.setItem("swarm_show_tools", showTools ? "1" : "0");
  }, [showTools]);

  async function enterWorkspace(handle, tok, preferred, { suggestedDraft } = {}) {
    setToken(tok);
    setUser(handle);
    localStorage.setItem(`swarm_token_${handle}`, tok);
    localStorage.setItem("swarm_last_handle", handle);
    const [chs] = await Promise.all([
      loadChannels(), loadAllAgents(), loadJobs(), loadSkills(), loadRoutines(),
      loadToolCatalog(), loadPeople(), loadTeams(), loadGroqStatus(),
    ]);
    const next = (chs || []).some((c) => c.id === (preferred || "dm-swarm"))
      ? (preferred || "dm-swarm")
      : (chs[0]?.id || "general");
    await switchChannel(next);
    if (suggestedDraft) setDraft(suggestedDraft);
  }

  useEffect(() => {
    const saved = localStorage.getItem("swarm_last_handle");
    const tok = saved && localStorage.getItem(`swarm_token_${saved}`);
    if (saved && tok) {
      enterWorkspace(saved, tok).catch(() => {});
    }
  }, []);

  useEffect(() => {
    const onDoc = (ev) => {
      if (!ev.target.closest("#emoji-pop") && !ev.target.closest(".row-actions")) {
        setEmoji(null);
      }
    };
    document.addEventListener("click", onDoc);
    return () => document.removeEventListener("click", onDoc);
  }, []);

  function logout({ skipClose } = {}) {
    if (reconnectTimer.current) clearTimeout(reconnectTimer.current);
    intentionalClose.current = true;
    if (!skipClose) wsRef.current?.close();
    wsRef.current = null;
    setToken(null);
    setUser(null);
    setWsStatus("offline");
  }

  async function onLogin(ev) {
    ev.preventDefault();
    const handle = handleDraft.trim();
    if (!handle) return;
    setLoginErr("");
    setLoginBusy(true);
    try {
      const res = await fetch("/api/register", {
        method: "POST",
        headers: authHeaders(null),
        body: JSON.stringify({ handle }),
      });
      let tok = null;
      if (res.ok) {
        const body = await res.json();
        tok = body.token;
        setToken(tok);
        tokenRef.current = tok;
        setUser(handle);
        setMeRole(body.role || "member");
        localStorage.setItem(`swarm_token_${handle}`, tok);
        localStorage.setItem("swarm_last_handle", handle);
        if (body.role !== "admin") {
          await enterWorkspace(handle, tok);
        } else {
          await Promise.all([loadChannels(), loadAllAgents(), loadGroqStatus()]);
          const templatesRes = await apiJson("/api/jobs", { token: tok, cacheTtl: CACHE_TTL.catalog });
          const templates = templatesRes.ok ? templatesRes.data : [];
          setJobs(templates);
          const pick = templates.find((j) => j.id === "chief-of-staff") || templates[0] || null;
          setOnboarding({
            step: 1,
            template: pick,
            name: pick?.suggested_name || "",
            displayName: pick?.suggested_name
              ? pick.suggested_name.replace(/[-_]/g, " ").replace(/\b\w/g, (c) => c.toUpperCase())
              : "",
            err: "",
            busy: false,
          });
        }
      } else if (res.status === 409) {
        tok = localStorage.getItem(`swarm_token_${handle}`);
        if (!tok) setLoginErr("handle taken and no saved session — pick another");
        else await enterWorkspace(handle, tok);
      } else setLoginErr("registration failed");
    } catch {
      setLoginErr("couldn't reach the relay");
    }
    setLoginBusy(false);
  }

  async function skipOnboarding() {
    if (!user || !token) return;
    setOnboarding(null);
    await enterWorkspace(user, token);
  }

  async function createOnboardingBot(ev) {
    ev.preventDefault();
    if (!onboarding?.template || !token) return;
    const displayName = (onboarding.displayName || onboarding.name || "").trim();
    const name = onboarding.name.trim() || slugFromName(displayName);
    const tpl = onboarding.template;
    if (!displayName) {
      setOnboarding((o) => ({ ...o, err: "give them a name" }));
      return;
    }
    if (!name || !/^[A-Za-z0-9_\-]+$/.test(name)) {
      setOnboarding((o) => ({ ...o, err: "mention handle — letters, numbers, _ or -" }));
      return;
    }
    setOnboarding((o) => ({ ...o, busy: true, err: "" }));
    try {
      const res = await api("/api/agents", {
        token,
        method: "POST",
        body: {
          name,
          display_name: displayName,
          system_prompt: tpl.prompt,
          job: tpl.job,
          model: DEFAULT_MODEL,
          channel_scope: null,
          history_window: 12,
          max_tool_calls: 3,
          tools: ALL_TOOLS,
        },
      });
      if (!res.ok) {
        const msg = res.status === 409 ? "that name is taken — try another" : "couldn't create bot";
        setOnboarding((o) => ({ ...o, busy: false, err: msg }));
        return;
      }
      const created = await res.json();
      setOnboarding(null);
      await enterWorkspace(user, token, created.dm_channel_id, {
        suggestedDraft: tpl.suggested_prompt || "",
      });
      flash(`${displayName} is ready — say hi in their 1:1`);
    } catch {
      setOnboarding((o) => ({ ...o, busy: false, err: "couldn't create bot" }));
    }
  }

  function sendFrom(body, parentId) {
    const text = body.trim();
    if (!text) return;
    const ws = wsRef.current;
    if (!ws || ws.readyState !== WebSocket.OPEN) {
      flash("Not connected — wait a moment and try again", true);
      return;
    }
    if (!agentsReady && /@[a-zA-Z0-9_\-]+/.test(text)) {
      flash("Bots can't reply until GROQ_API_KEY is set (or SWARM_DEMO=1)", true);
    }
    const payload = { body: text };
    if (parentId) payload.parent_id = parentId;
    ws.send(JSON.stringify(payload));
    setMention({ open: false, index: 0, items: [] });
  }

  function onComposerInput(value, caret) {
    setDraft(value);
    const q = mentionQuery(value, caret);
    if (!q) {
      setMention({ open: false, index: 0, items: [] });
      return;
    }
    const pool = q.mode === "at"
      ? [
          ...(agents.length ? agents : allAgents)
            .filter((a) => a.name.toLowerCase().startsWith(q.query))
            .map((a) => ({
              label: botLabel(a) !== a.name ? `@${a.name} · ${botLabel(a)}` : `@${a.name}`,
              value: a.name,
              kind: "at",
            })),
          ...teams
            .filter((t) => t.id.toLowerCase().startsWith(q.query) || (t.name || "").toLowerCase().startsWith(q.query))
            .map((t) => ({
              label: `@${t.id} · team`,
              value: t.id,
              kind: "at",
            })),
        ]
      : skills
          .filter((s) => s.name.toLowerCase().startsWith(q.query))
          .map((s) => ({ label: `/${s.name}`, value: s.name, kind: "slash" }));
    setMention({ open: pool.length > 0, index: 0, items: pool });
  }

  function applyMention(item) {
    const el = inputRef.current;
    const start = el?.selectionStart ?? draft.length;
    const end = el?.selectionEnd ?? draft.length;
    const next = insertMention(draft, start, end, item.value, item.kind);
    setDraft(next.value);
    setMention({ open: false, index: 0, items: [] });
    requestAnimationFrame(() => {
      if (el) {
        el.focus();
        el.setSelectionRange(next.pos, next.pos);
      }
    });
  }

  function onComposerKey(ev) {
    if (mention.open && mention.items.length) {
      if (ev.key === "ArrowDown") {
        ev.preventDefault();
        setMention((m) => ({ ...m, index: (m.index + 1) % m.items.length }));
        return;
      }
      if (ev.key === "ArrowUp") {
        ev.preventDefault();
        setMention((m) => ({ ...m, index: (m.index - 1 + m.items.length) % m.items.length }));
        return;
      }
      if (ev.key === "Enter" || ev.key === "Tab") {
        ev.preventDefault();
        applyMention(mention.items[mention.index]);
        return;
      }
      if (ev.key === "Escape") {
        ev.preventDefault();
        setMention({ open: false, index: 0, items: [] });
        return;
      }
    }
    if (ev.key === "Enter" && !ev.shiftKey) {
      ev.preventDefault();
      sendFrom(draft, null);
      setDraft("");
    }
  }

  async function sendReaction(messageId, emojiChar) {
    setEmoji(null);
    try {
      const res = await api(`/api/messages/${messageId}/reactions`, {
        token, method: "POST", body: { author: user, emoji: emojiChar },
      });
      if (!res.ok) flash("Couldn't add reaction", true);
    } catch {
      flash("Couldn't add reaction", true);
    }
  }

  async function openThread(id) {
    setThreadId(id);
    try {
      const res = await apiJson(`/api/messages/${id}/thread`, { token: tokenRef.current });
      if (!res.ok) throw new Error("thread");
      const data = res.data;
      remember(data.parent);
      data.replies.forEach(remember);
      setThreadParent(data.parent);
      setThreadReplies(data.replies);
      requestAnimationFrame(() => threadInputRef.current?.focus());
    } catch {
      flash("Couldn't load thread", true);
    }
  }

  async function createChannel(ev) {
    ev.preventDefault();
    const fd = new FormData(ev.target);
    const name = String(fd.get("name") || "").trim();
    const topic = String(fd.get("topic") || "").trim();
    const kind = channelModal === "group" ? "group" : "room";
    setChannelErr("");
    if (!name) return;
    if (kind === "group" && groupMembers.length < 1) {
      setChannelErr("pick at least one bot");
      return;
    }
    try {
      const res = await api("/api/channels", {
        token,
        method: "POST",
        body: { name, topic, kind, members: kind === "group" ? groupMembers : [] },
      });
      if (res.ok) {
        const c = await res.json();
        setChannelModal(null);
        setGroupMembers([]);
        await loadChannels();
        switchChannel(c.id);
      } else if (res.status === 409) setChannelErr("that name already exists");
      else if (res.status === 400) setChannelErr("pick at least one bot");
      else setChannelErr(kind === "group" ? "couldn't create group" : "couldn't create channel");
    } catch {
      setChannelErr(kind === "group" ? "couldn't create group" : "couldn't create channel");
    }
  }

  async function deleteChannel(channelId) {
    const res = await api(`/api/channels/${encodeURIComponent(channelId)}`, { token, method: "DELETE", json: false });
    if (!res.ok) {
      flash("Couldn't delete that channel", true);
      return;
    }
    const chs = await loadChannels();
    flash("Channel deleted");
    if (channel === channelId) {
      const roomsLeft = (chs || []).filter((c) => c.kind !== "dm");
      await switchChannel(roomsLeft[0]?.id || chs?.[0]?.id || "general");
    }
  }

  async function deleteMessage(m) {
    if (!m?.id) return;
    const res = await api(`/api/messages/${m.id}`, { token, method: "DELETE", json: false });
    if (!res.ok) {
      flash("Couldn't delete that message", true);
      return;
    }
    const data = await res.json();
    const ids = new Set(data.ids || [m.id]);
    setMessages((prev) => {
      const next = { ...prev };
      for (const id of ids) delete next[id];
      return next;
    });
    setOrder((prev) => prev.filter((id) => !ids.has(id)));
    setReactions((prev) => {
      const next = { ...prev };
      for (const id of ids) delete next[id];
      return next;
    });
    if (threadId && ids.has(threadId)) {
      setThreadId(null);
      setThreadParent(null);
      setThreadReplies([]);
    } else {
      setThreadReplies((list) => list.filter((row) => !ids.has(row.id)));
    }
  }

  function openCreateAgent() {
    if (!isAdmin) {
      flash("Only admins can create bots", true);
      return;
    }
    setAgentErr("");
    setHandleTouched(false);
    setAgentForm({ name: "", display_name: "", job: "", prompt: "", model: DEFAULT_MODEL, scope: "", window: 12, tools: ALL_TOOLS, memories: [] });
    setAgentModal("create");
  }

  async function openAgentPanel(name) {
    setAgentErr("");
    try {
      const res = await apiJson(`/api/agents/${encodeURIComponent(name)}`, { token: tokenRef.current });
      if (!res.ok) throw new Error("agent");
      const a = res.data;
      setAgentForm({
        name: a.name, display_name: a.display_name || a.name, job: a.job || "", prompt: a.system_prompt || "", model: a.model || DEFAULT_MODEL,
        scope: a.channel_scope || "", window: a.history_window || 12, tools: a.tools || ALL_TOOLS, memories: a.memories || [],
      });
      setAgentModal("edit");
    } catch {
      flash("Couldn't load that agent", true);
    }
  }

  async function saveAgent(ev) {
    ev.preventDefault();
    setAgentErr("");
    const display_name = (agentForm.display_name || "").trim();
    const name = (agentForm.name || slugFromName(display_name)).trim();
    const system_prompt = agentForm.prompt.trim();
    if (!display_name || !system_prompt) {
      setAgentErr("name and prompt are required");
      return;
    }
    if (!name || !/^[A-Za-z0-9_\-]+$/.test(name)) {
      setAgentErr("mention handle — letters, numbers, _ or -");
      return;
    }
    const payload = {
      display_name,
      system_prompt,
      model: agentForm.model.trim() || DEFAULT_MODEL,
      channel_scope: agentForm.scope || null,
      history_window: Number(agentForm.window) || 12,
      max_tool_calls: 3,
      tools: agentForm.tools,
      job: agentForm.job.trim() || "Teammate",
    };
    try {
      const editing = agentModal === "edit";
      const res = editing
        ? await api(`/api/agents/${encodeURIComponent(name)}`, { token, method: "PATCH", body: payload })
        : await api("/api/agents", { token, method: "POST", body: { name, ...payload } });
      if (res.ok) {
        const created = editing ? null : await res.json();
        setAgentModal(null);
        await Promise.all([loadAllAgents(), loadChannels()]);
        flash(editing ? `Updated ${display_name}` : `Created ${display_name}`);
        if (!editing && created?.dm_channel_id) switchChannel(created.dm_channel_id);
        return;
      }
      if (res.status === 409) setAgentErr("that agent name is taken");
      else if (res.status === 403) setAgentErr("only admins can manage bots");
      else if (res.status === 404) setAgentErr("channel or agent not found");
      else setAgentErr("couldn't save agent");
    } catch {
      setAgentErr("couldn't save agent");
    }
  }

  function peopleLabel(c) {
    const roster = c.people || [];
    return roster.find((h) => h !== user) || roster[0] || c.name;
  }

  async function openHumanDm(handle) {
    const res = await api("/api/dms", { token, method: "POST", body: { handle } });
    if (!res.ok) {
      flash(res.status === 404 ? "No such person" : "Couldn't open that DM", true);
      return;
    }
    const created = await res.json();
    setPeopleModal(false);
    await loadChannels();
    switchChannel(created.id);
  }

  async function archiveAgent() {
    if (!isAdmin || !agentForm.name) return;
    if (!window.confirm(`Archive @${agentForm.name}? Old messages stay; they will stop answering.`)) return;
    const res = await api(`/api/agents/${encodeURIComponent(agentForm.name)}`, { token, method: "DELETE", json: false });
    if (!res.ok) {
      setAgentErr("couldn't archive that bot");
      return;
    }
    setAgentModal(null);
    await Promise.all([loadAllAgents(), loadChannels(), loadTeams()]);
    flash(`Archived ${agentForm.display_name || agentForm.name}`);
    if (channel === `dm-${agentForm.name}`) switchChannel("general");
  }

  async function saveTeam(ev) {
    ev.preventDefault();
    setTeamErr("");
    const name = (teamForm.name || "").trim();
    const id = (teamForm.id || slugFromName(name)).trim();
    if (!name || !id) {
      setTeamErr("name is required");
      return;
    }
    if (!teamForm.members.length) {
      setTeamErr("pick at least one bot");
      return;
    }
    const res = await api("/api/teams", {
      token,
      method: "POST",
      body: { id, name, description: teamForm.description, members: teamForm.members },
    });
    if (!res.ok) {
      setTeamErr(res.status === 409 ? "that team id is taken" : res.status === 403 ? "admin only" : "couldn't create team");
      return;
    }
    setTeamModal(false);
    await loadTeams();
    flash(`@${id} is ready — mention the team in a room`);
  }

  async function exportChannel(fmt) {
    const res = await api(`/api/channels/${encodeURIComponent(channel)}/export?format=${fmt}`, { token, json: false });
    if (!res.ok) {
      flash("Couldn't export this channel", true);
      return;
    }
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${channel}-audit.${fmt}`;
    a.click();
    URL.revokeObjectURL(url);
  }

  async function runSearch(ev) {
    ev?.preventDefault();
    const q = searchQ.trim();
    if (q.length < 2) {
      setSearchHits(null);
      return;
    }
    setSearchBusy(true);
    try {
      const res = await apiJson(`/api/search?q=${encodeURIComponent(q)}`, { token: tokenRef.current });
      setSearchHits(res.ok ? res.data : []);
    } catch {
      setSearchHits([]);
    }
    setSearchBusy(false);
  }

  async function resolveApproval(id, status) {
    const res = await api(`/api/approvals/${id}/resolve`, { token, method: "POST", body: { status } });
    if (!res.ok) {
      flash("Couldn't resolve approval", true);
      return;
    }
    const updated = await res.json();
    setApprovals((list) => {
      const i = list.findIndex((a) => a.id === updated.id);
      if (i >= 0) {
        const next = list.slice();
        next[i] = updated;
        return next;
      }
      return [updated, ...list];
    });
  }

  async function previewFile(path) {
    try {
      const res = await apiJson(`/api/computer/file?path=${encodeURIComponent(path)}`, { token: tokenRef.current });
      if (!res.ok) throw new Error("file");
      setFilePreview(res.data.content);
    } catch {
      flash("Couldn't open that file", true);
    }
  }

  async function loadEarlier() {
    const ids = Object.keys(messages).map(Number).filter(Boolean);
    const oldest = Math.min(...ids);
    if (!Number.isFinite(oldest)) return;
    try {
      const page = await loadHistory(channel, oldest);
      applyHistory(page, true);
    } catch {
      flash("Couldn't load earlier messages", true);
    }
  }

  const placeholder = bot
    ? (bot.name === "coder" ? `Ask ${botLabel(bot)} for a program, proof, or listing` : `Message ${botLabel(bot)} — they already hear you`)
    : peopleChat
      ? `Message ${peopleLabel(peopleChat)} — private, like Slack DMs`
      : group
        ? `Message ${current.name} — everyone in the group hears you`
        : `Message #${current.name || current.id} — @name a bot, @core the pod`;

  const layout = [
    "app",
    threadId ? "thread-open" : "",
    computerOpen ? "computer-open" : "",
    sidebarOpen ? "sidebar-open" : "",
    showTools ? "" : "hide-tools",
    mainView === "paper" ? "paper-open" : "",
  ].filter(Boolean).join(" ");

  const streamRows = Object.values(streams);
  const catalogNames = useMemo(
    () => (toolCatalog.length ? toolCatalog.map((t) => t.name) : ALL_TOOLS),
    [toolCatalog],
  );

  return (
    <div className={layout}>
      <Toast toast={toast} />
      {!user && (
        <div id="login">
          <form className="card" onSubmit={onLogin}>
            <p className="kicker">Agents as teammates</p>
            <h1>Same room. Same audit trail.</h1>
            <p>Named LLM Bots join your channels — not a sidebar chatbot. Pick a handle to register or reconnect.</p>
            <label className="sr-only" htmlFor="login-input">Handle</label>
            <input id="login-input" maxLength={24} autoComplete="username" placeholder="your handle" autoFocus value={handleDraft} onChange={(e) => setHandleDraft(e.target.value)} />
            <button type="submit" className="btn primary" disabled={loginBusy}>Get started</button>
            <div className="hint">New handle registers you. A handle you've used here reconnects.</div>
            <div className="err" role="alert">{loginErr}</div>
          </form>
        </div>
      )}

      {user && onboarding && (
        <div id="onboarding">
          <div className="card onboarding-card">
            {onboarding.step === 1 && (
              <ApiConfigStep
                token={token}
                flash={flash}
                onSkip={() => setOnboarding((o) => ({ ...o, step: 2, err: "" }))}
                onContinue={async () => {
                  bustCache("/api/status");
                  await loadGroqStatus();
                  setOnboarding((o) => ({ ...o, step: 2, err: "" }));
                }}
              />
            )}
            {onboarding.step === 2 && (
              <>
                <p className="kicker">Step 2 of 3</p>
                <h1>Pick your first Bot</h1>
                <p>Each template is a named job with tools, memory, and a 1:1 channel. You can add more later.</p>
                <div className="job-templates onboarding-jobs">
                  {jobs.map((job) => (
                    <button
                      key={job.id}
                      type="button"
                      className={onboarding.template?.id === job.id ? "active" : ""}
                      onClick={() => setOnboarding((o) => ({
                        ...o,
                        template: job,
                        name: job.suggested_name || o.name,
                        err: "",
                      }))}
                    >
                      <span className="job-title">{job.job}</span>
                      {job.id === "chief-of-staff" && <span className="job-badge">Recommended</span>}
                    </button>
                  ))}
                </div>
                <div className="modal-actions onboarding-actions">
                  <button type="button" className="btn ghost" onClick={skipOnboarding}>Skip — use default teammates</button>
                  <button
                    type="button"
                    className="btn"
                    onClick={() => setOnboarding((o) => ({ ...o, step: 1, err: "" }))}
                  >
                    Back
                  </button>
                  <button
                    type="button"
                    className="btn primary"
                    disabled={!onboarding.template}
                    onClick={() => setOnboarding((o) => ({ ...o, step: 3, err: "" }))}
                  >
                    Continue
                  </button>
                </div>
              </>
            )}
            {onboarding.step === 3 && onboarding.template && (
              <form onSubmit={createOnboardingBot}>
                <p className="kicker">Step 3 of 3</p>
                <h1>Name them</h1>
                <p>Primary job: <strong>{onboarding.template.job}</strong>. You'll land in their 1:1 with a suggested first message.</p>
                <label htmlFor="onboard-display">Bot name</label>
                <input
                  id="onboard-display"
                  required
                  maxLength={40}
                  placeholder="Maya"
                  autoFocus
                  value={onboarding.displayName || ""}
                  onChange={(e) => {
                    const displayName = e.target.value;
                    setOnboarding((o) => ({
                      ...o,
                      displayName,
                      name: slugFromName(displayName),
                      err: "",
                    }));
                  }}
                />
                <label htmlFor="onboard-name">Mention handle</label>
                <input
                  id="onboard-name"
                  required
                  maxLength={32}
                  pattern="[A-Za-z0-9_\-]+"
                  placeholder="maya"
                  value={onboarding.name}
                  onChange={(e) => setOnboarding((o) => ({ ...o, name: e.target.value, err: "" }))}
                />
                <div className="hint">Shown as {onboarding.displayName || "Maya"}. Mention as @{onboarding.name || "maya"} in rooms, or talk in their 1:1 without @.</div>
                <div className="err" role="alert">{onboarding.err}</div>
                <div className="modal-actions onboarding-actions">
                  <button type="button" className="btn" onClick={() => setOnboarding((o) => ({ ...o, step: 2, err: "" }))}>Back</button>
                  <button type="submit" className="btn primary" disabled={onboarding.busy}>
                    {onboarding.busy ? "Creating…" : "Create & open 1:1"}
                  </button>
                </div>
              </form>
            )}
          </div>
        </div>
      )}

      {user && !onboarding && (
      <>
      {channelModal && (
        <div className="overlay" onClick={(e) => e.target === e.currentTarget && setChannelModal(null)}>
          <div className="modal-card" role="dialog" aria-modal="true">
            <h2>{channelModal === "group" ? "New group chat" : "New channel"}</h2>
            <form onSubmit={createChannel}>
              <label htmlFor="channel-name">Name</label>
              <input id="channel-name" name="name" maxLength={64} required placeholder={channelModal === "group" ? "launch team" : "release-planning"} autoFocus />
              <label htmlFor="channel-topic">Topic <span className="optional">(optional)</span></label>
              <input id="channel-topic" name="topic" maxLength={200} placeholder={channelModal === "group" ? "what this group is working on" : "what this channel is for"} />
              {channelModal === "group" && (
                <>
                  <label>Bots in this group</label>
                  <div className="member-picks">
                    {allAgents.map((a) => {
                      const on = groupMembers.includes(a.name);
                      return (
                        <button
                          key={a.name}
                          type="button"
                          className={`member-pick${on ? " active" : ""}`}
                          onClick={() => setGroupMembers((list) => on ? list.filter((n) => n !== a.name) : [...list, a.name])}
                        >
                          <span className={`dot ${a.status || "idle"}`} />
                          <span className="bot-name">{botLabel(a)}</span>
                          <span className="bot-job">{a.job || `@${a.name}`}</span>
                        </button>
                      );
                    })}
                    {!allAgents.length && <p className="empty-state">Create a bot first.</p>}
                  </div>
                </>
              )}
              <div className="err" role="alert">{channelErr}</div>
              <div className="modal-actions">
                <button type="button" className="btn" onClick={() => setChannelModal(null)}>Cancel</button>
                <button type="submit" className="btn primary">{channelModal === "group" ? "Create group" : "Create"}</button>
              </div>
            </form>
          </div>
        </div>
      )}

      {peopleModal && (
        <div className="overlay" onClick={(e) => e.target === e.currentTarget && setPeopleModal(false)}>
          <div className="modal-card" role="dialog" aria-modal="true">
            <h2>Message a person</h2>
            <p className="hint">Private 1:1 — only the two of you see it. @mention a bot to pull them in.</p>
            <div className="member-picks">
              {people.filter((p) => p.handle !== user).map((p) => (
                <button key={p.handle} type="button" className="member-pick" onClick={() => openHumanDm(p.handle)}>
                  <span className={`dot ${p.online ? "working" : "idle"}`} />
                  <span className="bot-name">{p.handle}</span>
                  <span className="bot-job">{p.online ? "online" : p.role}</span>
                </button>
              ))}
              {!people.filter((p) => p.handle !== user).length && <p className="empty-state">No other people yet. Have a teammate register.</p>}
            </div>
            <div className="modal-actions">
              <button type="button" className="btn" onClick={() => setPeopleModal(false)}>Cancel</button>
            </div>
          </div>
        </div>
      )}

      {teamModal && (
        <div className="overlay" onClick={(e) => e.target === e.currentTarget && setTeamModal(false)}>
          <div className="modal-card" role="dialog" aria-modal="true">
            <h2>New team</h2>
            <form onSubmit={saveTeam}>
              <p className="hint">@team-id in any room runs these bots in order — Buzz-style pods without leaving chat.</p>
              <label htmlFor="team-name">Name</label>
              <input
                id="team-name"
                required
                maxLength={64}
                placeholder="Launch"
                value={teamForm.name}
                onChange={(e) => setTeamForm((f) => ({ ...f, name: e.target.value, id: f.id || slugFromName(e.target.value) }))}
              />
              <label htmlFor="team-id">Mention handle</label>
              <input
                id="team-id"
                required
                maxLength={32}
                pattern="[A-Za-z0-9_\-]+"
                placeholder="launch"
                value={teamForm.id}
                onChange={(e) => setTeamForm((f) => ({ ...f, id: e.target.value }))}
              />
              <label htmlFor="team-desc">Description <span className="optional">(optional)</span></label>
              <input id="team-desc" maxLength={200} value={teamForm.description} onChange={(e) => setTeamForm((f) => ({ ...f, description: e.target.value }))} />
              <label>Bots</label>
              <div className="member-picks">
                {allAgents.map((a) => {
                  const on = teamForm.members.includes(a.name);
                  return (
                    <button
                      key={a.name}
                      type="button"
                      className={`member-pick${on ? " active" : ""}`}
                      onClick={() => setTeamForm((f) => ({
                        ...f,
                        members: on ? f.members.filter((n) => n !== a.name) : [...f.members, a.name],
                      }))}
                    >
                      <span className={`dot ${a.status || "idle"}`} />
                      <span className="bot-name">{botLabel(a)}</span>
                      <span className="bot-job">@{a.name}</span>
                    </button>
                  );
                })}
              </div>
              <div className="err" role="alert">{teamErr}</div>
              <div className="modal-actions">
                <button type="button" className="btn" onClick={() => setTeamModal(false)}>Cancel</button>
                <button type="submit" className="btn primary">Create team</button>
              </div>
            </form>
          </div>
        </div>
      )}

      {agentModal && (
        <div className="overlay" onClick={(e) => e.target === e.currentTarget && setAgentModal(null)}>
          <div className="modal-card agent-card" role="dialog" aria-modal="true">
            <h2>{agentModal === "edit" ? (agentForm.display_name || `@${agentForm.name}`) : "New Bot"}</h2>
            <form onSubmit={saveAgent}>
              {agentModal === "create" && (
                <div className="job-templates">
                  {jobs.map((job) => (
                    <button
                      key={job.job}
                      type="button"
                      className={agentForm.job === job.job ? "active" : ""}
                      onClick={() => setAgentForm((f) => {
                        const suggested = job.suggested_name || "";
                        const pretty = suggested.replace(/[-_]/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
                        return {
                          ...f,
                          job: job.job,
                          prompt: job.prompt,
                          display_name: f.display_name && handleTouched ? f.display_name : pretty,
                          name: handleTouched && f.name ? f.name : suggested,
                        };
                      })}
                    >
                      {job.job}
                    </button>
                  ))}
                </div>
              )}
              <label htmlFor="agent-display">Bot name</label>
              <input
                id="agent-display"
                required
                maxLength={40}
                placeholder="Maya"
                value={agentForm.display_name}
                onChange={(e) => {
                  const display_name = e.target.value;
                  setAgentForm((f) => ({
                    ...f,
                    display_name,
                    name: agentModal === "edit" || handleTouched ? f.name : slugFromName(display_name),
                  }));
                }}
              />
              <label htmlFor="agent-name">Mention handle</label>
              <input
                id="agent-name"
                required
                maxLength={32}
                pattern="[A-Za-z0-9_\-]+"
                placeholder="maya"
                disabled={agentModal === "edit"}
                value={agentForm.name}
                onChange={(e) => {
                  setHandleTouched(true);
                  setAgentForm((f) => ({ ...f, name: e.target.value }));
                }}
              />
              <div className="hint">People see {agentForm.display_name || "Maya"}. Rooms mention @{agentForm.name || "maya"}.</div>
              <label htmlFor="agent-job">Primary job</label>
              <input id="agent-job" maxLength={64} placeholder="Product Performance" value={agentForm.job} onChange={(e) => setAgentForm((f) => ({ ...f, job: e.target.value }))} />
              <label htmlFor="agent-prompt">How they should work</label>
              <textarea id="agent-prompt" required maxLength={4000} rows={5} placeholder="One job, sources, deliverable, and what needs approval." value={agentForm.prompt} onChange={(e) => setAgentForm((f) => ({ ...f, prompt: e.target.value }))} />
              <label htmlFor="agent-model">Model</label>
              <input id="agent-model" maxLength={128} placeholder={DEFAULT_MODEL} value={agentForm.model} onChange={(e) => setAgentForm((f) => ({ ...f, model: e.target.value }))} />
              <label htmlFor="agent-scope">Channel scope</label>
              <select id="agent-scope" value={agentForm.scope} onChange={(e) => setAgentForm((f) => ({ ...f, scope: e.target.value }))}>
                <option value="">Every channel (plus their 1:1)</option>
                {rooms.map((c) => <option key={c.id} value={c.id}>#{c.name}</option>)}
              </select>
              <label htmlFor="agent-window">History window</label>
              <input id="agent-window" type="number" min={1} max={50} value={agentForm.window} onChange={(e) => setAgentForm((f) => ({ ...f, window: e.target.value }))} />
              <fieldset className="tool-toggles">
                <legend>Tools</legend>
                {catalogNames.map((t) => {
                  const meta = toolCatalog.find((x) => x.name === t);
                  return (
                    <label key={t} className="check">
                      <input type="checkbox" checked={agentForm.tools.includes(t)} onChange={(e) => setAgentForm((f) => ({
                        ...f,
                        tools: e.target.checked ? [...f.tools, t] : f.tools.filter((x) => x !== t),
                      }))} />
                      {t}
                      {meta?.kind && meta.kind !== "builtin" && (
                        <span className="tool-kind">{meta.kind}</span>
                      )}
                    </label>
                  );
                })}
              </fieldset>
              {agentForm.memories?.length > 0 && (
                <div id="agent-memories">
                  <div className="mem-title">Recent notes</div>
                  <ul>
                    {agentForm.memories.map((m) => (
                      <li key={m.id}>{m.kind} · {m.channel_id ? `#${m.channel_id}` : "global"}: {m.body}</li>
                    ))}
                  </ul>
                </div>
              )}
              <div className="err" role="alert">{agentErr}</div>
              <div className="modal-actions">
                <button type="button" className="btn" onClick={() => setAgentModal(null)}>Cancel</button>
                {agentModal === "edit" && (
                  <button type="button" className="btn" onClick={() => {
                    const found = allAgents.find((a) => a.name === agentForm.name);
                    setAgentModal(null);
                    if (found) switchChannel(found.dm_channel_id);
                  }}>Open 1:1</button>
                )}
                {agentModal === "edit" && isAdmin && (
                  <button type="button" className="btn danger" onClick={archiveAgent}>Archive</button>
                )}
                <button type="submit" className="btn primary">{agentModal === "edit" ? "Save" : "Create"}</button>
              </div>
            </form>
          </div>
        </div>
      )}

      {emoji && (
        <div id="emoji-pop" role="dialog" aria-label="Pick a reaction" style={{ top: emoji.top, left: emoji.left }}>
          {EMOJI.map((e) => (
            <button key={e} type="button" className="emoji-chip" onClick={() => sendReaction(emoji.id, e)}>{e}</button>
          ))}
        </div>
      )}

      <div id="sidebar-backdrop" hidden={!sidebarOpen} onClick={() => setSidebarOpen(false)} />

      <nav id="sidebar" aria-label="Workspace">
        <div className="brand">swarm<small>workspace · bots as teammates</small></div>
        <form className="sidebar-search" onSubmit={runSearch}>
          <label className="sr-only" htmlFor="workspace-search">Search messages</label>
          <input
            id="workspace-search"
            value={searchQ}
            onChange={(e) => { setSearchQ(e.target.value); if (!e.target.value) setSearchHits(null); }}
            placeholder="Search"
            autoComplete="off"
          />
        </form>
        {searchHits && (
          <div className="search-hits" role="listbox" aria-label="Search results">
            {searchBusy && <div className="empty">Searching…</div>}
            {!searchBusy && !searchHits.length && <div className="empty">No matches</div>}
            {!searchBusy && searchHits.map((hit) => (
              <button
                key={hit.id}
                type="button"
                className="search-hit"
                onClick={() => { switchChannel(hit.channel_id); setSearchHits(null); setSearchQ(""); }}
              >
                <span className="ch-name plain">
                  {hit.channel_kind === "people" || hit.channel_kind === "dm" ? hit.channel_name : `#${hit.channel_name}`}
                </span>
                <span className="hit-body">{hit.author}: {String(hit.body || "").slice(0, 80)}</span>
              </button>
            ))}
          </div>
        )}
        <div className="section-label">Direct messages</div>
        <div id="bots-list">
          {peopleDms.map((c) => {
            const other = peopleLabel(c);
            const person = people.find((p) => p.handle === other);
            return (
              <button key={c.id} type="button" className={`bot-item${c.id === channel ? " active" : ""}`} onClick={() => switchChannel(c.id)}>
                <span className={`dot ${person?.online ? "working" : "idle"}`} />
                <span className="bot-meta">
                  <span className="bot-name">{other}</span>
                  <span className="bot-job">{person?.online ? "online" : "person"}</span>
                </span>
              </button>
            );
          })}
          {allAgents.map((a) => (
            <button key={a.name} type="button" className={`bot-item${a.dm_channel_id === channel ? " active" : ""}`} onClick={() => switchChannel(a.dm_channel_id)}>
              <span className={`dot ${a.status || "idle"}`} />
              <span className="bot-meta">
                <span className="bot-name">{botLabel(a)}</span>
                <span className="bot-job">{a.job || `@${a.name}`}</span>
              </span>
            </button>
          ))}
          {!allAgents.length && !peopleDms.length && <div className="empty">No conversations yet</div>}
        </div>
        <button type="button" id="new-dm" onClick={() => setPeopleModal(true)}>+ Message a person</button>
        {isAdmin && <button type="button" id="new-agent" onClick={openCreateAgent}>+ New Bot</button>}
        <div className="section-label">Teams</div>
        <ul id="team-list">
          {!teams.length && <li className="empty-state">No teams yet</li>}
          {teams.map((t) => (
            <li key={t.id} className="channel-item">
              <button type="button" className="team-item" onClick={() => { setDraft((d) => (d ? `${d} @${t.id} ` : `@${t.id} `)); inputRef.current?.focus(); }}>
                <span className="ch-name plain">@{t.id}</span>
                <span className="ch-topic">{(t.members || []).map(labelFor).join(", ") || t.name}</span>
              </button>
            </li>
          ))}
        </ul>
        {isAdmin && (
          <button
            type="button"
            id="new-team"
            onClick={() => {
              setTeamErr("");
              setTeamForm({ id: "", name: "", description: "", members: allAgents.slice(0, 2).map((a) => a.name) });
              setTeamModal(true);
            }}
          >
            + New team
          </button>
        )}
        <div className="section-label">Groups</div>
        <ul id="group-list">
          {!groups.length && <li className="empty-state">No groups yet</li>}
          {groups.map((c) => (
            <li key={c.id} className="channel-item group-item">
              <button type="button" className={c.id === channel ? "active" : ""} onClick={() => switchChannel(c.id)}>
                <span className="ch-name">{c.name}</span>
                <span className="member-chips">
                  {(c.members || []).slice(0, 4).map((n) => (
                    <span key={n} className="member-chip">{labelFor(n)}</span>
                  ))}
                </span>
              </button>
              <button type="button" className="ch-delete" aria-label={`Delete ${c.name}`} onClick={() => deleteChannel(c.id)}>×</button>
            </li>
          ))}
        </ul>
        <button type="button" id="new-group" onClick={() => { setChannelErr(""); setGroupMembers(allAgents.slice(0, 2).map((a) => a.name)); setChannelModal("group"); }}>+ New group</button>
        <div className="section-label">Channels</div>
        <ul id="channel-list">
          {!rooms.length && <li className="empty-state">No channels yet</li>}
          {rooms.map((c) => (
            <li key={c.id} className="channel-item">
              <button type="button" className={c.id === channel ? "active" : ""} onClick={() => switchChannel(c.id)}>
                <span className="ch-name">{c.name}</span>
                {c.topic ? <span className="ch-topic">{c.topic}</span> : null}
              </button>
              <button type="button" className="ch-delete" aria-label={`Delete #${c.name}`} onClick={() => deleteChannel(c.id)}>×</button>
            </li>
          ))}
        </ul>
        <button type="button" id="new-channel" onClick={() => { setChannelErr(""); setChannelModal("room"); }}>+ New channel</button>
        <div id="agents-box">
          <div id="groq-status" className={agentsReady ? "ready" : "missing"}>
            {demoMode ? "Demo mode — mock replies" : agentsReady ? "Bots ready" : "Set GROQ_API_KEY or SWARM_DEMO=1"}
          </div>
        </div>
        <div id="me">
          <div className="avatar">{initials(user || "?")}</div>
          <div className="me-meta">
            <div className="handle">{user || "anon"}{isAdmin ? <span className="role-pill">admin</span> : null}</div>
            <div className={`sub ${wsStatus}`}>{wsStatus}</div>
          </div>
          <button type="button" className="btn ghost" onClick={() => { if (user) localStorage.removeItem(`swarm_token_${user}`); logout(); }}>Log out</button>
        </div>
      </nav>

      <main id="main">
        <div id="topbar">
          <button type="button" id="menu-btn" aria-label={sidebarOpen ? "Close sidebar" : "Open sidebar"} aria-expanded={sidebarOpen} onClick={() => setSidebarOpen((open) => !open)}>☰</button>
          <div className="channel-meta">
            <div className="name">{bot ? botLabel(bot) : peopleChat ? peopleLabel(peopleChat) : group ? current.name : `#${current.name || current.id}`}</div>
            <div className="topic">
              {bot
                ? (bot.job || current.topic || "Teammate")
                : peopleChat
                  ? "Private conversation — only you two see this"
                  : group
                    ? ((current.members || []).map(labelFor).join(", ") || current.topic || "Group chat")
                    : (current.topic || "No topic set")}
            </div>
          </div>
          <div className="view-tabs" role="tablist" aria-label="Channel view">
            <button type="button" className={`tab${mainView === "talk" ? " active" : ""}`} onClick={() => setMainView("talk")}>Talk</button>
            <button type="button" className={`tab${mainView === "paper" ? " active" : ""}`} onClick={() => setMainView("paper")}>Paper</button>
          </div>
          {bot && <span className={`status-chip ${bot.status || "idle"}`}>{statusLabel(bot.status)}</span>}
          <span className="who">you're <span>{user || "anon"}</span></span>
          {bot && isAdmin && <button type="button" className="btn ghost" onClick={() => openAgentPanel(bot.name)}>Configure</button>}
          <button type="button" className="btn ghost" onClick={() => exportChannel("json")}>Export JSON</button>
          <button type="button" className="btn ghost" onClick={() => exportChannel("csv")}>Export CSV</button>
          <button type="button" className="btn ghost" onClick={() => setShowTools((v) => !v)}>{showTools ? "Hide tool log" : "Show tool log"}</button>
          <button type="button" className="btn ghost" onClick={() => setComputerOpen((v) => !v)}>Computer</button>
        </div>
        {mainView === "paper" ? (
          <div id="log" ref={logRef} className="paper-log" role="document">
            <PaperView title={bot ? bot.name : (current.name || current.id)} messages={[...roots, ...Object.values(messages).filter((m) => m.parent_id)]} />
          </div>
        ) : (
        <>
        {hasMore && <button type="button" id="load-earlier" onClick={loadEarlier}>Load earlier messages</button>}
        <div id="log" ref={logRef} role="log" aria-live="polite">
          {loadingLog && <div className="loading-state">Loading messages…</div>}
          {!loadingLog && logError && <div className="empty-state">{logError}</div>}
          {!loadingLog && !logError && !roots.length && !streamRows.length && (
            <div className="empty-state editorial">
              {bot?.name === "coder"
                ? "Empty page. Ask for a function, a proof, or a listing — code and TeX compile into Paper."
                : bot
                  ? `A blank channel. Give ${botLabel(bot)} a real task.`
                  : peopleChat
                    ? `Private 1:1 with ${peopleLabel(peopleChat)}. Bots only join if you @mention them.`
                    : group
                    ? "A group chat. Message the room and every member hears you — or @mention one."
                    : "A blank channel. Talk here, @coder for a listing, or @core to run the pod."}
            </div>
          )}
          {roots.map((m, i) => (
            <MessageRow
              key={m.id}
              m={m}
              grouped={groupedWith(roots[i - 1], m)}
              reactions={reactions[m.id]}
              replyCount={replyCounts[m.id] || 0}
              onReply={openThread}
              onReact={sendReaction}
              onOpenThread={openThread}
              onDelete={deleteMessage}
              onToggleEmoji={(id, el) => {
                const r = el.getBoundingClientRect();
                setEmoji({ id, top: r.bottom + 8, left: Math.min(r.left, window.innerWidth - 220) });
              }}
              label={labelFor(m.author)}
            />
          ))}
          {streamRows.map((m) => (
            <MessageRow key={`stream-${m.author}`} m={m} grouped={false} reactions={[]} replyCount={0} onReply={() => {}} onReact={() => {}} onOpenThread={() => {}} onToggleEmoji={() => {}} label={labelFor(m.author)} />
          ))}
        </div>
        </>
        )}
        <div id="approval-dock">
          {pendingHere.map((a) => <ApprovalCard key={a.id} a={a} onResolve={resolveApproval} />)}
        </div>
        <div id="typing" aria-live="polite">{typing}</div>
        <div id="composer">
          <div id="inputbar">
            {mention.open && (
              <ul id="mention-menu" role="listbox" aria-label="Mention a bot or skill">
                {mention.items.map((item, i) => (
                  <li key={item.label}>
                    <button type="button" className={i === mention.index ? "active" : ""} onMouseDown={(e) => { e.preventDefault(); applyMention(item); }}>
                      {item.label}
                    </button>
                  </li>
                ))}
              </ul>
            )}
            <textarea
              ref={inputRef}
              id="msg-input"
              rows={1}
              autoComplete="off"
              placeholder={placeholder}
              value={draft}
              onChange={(e) => onComposerInput(e.target.value, e.target.selectionStart)}
              onKeyDown={onComposerKey}
            />
            <button type="button" className="btn primary send" onClick={() => { sendFrom(draft, null); setDraft(""); }}>Send</button>
          </div>
          <div className="composer-hint">@bot · @core for the pod · /skill · people DMs stay private</div>
        </div>
      </main>

      {computerOpen && (
        <aside id="computer-panel">
          <div className="computer-head">
            <div>
              <div className="thread-title">Shared computer</div>
              <div className="thread-sub">
                {computer ? `Shared workspace · ${computer.files.length} file${computer.files.length === 1 ? "" : "s"}` : "Shared workspace"}
              </div>
            </div>
            <button type="button" className="btn ghost" onClick={() => setComputerOpen(false)}>Close</button>
          </div>
          <div className="panel-tabs" role="tablist">
            {["files", "tools", "plugins", "ai", "skills", "routines", "approvals"].map((tab) => (
              <button key={tab} type="button" className={`tab${panelTab === tab ? " active" : ""}`} onClick={() => setPanelTab(tab)}>{tab === "ai" ? "AI" : tab[0].toUpperCase() + tab.slice(1)}</button>
            ))}
          </div>
          {panelTab === "tools" && (
            <ToolsPanel
              token={token}
              toolCatalog={toolCatalog}
              plugins={pluginList}
              flash={flash}
              onReload={loadToolCatalog}
            />
          )}
          {panelTab === "plugins" && (
            <div className="panel-body">
              <p className="panel-note">
                Drop folders with <code>manifest.json</code> into <code>plugins/</code>, then reload.
              </p>
              <button type="button" className="btn primary" onClick={async () => {
                const res = await api("/api/plugins/reload", { token, method: "POST", body: {} });
                if (res.ok) {
                  const data = await res.json();
                  setPluginList(data.plugins || []);
                  await loadToolCatalog();
                  flash(`Reloaded — ${data.tool_count} tools`);
                } else flash("Couldn't reload plugins", true);
              }}>Reload plugins</button>
              <ul className="panel-list">
                {!pluginList.length && <li className="empty-state">No plugins loaded.</li>}
                {pluginList.map((p) => (
                  <li key={p.id}>
                    <strong>{p.name}</strong> v{p.version}
                    <div className="bot-job">{p.description}</div>
                    <div className="hint">{p.path}</div>
                  </li>
                ))}
              </ul>
            </div>
          )}
          {panelTab === "ai" && (
            <div className="panel-body">
              <ProviderPanel token={token} flash={flash} onStatusChange={loadGroqStatus} />
            </div>
          )}
          {panelTab === "files" && (
            <div className="panel-body">
              <p className="panel-note">{computer?.note || ""}</p>
              <ul className="panel-list">
                {!computer?.files?.length && <li className="empty-state">Workspace is empty. Ask a bot to write a file here.</li>}
                {computer?.files?.map((f) => (
                  <li key={f.path}><button type="button" className="linkish" onClick={() => previewFile(f.path)}>{f.path} ({f.size} B)</button></li>
                ))}
              </ul>
              {filePreview && <pre id="file-preview">{filePreview}</pre>}
              <div className="mem-title">Recent actions</div>
              <ul className="panel-list dim">
                {(computer?.activity || []).slice(0, 12).map((m) => <li key={m.id}>{m.body}</li>)}
              </ul>
            </div>
          )}
          {panelTab === "skills" && (
            <div className="panel-body">
              <form className="mini-form" onSubmit={async (ev) => {
                ev.preventDefault();
                const fd = new FormData(ev.target);
                const name = String(fd.get("name") || "").trim();
                const body = String(fd.get("body") || "").trim();
                if (!name || !body) return;
                const res = await api("/api/skills", { token, method: "POST", body: { name, body } });
                if (res.ok) { ev.target.reset(); loadSkills(); flash(`Saved /${name}`); }
                else flash("Couldn't save skill", true);
              }}>
                <input name="name" maxLength={64} required placeholder="weekly-health" />
                <textarea name="body" maxLength={8000} rows={4} required placeholder="When to use it, inputs, steps, validation, output, approval boundary." />
                <button type="submit" className="btn primary">Save skill</button>
              </form>
              <ul className="panel-list">
                {!skills.length && <li className="empty-state">No skills yet. Save a process that worked.</li>}
                {skills.map((s) => (
                  <li key={s.id}>
                    <button type="button" className="linkish" onClick={() => {
                      const next = insertMention(draft, draft.length, draft.length, s.name, "slash");
                      setDraft(next.value);
                    }}>/{s.name}</button>{" "}
                    <button type="button" className="btn ghost" onClick={async () => {
                      const res = await api(`/api/skills/${s.id}`, { token, method: "DELETE", json: false });
                      if (res.ok) loadSkills();
                    }}>Delete</button>
                  </li>
                ))}
              </ul>
            </div>
          )}
          {panelTab === "routines" && (
            <div className="panel-body">
              <form className="mini-form" onSubmit={async (ev) => {
                ev.preventDefault();
                const fd = new FormData(ev.target);
                const payload = {
                  agent_name: String(fd.get("agent") || ""),
                  title: String(fd.get("title") || "").trim(),
                  instructions: String(fd.get("instructions") || "").trim(),
                  interval_minutes: Number(fd.get("interval")) || 60,
                  enabled: true,
                };
                if (!payload.agent_name || !payload.title || !payload.instructions) return;
                const res = await api("/api/routines", { token, method: "POST", body: payload });
                if (res.ok) { ev.target.reset(); loadRoutines(); flash("Routine created"); }
                else flash("Couldn't create routine", true);
              }}>
                <input name="title" maxLength={80} required placeholder="Morning digest" />
                <textarea name="instructions" maxLength={4000} rows={3} required placeholder="Every run: what to do, where to post, what needs approval." />
                <div className="row-fields">
                  <select name="agent" defaultValue={bot?.name || allAgents[0]?.name || ""}>
                    {allAgents.map((a) => <option key={a.name} value={a.name}>{a.name}</option>)}
                  </select>
                  <input name="interval" type="number" min={1} max={10080} defaultValue={60} title="Minutes" />
                </div>
                <button type="submit" className="btn primary">Create routine</button>
              </form>
              <ul className="panel-list">
                {!routines.length && <li className="empty-state">No routines. Automate a skill after it is reliable.</li>}
                {routines.map((r) => (
                  <li key={r.id}>
                    <div>{r.title} · @{r.agent_name} · every {r.interval_minutes}m</div>
                    <div className="bot-job">{r.enabled ? "enabled" : "paused"}</div>
                    <button type="button" className="btn ghost" onClick={async () => {
                      const res = await api(`/api/routines/${r.id}/run`, { token, method: "POST", body: {} });
                      flash(res.ok ? "Routine started" : "Couldn't start routine", !res.ok);
                    }}>Test run</button>
                    <button type="button" className="btn ghost" onClick={async () => {
                      await api(`/api/routines/${r.id}`, { token, method: "PATCH", body: { enabled: !r.enabled } });
                      loadRoutines();
                    }}>{r.enabled ? "Pause" : "Enable"}</button>
                    <button type="button" className="btn ghost" onClick={async () => {
                      await api(`/api/routines/${r.id}`, { token, method: "DELETE", json: false });
                      loadRoutines();
                    }}>Delete</button>
                  </li>
                ))}
              </ul>
            </div>
          )}
          {panelTab === "approvals" && (
            <div className="panel-body">
              <ul className="panel-list">
                {!pendingAll.length && <li className="empty-state">Nothing waiting. Consequential actions pause here.</li>}
                {pendingAll.map((a) => <li key={a.id}><ApprovalCard a={a} onResolve={resolveApproval} /></li>)}
              </ul>
            </div>
          )}
        </aside>
      )}

      {threadId && (
        <aside id="thread-panel">
          <div className="thread-head">
            <div>
              <div className="thread-title">Thread</div>
              <div className="thread-sub">{threadParent ? `with ${threadParent.author}` : ""}</div>
            </div>
            <button type="button" className="btn ghost" onClick={() => { setThreadId(null); setThreadParent(null); setThreadReplies([]); }}>Close</button>
          </div>
          <div id="thread-log" ref={threadLogRef} role="log">
            {threadParent && <MessageRow m={threadParent} inThread reactions={reactions[threadParent.id]} replyCount={0} onReply={() => {}} onReact={sendReaction} onOpenThread={() => {}} onDelete={deleteMessage} onToggleEmoji={(id, el) => { const r = el.getBoundingClientRect(); setEmoji({ id, top: r.bottom + 8, left: Math.min(r.left, window.innerWidth - 220) }); }} label={labelFor(threadParent.author)} />}
            {threadReplies.map((m, i) => (
              <MessageRow key={m.id} m={m} inThread grouped={groupedWith(i ? threadReplies[i - 1] : threadParent, m)} reactions={reactions[m.id]} replyCount={0} onReply={() => {}} onReact={sendReaction} onOpenThread={() => {}} onDelete={deleteMessage} onToggleEmoji={(id, el) => { const r = el.getBoundingClientRect(); setEmoji({ id, top: r.bottom + 8, left: Math.min(r.left, window.innerWidth - 220) }); }} label={labelFor(m.author)} />
            ))}
          </div>
          <div className="thread-composer">
            <div id="inputbar-thread">
              <textarea ref={threadInputRef} id="thread-input" rows={1} autoComplete="off" placeholder="Reply in thread" value={threadDraft} onChange={(e) => setThreadDraft(e.target.value)} onKeyDown={(ev) => {
                if (ev.key === "Enter" && !ev.shiftKey) {
                  ev.preventDefault();
                  sendFrom(threadDraft, threadId);
                  setThreadDraft("");
                }
              }} />
              <button type="button" className="btn primary send" onClick={() => { sendFrom(threadDraft, threadId); setThreadDraft(""); }}>Send</button>
            </div>
          </div>
        </aside>
      )}
      </>
      )}
    </div>
  );
}
