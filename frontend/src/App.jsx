import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import katex from "katex";
import {
  ALL_TOOLS, DEFAULT_MODEL, EMOJI, HISTORY_LIMIT, api, escapeHtml, extractPaper,
  fmtTime, formatInline, groupedWith, initials, insertMention, mentionQuery,
  roleLine, statusLabel, tokenizeBody,
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

function MessageRow({ m, grouped, inThread, reactions, replyCount, onReply, onReact, onOpenThread, onToggleEmoji, onDelete }) {
  const counts = {};
  for (const r of reactions || []) counts[r.emoji] = (counts[r.emoji] || 0) + 1;
  return (
    <div className={`row ${m.author_kind || "human"}${grouped ? " grouped" : ""}${m.streaming ? " streaming" : ""}`}>
      <div className="row-main">
        <div className="avatar">{initials(m.author)}</div>
        <div className="content">
          <div className="meta">
            <span className="who">{m.author}</span>
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
  const [handleDraft, setHandleDraft] = useState(() => localStorage.getItem("swarm_last_handle") || "");
  const [computerOpen, setComputerOpen] = useState(() => localStorage.getItem("swarm_computer") !== "0");
  const [showTools, setShowTools] = useState(() => localStorage.getItem("swarm_show_tools") === "1");
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [channelModal, setChannelModal] = useState(false);
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
    name: "", job: "", prompt: "", model: DEFAULT_MODEL, scope: "", window: 12, tools: ALL_TOOLS, memories: [],
  });

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
  const rooms = channels.filter((c) => c.kind !== "dm");
  const roots = order.map((id) => messages[id]).filter(Boolean);
  const pendingHere = approvals.filter((a) => a.status === "pending" && a.channel_id === channel);
  const pendingAll = approvals.filter((a) => a.status === "pending");

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
    const res = await fetch("/api/channels");
    if (!res.ok) throw new Error("channels");
    const data = await res.json();
    setChannels(data);
    return data;
  }

  async function loadAllAgents() {
    try {
      const res = await fetch("/api/agents");
      setAllAgents(res.ok ? await res.json() : []);
    } catch {
      setAllAgents([]);
    }
  }

  async function loadAgents(channelId) {
    try {
      const res = await fetch(`/api/agents?channel_id=${encodeURIComponent(channelId)}`);
      setAgents(res.ok ? await res.json() : []);
    } catch {
      setAgents([]);
    }
  }

  async function loadGroqStatus() {
    try {
      const res = await fetch("/api/status");
      const data = res.ok ? await res.json() : { groq: false, openrouter: false };
      setAgentsReady(!!(data.groq || data.openrouter));
    } catch {
      setAgentsReady(false);
    }
  }

  async function loadJobs() {
    try {
      const res = await fetch("/api/jobs");
      setJobs(res.ok ? await res.json() : []);
    } catch {
      setJobs([]);
    }
  }

  async function loadSkills() {
    try {
      const res = await fetch("/api/skills");
      setSkills(res.ok ? await res.json() : []);
    } catch {
      setSkills([]);
    }
  }

  async function loadRoutines() {
    try {
      const res = await fetch("/api/routines");
      setRoutines(res.ok ? await res.json() : []);
    } catch {
      setRoutines([]);
    }
  }

  async function loadApprovals() {
    try {
      const res = await fetch("/api/approvals?status=pending");
      setApprovals(res.ok ? await res.json() : []);
    } catch {
      setApprovals([]);
    }
  }

  async function loadComputer() {
    try {
      const res = await fetch("/api/computer");
      setComputer(res.ok ? await res.json() : null);
    } catch {
      setComputer(null);
    }
  }

  async function loadHistory(channelId, beforeId) {
    const params = new URLSearchParams({ limit: String(HISTORY_LIMIT) });
    if (beforeId) params.set("before_id", String(beforeId));
    const res = await fetch(`/api/channels/${channelId}/messages?${params}`);
    if (!res.ok) throw new Error("history");
    return res.json();
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

  async function enterWorkspace(handle, tok, preferred) {
    setToken(tok);
    setUser(handle);
    localStorage.setItem(`swarm_token_${handle}`, tok);
    localStorage.setItem("swarm_last_handle", handle);
    const [chs] = await Promise.all([loadChannels(), loadAllAgents(), loadJobs(), loadSkills(), loadRoutines()]);
    const next = (chs || []).some((c) => c.id === (preferred || "dm-swarm"))
      ? (preferred || "dm-swarm")
      : (chs[0]?.id || "general");
    await switchChannel(next);
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
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ handle }),
      });
      let tok = null;
      if (res.ok) tok = (await res.json()).token;
      else if (res.status === 409) {
        tok = localStorage.getItem(`swarm_token_${handle}`);
        if (!tok) setLoginErr("handle taken and no saved session — pick another");
      } else setLoginErr("registration failed");
      if (tok) await enterWorkspace(handle, tok);
    } catch {
      setLoginErr("couldn't reach the relay");
    }
    setLoginBusy(false);
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
      flash("Bots can't reply until GROQ_API_KEY is set in .env", true);
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
      ? (agents.length ? agents : allAgents)
          .filter((a) => a.name.toLowerCase().startsWith(q.query))
          .map((a) => ({ label: `@${a.name}`, value: a.name, kind: "at" }))
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
      const res = await fetch(`/api/messages/${id}/thread`);
      if (!res.ok) throw new Error("thread");
      const data = await res.json();
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
    setChannelErr("");
    if (!name) return;
    try {
      const res = await api("/api/channels", { token, method: "POST", body: { name, topic } });
      if (res.ok) {
        const c = await res.json();
        setChannelModal(false);
        await loadChannels();
        switchChannel(c.id);
      } else if (res.status === 409) setChannelErr("that channel already exists");
      else setChannelErr("couldn't create channel");
    } catch {
      setChannelErr("couldn't create channel");
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
    setAgentErr("");
    setAgentForm({ name: "", job: "", prompt: "", model: DEFAULT_MODEL, scope: "", window: 12, tools: ALL_TOOLS, memories: [] });
    setAgentModal("create");
  }

  async function openAgentPanel(name) {
    setAgentErr("");
    try {
      const res = await fetch(`/api/agents/${encodeURIComponent(name)}`);
      if (!res.ok) throw new Error("agent");
      const a = await res.json();
      setAgentForm({
        name: a.name, job: a.job || "", prompt: a.system_prompt || "", model: a.model || DEFAULT_MODEL,
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
    const name = agentForm.name.trim();
    const system_prompt = agentForm.prompt.trim();
    if (!name || !system_prompt) {
      setAgentErr("name and prompt are required");
      return;
    }
    const payload = {
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
        flash(editing ? `Updated @${name}` : `Created @${name}`);
        if (!editing && created?.dm_channel_id) switchChannel(created.dm_channel_id);
        return;
      }
      if (res.status === 409) setAgentErr("that agent name is taken");
      else if (res.status === 404) setAgentErr("channel or agent not found");
      else setAgentErr("couldn't save agent");
    } catch {
      setAgentErr("couldn't save agent");
    }
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
      const res = await fetch(`/api/computer/file?path=${encodeURIComponent(path)}`);
      if (!res.ok) throw new Error("file");
      setFilePreview((await res.json()).content);
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
    ? (bot.name === "coder" ? `Ask ${bot.name} for a program, proof, or listing` : `Message ${bot.name} — they already hear you`)
    : `Message #${current.name || current.id} — @coder for code, @swarm to talk`;

  const layout = [
    "app",
    threadId ? "thread-open" : "",
    computerOpen ? "computer-open" : "",
    sidebarOpen ? "sidebar-open" : "",
    showTools ? "" : "hide-tools",
    mainView === "paper" ? "paper-open" : "",
  ].filter(Boolean).join(" ");

  const streamRows = Object.values(streams);

  return (
    <div className={layout}>
      <Toast toast={toast} />
      {!user && (
        <div id="login">
          <form className="card" onSubmit={onLogin}>
            <p className="kicker">A quiet room</p>
            <h1>Talk. Then put the work on paper.</h1>
            <p>Teammates live in channels, not a sidebar chatbot. @swarm for conversation, @coder for programs and proofs — code lands in its own LaTeX subsection.</p>
            <label className="sr-only" htmlFor="login-input">Handle</label>
            <input id="login-input" maxLength={24} autoComplete="username" placeholder="your handle" autoFocus value={handleDraft} onChange={(e) => setHandleDraft(e.target.value)} />
            <button type="submit" className="btn primary" disabled={loginBusy}>Get started</button>
            <div className="hint">New handle registers you. A handle you've used here reconnects.</div>
            <div className="err" role="alert">{loginErr}</div>
          </form>
        </div>
      )}

      {channelModal && (
        <div className="overlay" onClick={(e) => e.target === e.currentTarget && setChannelModal(false)}>
          <div className="modal-card" role="dialog" aria-modal="true">
            <h2>New channel</h2>
            <form onSubmit={createChannel}>
              <label htmlFor="channel-name">Name</label>
              <input id="channel-name" name="name" maxLength={64} required placeholder="release-planning" autoFocus />
              <label htmlFor="channel-topic">Topic <span className="optional">(optional)</span></label>
              <input id="channel-topic" name="topic" maxLength={200} placeholder="what this channel is for" />
              <div className="err" role="alert">{channelErr}</div>
              <div className="modal-actions">
                <button type="button" className="btn" onClick={() => setChannelModal(false)}>Cancel</button>
                <button type="submit" className="btn primary">Create</button>
              </div>
            </form>
          </div>
        </div>
      )}

      {agentModal && (
        <div className="overlay" onClick={(e) => e.target === e.currentTarget && setAgentModal(null)}>
          <div className="modal-card agent-card" role="dialog" aria-modal="true">
            <h2>{agentModal === "edit" ? `@${agentForm.name}` : "New Bot"}</h2>
            <form onSubmit={saveAgent}>
              {agentModal === "create" && (
                <div className="job-templates">
                  {jobs.map((job) => (
                    <button key={job.job} type="button" className={agentForm.job === job.job ? "active" : ""} onClick={() => setAgentForm((f) => ({ ...f, job: job.job, prompt: job.prompt }))}>
                      {job.job}
                    </button>
                  ))}
                </div>
              )}
              <label htmlFor="agent-name">Name</label>
              <input id="agent-name" required maxLength={32} pattern="[A-Za-z0-9_\-]+" placeholder="piper" disabled={agentModal === "edit"} value={agentForm.name} onChange={(e) => setAgentForm((f) => ({ ...f, name: e.target.value }))} />
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
                {ALL_TOOLS.map((t) => (
                  <label key={t} className="check">
                    <input type="checkbox" checked={agentForm.tools.includes(t)} onChange={(e) => setAgentForm((f) => ({
                      ...f,
                      tools: e.target.checked ? [...f.tools, t] : f.tools.filter((x) => x !== t),
                    }))} />
                    {t}
                  </label>
                ))}
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
        <div className="brand">swarm<small>talk · code · paper</small></div>
        <div className="section-label">Bots</div>
        <div id="bots-list">
          {!allAgents.length && <div className="empty">No bots yet</div>}
          {allAgents.map((a) => (
            <button key={a.name} type="button" className={`bot-item${a.dm_channel_id === channel ? " active" : ""}`} onClick={() => switchChannel(a.dm_channel_id)}>
              <span className={`dot ${a.status || "idle"}`} />
              <span className="bot-meta">
                <span className="bot-name">{a.name}</span>
                <span className="bot-job">{a.job || roleLine(a.system_prompt)}</span>
              </span>
            </button>
          ))}
        </div>
        <button type="button" id="new-agent" onClick={openCreateAgent}>+ New Bot</button>
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
        <button type="button" id="new-channel" onClick={() => { setChannelErr(""); setChannelModal(true); }}>+ New channel</button>
        <div id="agents-box">
          <div id="groq-status" className={agentsReady ? "ready" : "missing"}>
            {agentsReady ? "Bots ready" : "Set GROQ_API_KEY in .env so bots can reply"}
          </div>
        </div>
        <div id="me">
          <div className="avatar">{initials(user || "?")}</div>
          <div className="me-meta">
            <div className="handle">{user || "anon"}</div>
            <div className={`sub ${wsStatus}`}>{wsStatus}</div>
          </div>
          <button type="button" className="btn ghost" onClick={() => { if (user) localStorage.removeItem(`swarm_token_${user}`); logout(); }}>Log out</button>
        </div>
      </nav>

      <main id="main">
        <div id="topbar">
          <button type="button" id="menu-btn" aria-label={sidebarOpen ? "Close sidebar" : "Open sidebar"} aria-expanded={sidebarOpen} onClick={() => setSidebarOpen((open) => !open)}>☰</button>
          <div className="channel-meta">
            <div className="name">{bot ? bot.name : `#${current.name || current.id}`}</div>
            <div className="topic">{bot ? (bot.job || current.topic || "Teammate") : (current.topic || "No topic set")}</div>
          </div>
          <div className="view-tabs" role="tablist" aria-label="Channel view">
            <button type="button" className={`tab${mainView === "talk" ? " active" : ""}`} onClick={() => setMainView("talk")}>Talk</button>
            <button type="button" className={`tab${mainView === "paper" ? " active" : ""}`} onClick={() => setMainView("paper")}>Paper</button>
          </div>
          {bot && <span className={`status-chip ${bot.status || "idle"}`}>{statusLabel(bot.status)}</span>}
          <span className="who">you're <span>{user || "anon"}</span></span>
          {bot && <button type="button" className="btn ghost" onClick={() => openAgentPanel(bot.name)}>Configure</button>}
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
                  ? `A blank channel. Give ${bot.name} a real task.`
                  : "A blank channel. Talk here, or @coder when you want a listing."}
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
            />
          ))}
          {streamRows.map((m) => (
            <MessageRow key={`stream-${m.author}`} m={m} grouped={false} reactions={[]} replyCount={0} onReply={() => {}} onReact={() => {}} onOpenThread={() => {}} onToggleEmoji={() => {}} />
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
          <div className="composer-hint">@swarm to talk · @coder for listings · Paper compiles code and TeX</div>
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
            {["files", "skills", "routines", "approvals"].map((tab) => (
              <button key={tab} type="button" className={`tab${panelTab === tab ? " active" : ""}`} onClick={() => setPanelTab(tab)}>{tab[0].toUpperCase() + tab.slice(1)}</button>
            ))}
          </div>
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
            {threadParent && <MessageRow m={threadParent} inThread reactions={reactions[threadParent.id]} replyCount={0} onReply={() => {}} onReact={sendReaction} onOpenThread={() => {}} onDelete={deleteMessage} onToggleEmoji={(id, el) => { const r = el.getBoundingClientRect(); setEmoji({ id, top: r.bottom + 8, left: Math.min(r.left, window.innerWidth - 220) }); }} />}
            {threadReplies.map((m, i) => (
              <MessageRow key={m.id} m={m} inThread grouped={groupedWith(i ? threadReplies[i - 1] : threadParent, m)} reactions={reactions[m.id]} replyCount={0} onReply={() => {}} onReact={sendReaction} onOpenThread={() => {}} onDelete={deleteMessage} onToggleEmoji={(id, el) => { const r = el.getBoundingClientRect(); setEmoji({ id, top: r.bottom + 8, left: Math.min(r.left, window.innerWidth - 220) }); }} />
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
    </div>
  );
}
