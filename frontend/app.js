const API = "";
const HISTORY_LIMIT = 50;

const state = {
  user: null,
  token: null,
  channel: "dm-swarm",
  ws: null,
  channels: [],
  agents: [],
  allAgents: [],
  jobs: [],
  skills: [],
  routines: [],
  approvals: [],
  computer: null,
  panelTab: "files",
  threadId: null,
  lastSeenId: 0,
  reactions: {},
  messages: {},
  replyCounts: {},
  reconnectAttempt: 0,
  intentionalClose: false,
  wsGen: 0,
  hasMore: false,
  mentionIndex: 0,
  streams: {},
  emojiMessageId: null,
  agentsReady: false,
  editingAgent: null,
  mentionMode: "at",
};

const el = (id) => document.getElementById(id);

function initials(name) {
  const parts = String(name || "?").trim().split(/\s+/);
  if (parts.length >= 2) return (parts[0][0] + parts[1][0]).toUpperCase();
  return String(name || "?").slice(0, 2).toUpperCase();
}

function fmtTime(ts) {
  if (!ts) return "";
  return new Date(ts * 1000).toTimeString().slice(0, 5);
}

function authHeaders(json = true) {
  const h = {};
  if (json) h["Content-Type"] = "application/json";
  if (state.token) h["Authorization"] = `Bearer ${state.token}`;
  return h;
}

function toast(msg, isError = false) {
  const n = el("toast");
  n.textContent = msg;
  n.classList.toggle("error", !!isError);
  n.classList.add("visible");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => n.classList.remove("visible"), 3200);
}

function setStatus(kind) {
  const n = el("me-status");
  n.textContent = kind;
  n.className = "sub " + kind;
}

function currentChannel() {
  return state.channels.find((c) => c.id === state.channel)
    || { id: state.channel, name: state.channel, topic: "" };
}

function isDm(channel) {
  const c = channel || currentChannel();
  return (c.kind || "") === "dm";
}

function botForChannel(channelId) {
  return state.allAgents.find((a) => a.dm_channel_id === channelId);
}

function updateComposerPlaceholder() {
  const c = currentChannel();
  const bot = botForChannel(c.id);
  if (bot) {
    el("msg-input").placeholder = `Message ${bot.name} — they already hear you`;
    return;
  }
  el("msg-input").placeholder = `Message #${c.name || c.id} — @mention a bot or /skill`;
}

function updateTopbar() {
  const c = currentChannel();
  const bot = botForChannel(c.id);
  const chip = el("bot-chip");
  const configure = el("configure-bot");
  if (bot) {
    el("topbar-name").textContent = bot.name;
    el("topbar-topic").textContent = bot.job || c.topic || "Teammate";
    chip.hidden = false;
    chip.className = `status-chip ${bot.status || "idle"}`;
    chip.textContent = ({
      working: "Working",
      needs_approval: "Needs approval",
      idle: "Idle",
    })[bot.status || "idle"] || bot.status;
    configure.hidden = false;
  } else {
    el("topbar-name").textContent = "#" + (c.name || c.id);
    el("topbar-topic").textContent = c.topic || "No topic set";
    chip.hidden = true;
    configure.hidden = true;
  }
}

function remember(m) {
  state.messages[m.id] = m;
  if (m.id && m.id > state.lastSeenId) state.lastSeenId = m.id;
  if (m.reactions) state.reactions[m.id] = m.reactions.slice();
  else if (!state.reactions[m.id]) state.reactions[m.id] = [];
}

function lastRow(container) {
  const nodes = container.querySelectorAll(":scope > .row");
  return nodes.length ? nodes[nodes.length - 1] : null;
}

function groupedWith(prev, m) {
  return !!(
    prev
    && !m.parent_id
    && prev.dataset.kind === m.author_kind
    && prev.dataset.author === m.author
    && !prev.classList.contains("streaming")
  );
}

function clearPlaceholders(container) {
  container.querySelectorAll(".empty-state, .loading-state").forEach((n) => n.remove());
}

function showEmpty(container, text) {
  if (container.querySelector(".row")) return;
  container.innerHTML = `<div class="empty-state">${text}</div>`;
}

function buildRow(m, { grouped = false, inThread = false } = {}) {
  const wrapper = document.createElement("div");
  wrapper.className = "row " + (m.author_kind || "human")
    + (grouped ? " grouped" : "")
    + (m.streaming ? " streaming" : "");
  if (m.id != null) wrapper.dataset.id = String(m.id);
  wrapper.dataset.author = m.author;
  wrapper.dataset.kind = m.author_kind || "human";
  if (m.streaming) wrapper.dataset.stream = m.author;

  const main = document.createElement("div");
  main.className = "row-main";
  const badge = m.author_kind === "agent" ? '<span class="badge">agent</span>' : "";
  main.innerHTML = `
    <div class="avatar">${initials(m.author)}</div>
    <div class="content">
      <div class="meta">
        <span class="who"></span>
        ${badge}
        <span class="ts">${fmtTime(m.created_at)}</span>
      </div>
      <div class="body"></div>
    </div>
  `;
  main.querySelector(".who").textContent = m.author;
  main.querySelector(".body").textContent = m.body || "";
  wrapper.appendChild(main);

  if (m.author_kind !== "system" && !m.streaming) {
    const actions = document.createElement("div");
    actions.className = "row-actions";
    const reply = document.createElement("button");
    reply.type = "button";
    reply.textContent = "Reply";
    reply.addEventListener("click", () => openThread(m.parent_id || m.id));
    const react = document.createElement("button");
    react.type = "button";
    react.textContent = "React";
    react.addEventListener("click", (ev) => openEmoji(m.id, ev.currentTarget));
    actions.append(reply, react);
    wrapper.appendChild(actions);
  }

  if (!m.streaming && m.id != null) {
    const reactionsRow = document.createElement("div");
    reactionsRow.className = "reactions";
    reactionsRow.dataset.reactions = String(m.id);
    renderReactions(reactionsRow, state.reactions[m.id] || m.reactions || []);
    wrapper.appendChild(reactionsRow);
  }

  if (!inThread && !m.parent_id && !m.streaming && m.id != null) {
    const n = state.replyCounts[m.id] || 0;
    if (n > 0) wrapper.appendChild(threadCountButton(m.id, n));
  }

  return wrapper;
}

function threadCountButton(id, n) {
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "thread-count";
  btn.textContent = n === 1 ? "1 reply" : `${n} replies`;
  btn.addEventListener("click", () => openThread(id));
  return btn;
}

function updateThreadCount(parentId) {
  const n = state.replyCounts[parentId] || 0;
  const row = el("log").querySelector(`.row[data-id="${parentId}"]`);
  if (!row) return;
  let btn = row.querySelector(".thread-count");
  if (n <= 0) {
    btn?.remove();
    return;
  }
  if (!btn) {
    btn = threadCountButton(parentId, n);
    row.appendChild(btn);
  } else {
    btn.textContent = n === 1 ? "1 reply" : `${n} replies`;
  }
}

function renderReactions(container, reactions) {
  container.innerHTML = "";
  const counts = {};
  for (const r of reactions) counts[r.emoji] = (counts[r.emoji] || 0) + 1;
  for (const [emoji, count] of Object.entries(counts)) {
    const chip = document.createElement("button");
    chip.type = "button";
    chip.className = "reaction-chip";
    chip.textContent = `${emoji} ${count}`;
    chip.addEventListener("click", () => sendReaction(container.dataset.reactions, emoji));
    container.appendChild(chip);
  }
}

function refreshReactionViews(messageId) {
  const list = state.reactions[messageId] || [];
  document.querySelectorAll(`[data-reactions="${messageId}"]`).forEach((node) => {
    renderReactions(node, list);
  });
}

function appendRoot(m) {
  const log = el("log");
  if (log.querySelector(`.row[data-id="${m.id}"]`)) return;
  clearPlaceholders(log);
  const prev = lastRow(log);
  log.appendChild(buildRow(m, { grouped: groupedWith(prev, m) }));
  log.scrollTop = log.scrollHeight;
}

function ingestLive(m) {
  const seen = !!state.messages[m.id];
  remember(m);
  if (seen) return;
  if (m.parent_id) {
    state.replyCounts[m.parent_id] = (state.replyCounts[m.parent_id] || 0) + 1;
    updateThreadCount(m.parent_id);
    if (state.threadId === m.parent_id) {
      const tlog = el("thread-log");
      if (!tlog.querySelector(`.row[data-id="${m.id}"]`)) {
        clearPlaceholders(tlog);
        tlog.appendChild(buildRow(m, { inThread: true, grouped: groupedWith(lastRow(tlog), m) }));
        tlog.scrollTop = tlog.scrollHeight;
      }
    }
    return;
  }
  if (m.author_kind === "agent") {
    const ph = state.streams[m.author];
    if (ph) {
      ph.remove();
      delete state.streams[m.author];
    }
    el("typing").textContent = "";
  }
  appendRoot(m);
}

async function sendReaction(messageId, emoji) {
  hideEmoji();
  try {
    const res = await fetch(`${API}/api/messages/${messageId}/reactions`, {
      method: "POST",
      headers: authHeaders(),
      body: JSON.stringify({ author: state.user, emoji }),
    });
    if (!res.ok) toast("Couldn't add reaction", true);
  } catch {
    toast("Couldn't add reaction", true);
  }
}

function openEmoji(messageId, anchor) {
  state.emojiMessageId = messageId;
  const pop = el("emoji-pop");
  pop.hidden = false;
  const r = anchor.getBoundingClientRect();
  pop.style.top = `${r.bottom + 8}px`;
  pop.style.left = `${Math.min(r.left, window.innerWidth - 220)}px`;
}

function hideEmoji() {
  el("emoji-pop").hidden = true;
  state.emojiMessageId = null;
}

el("emoji-pop").addEventListener("click", (ev) => {
  const chip = ev.target.closest("[data-emoji]");
  if (!chip || state.emojiMessageId == null) return;
  sendReaction(state.emojiMessageId, chip.dataset.emoji);
});

document.addEventListener("click", (ev) => {
  if (!ev.target.closest("#emoji-pop") && !ev.target.closest(".row-actions")) hideEmoji();
});

async function loadChannels() {
  const res = await fetch(`${API}/api/channels`);
  if (!res.ok) throw new Error("channels");
  state.channels = await res.json();
  const list = el("channel-list");
  list.innerHTML = "";
  const rooms = state.channels.filter((c) => c.kind !== "dm");
  if (!rooms.length) {
    list.innerHTML = `<li class="empty-state">No rooms</li>`;
    return;
  }
  for (const c of rooms) {
    const li = document.createElement("li");
    const btn = document.createElement("button");
    btn.type = "button";
    btn.textContent = c.name;
    btn.dataset.id = c.id;
    if (c.id === state.channel) btn.classList.add("active");
    btn.addEventListener("click", () => {
      closeSidebar();
      switchChannel(c.id);
    });
    li.appendChild(btn);
    list.appendChild(li);
  }
}

async function loadGroqStatus() {
  const n = el("groq-status");
  if (!n) return;
  try {
    const res = await fetch(`${API}/api/status`);
    const data = res.ok ? await res.json() : { groq: false, openrouter: false };
    state.agentsReady = !!(data.groq || data.openrouter);
    n.className = state.agentsReady ? "ready" : "missing";
    n.textContent = state.agentsReady
      ? "Bots ready"
      : "Set GROQ_API_KEY in .env so bots can reply";
  } catch {
    state.agentsReady = false;
    n.className = "missing";
    n.textContent = "Set GROQ_API_KEY in .env so bots can reply";
  }
}

function statusLabel(status) {
  return ({ working: "Working", needs_approval: "Needs approval", idle: "Idle" })[status] || status || "Idle";
}

function roleLine(prompt) {
  const text = String(prompt || "").replace(/\s+/g, " ").trim();
  if (!text) return "custom bot";
  const cut = text.search(/[.!?]/);
  const first = cut === -1 ? text : text.slice(0, cut + 1);
  return first.length > 72 ? first.slice(0, 69) + "…" : first;
}

function renderBots() {
  const box = el("bots-list");
  box.innerHTML = "";
  if (!state.allAgents.length) {
    box.innerHTML = `<div class="empty">No bots yet</div>`;
    return;
  }
  for (const a of state.allAgents) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "bot-item" + (a.dm_channel_id === state.channel ? " active" : "");
    btn.innerHTML = `<span class="dot ${a.status || "idle"}"></span>`;
    const meta = document.createElement("span");
    meta.className = "bot-meta";
    const name = document.createElement("span");
    name.className = "bot-name";
    name.textContent = a.name;
    const job = document.createElement("span");
    job.className = "bot-job";
    job.textContent = a.job || roleLine(a.system_prompt);
    meta.append(name, job);
    btn.appendChild(meta);
    btn.addEventListener("click", () => {
      closeSidebar();
      switchChannel(a.dm_channel_id);
    });
    box.appendChild(btn);
  }
}

async function loadAllAgents() {
  try {
    const res = await fetch(`${API}/api/agents`);
    state.allAgents = res.ok ? await res.json() : [];
  } catch {
    state.allAgents = [];
  }
  fillRoutineAgents();
  renderBots();
}

async function loadAgents(channelId) {
  try {
    const res = await fetch(`${API}/api/agents?channel_id=${encodeURIComponent(channelId)}`);
    state.agents = res.ok ? await res.json() : [];
  } catch {
    state.agents = [];
  }
}

async function loadHistory(channelId, { beforeId } = {}) {
  const params = new URLSearchParams({ limit: String(HISTORY_LIMIT) });
  if (beforeId) params.set("before_id", String(beforeId));
  const res = await fetch(`${API}/api/channels/${channelId}/messages?${params}`);
  if (!res.ok) throw new Error("history");
  return res.json();
}

function applyHistoryPage(history, { prepend } = {}) {
  state.hasMore = history.length === HISTORY_LIMIT;
  el("load-earlier").hidden = !state.hasMore;

  const bumped = new Set();
  for (const m of history) {
    remember(m);
    if (m.parent_id) {
      state.replyCounts[m.parent_id] = (state.replyCounts[m.parent_id] || 0) + 1;
      bumped.add(m.parent_id);
    }
  }

  const roots = history.filter((m) => !m.parent_id);
  const log = el("log");
  if (!prepend) {
    log.innerHTML = "";
    state.streams = {};
    if (!roots.length) {
      const bot = botForChannel(state.channel);
      showEmpty(log, bot
        ? `No messages yet. Give ${bot.name} a real task — outcome, sources, and what needs your approval.`
        : "No messages yet. Bots live in this room — try @swarm");
      return;
    }
    roots.forEach((m) => {
      log.appendChild(buildRow(m, { grouped: groupedWith(lastRow(log), m) }));
    });
    log.scrollTop = log.scrollHeight;
    return;
  }

  const prevHeight = log.scrollHeight;
  clearPlaceholders(log);
  const first = log.firstChild;
  for (const m of roots) {
    if (log.querySelector(`.row[data-id="${m.id}"]`)) continue;
    const row = buildRow(m, { grouped: false });
    log.insertBefore(row, first);
  }
  bumped.forEach((id) => updateThreadCount(id));
  log.scrollTop = log.scrollHeight - prevHeight;
}

async function switchChannel(id) {
  state.channel = id;
  state.threadId = null;
  state.lastSeenId = 0;
  state.messages = {};
  state.reactions = {};
  state.replyCounts = {};
  state.streams = {};
  closeThread();
  document.querySelectorAll("#channel-list button").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.id === id);
  });
  renderBots();
  updateTopbar();
  updateComposerPlaceholder();
  el("log").innerHTML = `<div class="loading-state">Loading messages…</div>`;
  el("load-earlier").hidden = true;

  try {
    const history = await loadHistory(id);
    applyHistoryPage(history);
  } catch {
    el("log").innerHTML = `<div class="empty-state">Couldn't load messages.</div>`;
    toast("Couldn't load channel history", true);
  }

  await loadAgents(id);
  await loadGroqStatus();
  await loadApprovals();
  await loadComputer();
  connectWs(id);
}

function connectWs(channelId) {
  if (state.reconnectTimer) {
    clearTimeout(state.reconnectTimer);
    state.reconnectTimer = null;
  }
  if (state.ws) {
    state.intentionalClose = true;
    state.ws.close();
  }
  const gen = ++state.wsGen;
  state.intentionalClose = false;
  setStatus("connecting");
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/ws/${channelId}`);
  ws.onopen = () => {
    if (gen !== state.wsGen) {
      ws.close();
      return;
    }
    const payload = { token: state.token };
    if (state.lastSeenId) payload.last_seen_id = state.lastSeenId;
    ws.send(JSON.stringify(payload));
    setStatus("online");
    state.reconnectAttempt = 0;
  };
  ws.onmessage = (ev) => {
    if (gen !== state.wsGen) return;
    let data;
    try {
      data = JSON.parse(ev.data);
    } catch {
      return;
    }
    handleWsEvent(data);
  };
  ws.onclose = (ev) => {
    if (gen !== state.wsGen) return;
    setStatus("offline");
    if (ev.code === 4001) {
      toast("Session expired — pick a handle again", true);
      logout({ skipClose: true });
      return;
    }
    if (!state.intentionalClose && state.token && state.channel === channelId) {
      const delay = Math.min(15000, 1000 * (2 ** state.reconnectAttempt));
      state.reconnectAttempt += 1;
      toast("Disconnected, reconnecting…");
      state.reconnectTimer = setTimeout(() => connectWs(channelId), delay);
    }
  };
  ws.onerror = () => {};
  state.ws = ws;
}

function handleWsEvent(data) {
  if (data.type === "message") {
    ingestLive(Object.assign({ reactions: [] }, data.message));
  } else if (data.type === "typing") {
    el("typing").textContent = `${data.author} is typing…`;
    clearTimeout(handleWsEvent._typing);
    handleWsEvent._typing = setTimeout(() => {
      if (el("typing").textContent.startsWith(data.author)) el("typing").textContent = "";
    }, 15000);
  } else if (data.type === "reaction") {
    const list = state.reactions[data.message_id] || [];
    if (!list.some((r) => r.author === data.author && r.emoji === data.emoji)) {
      list.push({ author: data.author, emoji: data.emoji });
      state.reactions[data.message_id] = list;
    }
    refreshReactionViews(data.message_id);
  } else if (data.type === "error") {
    toast(data.detail || "Something went wrong", true);
  } else if (data.type === "agent_stream_start") {
    startStream(data.author);
  } else if (data.type === "agent_token") {
    appendStream(data.author, data.delta || "");
  } else if (data.type === "bot_status") {
    const bot = state.allAgents.find((a) => a.name === data.name);
    if (bot) bot.status = data.status;
    renderBots();
    updateTopbar();
  } else if (data.type === "approval") {
    upsertApproval(data.approval);
    renderApprovals();
    if (data.approval && data.approval.channel_id === state.channel) {
      renderApprovalDock();
    }
  }
}

function startStream(author) {
  const existing = state.streams[author];
  if (existing) existing.remove();
  clearPlaceholders(el("log"));
  const row = buildRow({
    author, author_kind: "agent", body: "", streaming: true, created_at: Date.now() / 1000,
  });
  el("log").appendChild(row);
  el("log").scrollTop = el("log").scrollHeight;
  state.streams[author] = row;
}

function appendStream(author, delta) {
  const row = state.streams[author];
  if (!row) {
    startStream(author);
    return appendStream(author, delta);
  }
  const body = row.querySelector(".body");
  body.textContent += delta;
  el("log").scrollTop = el("log").scrollHeight;
}

async function openThread(id) {
  state.threadId = id;
  document.body.classList.add("thread-open");
  el("thread-panel").hidden = false;
  el("thread-log").innerHTML = `<div class="loading-state">Loading thread…</div>`;
  try {
    const res = await fetch(`${API}/api/messages/${id}/thread`);
    if (!res.ok) throw new Error("thread");
    const data = await res.json();
    remember(data.parent);
    data.replies.forEach(remember);
    el("thread-sub").textContent = `with ${data.parent.author}`;
    const tlog = el("thread-log");
    tlog.innerHTML = "";
    tlog.appendChild(buildRow(data.parent, { inThread: true }));
    data.replies.forEach((m) => {
      tlog.appendChild(buildRow(m, { inThread: true, grouped: groupedWith(lastRow(tlog), m) }));
    });
    tlog.scrollTop = tlog.scrollHeight;
    el("thread-input").focus();
  } catch {
    el("thread-log").innerHTML = `<div class="empty-state">Couldn't load this thread.</div>`;
    toast("Couldn't load thread", true);
  }
}

function closeThread() {
  state.threadId = null;
  document.body.classList.remove("thread-open");
  el("thread-panel").hidden = true;
  el("thread-log").innerHTML = "";
}

function sendFrom(input, parentId) {
  const body = input.value.trim();
  if (!body) return;
  if (!state.ws || state.ws.readyState !== WebSocket.OPEN) {
    toast("Not connected — wait a moment and try again", true);
    return;
  }
  if (!state.agentsReady && /@[a-zA-Z0-9_\-]+/.test(body)) {
    toast("Bots can't reply until GROQ_API_KEY is set in .env", true);
  }
  const payload = { body };
  if (parentId) payload.parent_id = parentId;
  state.ws.send(JSON.stringify(payload));
  input.value = "";
  autosize(input);
  hideMention();
}

function autosize(textarea) {
  textarea.style.height = "auto";
  textarea.style.height = `${Math.min(textarea.scrollHeight, 160)}px`;
}

function mentionQuery(textarea) {
  const pos = textarea.selectionStart;
  const before = textarea.value.slice(0, pos);
  const at = before.match(/(^|\s)@([a-zA-Z0-9_\-]*)$/);
  if (at) return { mode: "at", query: at[2].toLowerCase() };
  const slash = before.match(/(^|\s)\/([a-zA-Z0-9_\-]*)$/);
  if (slash) return { mode: "slash", query: slash[2].toLowerCase() };
  return null;
}

function hideMention() {
  el("mention-menu").hidden = true;
}

function renderMentionMenu(info) {
  const menu = el("mention-menu");
  let matches = [];
  if (info.mode === "at") {
    matches = (state.agents.length ? state.agents : state.allAgents)
      .filter((a) => a.name.toLowerCase().startsWith(info.query))
      .map((a) => ({ label: `@${a.name}`, value: a.name, kind: "at" }));
  } else {
    matches = state.skills
      .filter((s) => s.name.toLowerCase().startsWith(info.query))
      .map((s) => ({ label: `/${s.name}`, value: s.name, kind: "slash" }));
  }
  if (!matches.length) {
    hideMention();
    return;
  }
  if (state.mentionIndex >= matches.length) state.mentionIndex = 0;
  menu.innerHTML = "";
  matches.forEach((item, i) => {
    const li = document.createElement("li");
    const btn = document.createElement("button");
    btn.type = "button";
    btn.setAttribute("role", "option");
    btn.textContent = item.label;
    if (i === state.mentionIndex) btn.classList.add("active");
    btn.addEventListener("mousedown", (ev) => {
      ev.preventDefault();
      insertMention(item.value, el("msg-input"), item.kind);
    });
    li.appendChild(btn);
    menu.appendChild(li);
  });
  menu.hidden = false;
}

function insertMention(name, textarea = el("msg-input"), kind = "at") {
  const start = textarea.selectionStart;
  const value = textarea.value;
  const before = value.slice(0, start);
  const after = value.slice(textarea.selectionEnd);
  const needle = kind === "slash" ? /(^|\s)\/[a-zA-Z0-9_\-]*$/ : /(^|\s)@[a-zA-Z0-9_\-]*$/;
  const mark = kind === "slash" ? "/" : "@";
  const replaced = before.replace(needle, `$1${mark}${name} `);
  const usedReplace = replaced !== before;
  textarea.value = usedReplace ? replaced + after : `${before}${mark}${name} ${after}`;
  const pos = usedReplace ? replaced.length : before.length + name.length + 2;
  textarea.focus();
  textarea.setSelectionRange(pos, pos);
  hideMention();
  autosize(textarea);
}

function onComposerInput() {
  autosize(el("msg-input"));
  const q = mentionQuery(el("msg-input"));
  if (q == null) {
    hideMention();
    return;
  }
  renderMentionMenu(q);
}

function onComposerKey(ev) {
  const menu = el("mention-menu");
  if (!menu.hidden) {
    const buttons = [...menu.querySelectorAll("button")];
    if (ev.key === "ArrowDown") {
      ev.preventDefault();
      state.mentionIndex = (state.mentionIndex + 1) % buttons.length;
      const info = mentionQuery(el("msg-input"));
      if (info) renderMentionMenu(info);
      return;
    }
    if (ev.key === "ArrowUp") {
      ev.preventDefault();
      state.mentionIndex = (state.mentionIndex - 1 + buttons.length) % buttons.length;
      const info = mentionQuery(el("msg-input"));
      if (info) renderMentionMenu(info);
      return;
    }
    if (ev.key === "Enter" || ev.key === "Tab") {
      ev.preventDefault();
      const raw = buttons[state.mentionIndex]?.textContent || "";
      const kind = raw.startsWith("/") ? "slash" : "at";
      const name = raw.replace(/^[@/]/, "");
      if (name) insertMention(name, el("msg-input"), kind);
      return;
    }
    if (ev.key === "Escape") {
      ev.preventDefault();
      hideMention();
      return;
    }
  }
  if (ev.key === "Enter" && !ev.shiftKey) {
    ev.preventDefault();
    sendFrom(el("msg-input"), null);
  }
}

function openChannelModal() {
  el("channel-err").textContent = "";
  el("channel-form").reset();
  el("modal").hidden = false;
  el("channel-name").focus();
}

function closeChannelModal() {
  el("modal").hidden = true;
}

function fillScopeOptions(selected) {
  const sel = el("agent-scope");
  sel.innerHTML = `<option value="">Every room (plus their 1:1)</option>`;
  for (const c of state.channels.filter((ch) => ch.kind !== "dm")) {
    const opt = document.createElement("option");
    opt.value = c.id;
    opt.textContent = `#${c.name}`;
    if (selected && selected === c.id) opt.selected = true;
    sel.appendChild(opt);
  }
}

function selectedTools() {
  return [...document.querySelectorAll('#agent-form input[name="tool"]:checked')].map((n) => n.value);
}

function setToolChecks(tools) {
  const set = new Set(tools || []);
  document.querySelectorAll('#agent-form input[name="tool"]').forEach((n) => {
    n.checked = set.size ? set.has(n.value) : true;
  });
}

function renderMemories(memories) {
  const box = el("agent-memories");
  const list = el("agent-memory-list");
  list.innerHTML = "";
  if (!memories || !memories.length) {
    box.hidden = true;
    return;
  }
  box.hidden = false;
  for (const m of memories) {
    const li = document.createElement("li");
    const scope = m.channel_id ? `#${m.channel_id}` : "global";
    li.textContent = `${m.kind} · ${scope}: ${m.body}`;
    list.appendChild(li);
  }
}

function renderJobTemplates() {
  const box = el("job-templates");
  box.innerHTML = "";
  box.hidden = false;
  for (const job of state.jobs) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.textContent = job.job;
    btn.addEventListener("click", () => {
      box.querySelectorAll("button").forEach((n) => n.classList.remove("active"));
      btn.classList.add("active");
      el("agent-job").value = job.job;
      el("agent-prompt").value = job.prompt;
    });
    box.appendChild(btn);
  }
}

function openCreateAgent() {
  state.editingAgent = null;
  el("agent-err").textContent = "";
  el("agent-form").reset();
  el("agent-name").disabled = false;
  el("agent-model").value = "llama-3.3-70b-versatile";
  el("agent-window").value = "12";
  el("agent-job").value = "";
  setToolChecks([
    "read_only_shell", "search_channel_history", "remember", "recall",
    "list_workspace", "write_workspace", "save_skill", "request_approval",
  ]);
  fillScopeOptions("");
  renderMemories([]);
  renderJobTemplates();
  el("agent-modal-title").textContent = "New Bot";
  el("agent-save").textContent = "Create";
  el("agent-mention").hidden = true;
  el("agent-modal").hidden = false;
  el("agent-name").focus();
}

async function openAgentPanel(name) {
  el("agent-err").textContent = "";
  try {
    const res = await fetch(`${API}/api/agents/${encodeURIComponent(name)}`);
    if (!res.ok) throw new Error("agent");
    const a = await res.json();
    state.editingAgent = a.name;
    el("agent-name").value = a.name;
    el("agent-name").disabled = true;
    el("agent-prompt").value = a.system_prompt || "";
    el("agent-job").value = a.job || "";
    el("agent-model").value = a.model || "";
    el("agent-window").value = String(a.history_window || 12);
    fillScopeOptions(a.channel_scope || "");
    setToolChecks(a.tools);
    renderMemories(a.memories);
    el("job-templates").hidden = true;
    el("agent-modal-title").textContent = `@${a.name}`;
    el("agent-save").textContent = "Save";
    el("agent-mention").hidden = false;
    el("agent-modal").hidden = false;
    el("agent-prompt").focus();
  } catch {
    toast("Couldn't load that agent", true);
  }
}

function closeAgentModal() {
  el("agent-modal").hidden = true;
  state.editingAgent = null;
}

async function saveAgent(ev) {
  ev.preventDefault();
  el("agent-err").textContent = "";
  const name = el("agent-name").value.trim();
  const system_prompt = el("agent-prompt").value.trim();
  const model = el("agent-model").value.trim() || "llama-3.3-70b-versatile";
  const channel_scope = el("agent-scope").value || null;
  const history_window = Number(el("agent-window").value) || 12;
  const job = el("agent-job").value.trim() || "Teammate";
  const tools = selectedTools();
  if (!name || !system_prompt) {
    el("agent-err").textContent = "name and prompt are required";
    return;
  }
  const payload = { system_prompt, model, channel_scope, history_window, max_tool_calls: 3, tools, job };
  try {
    let res;
    if (state.editingAgent) {
      res = await fetch(`${API}/api/agents/${encodeURIComponent(state.editingAgent)}`, {
        method: "PATCH",
        headers: authHeaders(),
        body: JSON.stringify(payload),
      });
    } else {
      res = await fetch(`${API}/api/agents`, {
        method: "POST",
        headers: authHeaders(),
        body: JSON.stringify({ name, ...payload }),
      });
    }
    if (res.ok) {
      const wasEdit = !!state.editingAgent;
      const created = wasEdit ? null : await res.json();
      closeAgentModal();
      await loadAllAgents();
      await loadChannels();
      toast(wasEdit ? `Updated @${name}` : `Created @${name}`);
      if (!wasEdit && created && created.dm_channel_id) switchChannel(created.dm_channel_id);
      return;
    }
    if (res.status === 409) el("agent-err").textContent = "that agent name is taken";
    else if (res.status === 404) el("agent-err").textContent = "channel or agent not found";
    else el("agent-err").textContent = "couldn't save agent";
  } catch {
    el("agent-err").textContent = "couldn't save agent";
  }
}

function applyToolsVisibility() {
  const show = localStorage.getItem("swarm_show_tools") === "1";
  document.body.classList.toggle("hide-tools", !show);
  const btn = el("toggle-tools");
  if (btn) btn.textContent = show ? "Hide tool log" : "Show tool log";
}

function closeSidebar() {
  document.body.classList.remove("sidebar-open");
  el("sidebar-backdrop").hidden = true;
}

function openSidebar() {
  document.body.classList.add("sidebar-open");
  el("sidebar-backdrop").hidden = false;
}

async function loginAs(handle) {
  el("login-err").textContent = "";
  let res;
  try {
    res = await fetch(`${API}/api/register`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ handle }),
    });
  } catch {
    el("login-err").textContent = "couldn't reach the relay";
    return null;
  }
  if (res.ok) {
    const data = await res.json();
    return data.token;
  }
  if (res.status === 409) {
    const saved = localStorage.getItem(`swarm_token_${handle}`);
    if (saved) return saved;
    el("login-err").textContent = "handle taken and no saved session — pick another";
    return null;
  }
  el("login-err").textContent = "registration failed";
  return null;
}

function setIdentity(handle) {
  state.user = handle;
  el("topbar-user").textContent = handle;
  el("me-handle").textContent = handle;
  el("me-avatar").textContent = initials(handle);
}

async function enterWorkspace(handle, token) {
  state.token = token;
  localStorage.setItem(`swarm_token_${handle}`, token);
  localStorage.setItem("swarm_last_handle", handle);
  setIdentity(handle);
  el("login").style.display = "none";
  await Promise.all([loadChannels(), loadAllAgents(), loadJobs(), loadSkills(), loadRoutines()]);
  const preferred = state.channels.some((c) => c.id === "dm-swarm") ? "dm-swarm" : (state.channels[0]?.id || "general");
  if (!state.channels.some((c) => c.id === state.channel)) state.channel = preferred;
  await switchChannel(state.channel);
}

function logout({ skipClose } = {}) {
  if (state.reconnectTimer) clearTimeout(state.reconnectTimer);
  state.intentionalClose = true;
  if (!skipClose && state.ws) state.ws.close();
  state.ws = null;
  state.token = null;
  state.user = null;
  setStatus("offline");
  el("login").style.display = "flex";
  el("login-input").focus();
}

el("login-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const handle = el("login-input").value.trim();
  if (!handle) return;
  el("login-submit").disabled = true;
  const token = await loginAs(handle);
  el("login-submit").disabled = false;
  if (!token) return;
  try {
    await enterWorkspace(handle, token);
  } catch {
    el("login-err").textContent = "couldn't load the workspace";
  }
});

el("logout").addEventListener("click", () => {
  const handle = state.user;
  if (handle) localStorage.removeItem(`swarm_token_${handle}`);
  logout();
});

el("new-channel").addEventListener("click", openChannelModal);
el("channel-cancel").addEventListener("click", closeChannelModal);
el("modal").addEventListener("click", (ev) => {
  if (ev.target === el("modal")) closeChannelModal();
});
el("new-agent").addEventListener("click", openCreateAgent);
el("agent-cancel").addEventListener("click", closeAgentModal);
el("agent-modal").addEventListener("click", (ev) => {
  if (ev.target === el("agent-modal")) closeAgentModal();
});
el("agent-form").addEventListener("submit", saveAgent);
el("agent-mention").addEventListener("click", () => {
  const name = state.editingAgent || el("agent-name").value.trim();
  if (!name) return;
  const bot = state.allAgents.find((a) => a.name === name);
  closeAgentModal();
  if (bot) switchChannel(bot.dm_channel_id);
  else insertMention(name);
});
el("toggle-tools").addEventListener("click", () => {
  const show = localStorage.getItem("swarm_show_tools") === "1";
  localStorage.setItem("swarm_show_tools", show ? "0" : "1");
  applyToolsVisibility();
});

el("channel-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const name = el("channel-name").value.trim();
  const topic = el("channel-topic").value.trim();
  el("channel-err").textContent = "";
  if (!name) return;
  try {
    const res = await fetch(`${API}/api/channels`, {
      method: "POST",
      headers: authHeaders(),
      body: JSON.stringify({ name, topic }),
    });
    if (res.ok) {
      const c = await res.json();
      closeChannelModal();
      await loadChannels();
      switchChannel(c.id);
    } else if (res.status === 409) {
      el("channel-err").textContent = "that channel already exists";
    } else {
      el("channel-err").textContent = "couldn't create channel";
    }
  } catch {
    el("channel-err").textContent = "couldn't create channel";
  }
});

el("send-btn").addEventListener("click", () => sendFrom(el("msg-input"), null));
el("msg-input").addEventListener("keydown", onComposerKey);
el("msg-input").addEventListener("input", onComposerInput);

el("thread-send").addEventListener("click", () => {
  if (state.threadId) sendFrom(el("thread-input"), state.threadId);
});
el("thread-input").addEventListener("keydown", (ev) => {
  if (ev.key === "Enter" && !ev.shiftKey) {
    ev.preventDefault();
    if (state.threadId) sendFrom(el("thread-input"), state.threadId);
  }
});
el("thread-input").addEventListener("input", () => autosize(el("thread-input")));
el("thread-close").addEventListener("click", closeThread);

el("load-earlier").addEventListener("click", async () => {
  const ids = Object.keys(state.messages).map(Number).filter(Boolean);
  const oldest = Math.min(...ids);
  if (!Number.isFinite(oldest)) return;
  el("load-earlier").disabled = true;
  try {
    const page = await loadHistory(state.channel, { beforeId: oldest });
    applyHistoryPage(page, { prepend: true });
  } catch {
    toast("Couldn't load earlier messages", true);
  }
  el("load-earlier").disabled = false;
});

el("menu-btn").addEventListener("click", openSidebar);
el("sidebar-backdrop").addEventListener("click", closeSidebar);

function setComputerOpen(open) {
  document.body.classList.toggle("computer-open", open);
  localStorage.setItem("swarm_computer", open ? "1" : "0");
}

function showPanelTab(tab) {
  state.panelTab = tab;
  document.querySelectorAll(".panel-tabs .tab").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.tab === tab);
  });
  ["files", "skills", "routines", "approvals"].forEach((name) => {
    const node = el(`panel-${name}`);
    if (node) node.hidden = name !== tab;
  });
}

async function loadJobs() {
  try {
    const res = await fetch(`${API}/api/jobs`);
    state.jobs = res.ok ? await res.json() : [];
  } catch {
    state.jobs = [];
  }
}

async function loadSkills() {
  try {
    const res = await fetch(`${API}/api/skills`);
    state.skills = res.ok ? await res.json() : [];
  } catch {
    state.skills = [];
  }
  renderSkills();
}

function renderSkills() {
  const list = el("skill-list");
  if (!list) return;
  list.innerHTML = "";
  if (!state.skills.length) {
    list.innerHTML = `<li class="empty-state">No skills yet. Save a process that worked.</li>`;
    return;
  }
  for (const s of state.skills) {
    const li = document.createElement("li");
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "linkish";
    btn.textContent = `/${s.name}`;
    btn.addEventListener("click", () => insertMention(s.name, el("msg-input"), "slash"));
    const del = document.createElement("button");
    del.type = "button";
    del.className = "btn ghost";
    del.textContent = "Delete";
    del.addEventListener("click", async () => {
      const res = await fetch(`${API}/api/skills/${s.id}`, { method: "DELETE", headers: authHeaders(false) });
      if (res.ok) loadSkills();
    });
    li.append(btn, document.createTextNode(" "), del);
    list.appendChild(li);
  }
}

function fillRoutineAgents() {
  const sel = el("routine-agent");
  if (!sel) return;
  const current = sel.value;
  sel.innerHTML = "";
  for (const a of state.allAgents) {
    const opt = document.createElement("option");
    opt.value = a.name;
    opt.textContent = a.name;
    sel.appendChild(opt);
  }
  if (current) sel.value = current;
  const owner = botForChannel(state.channel);
  if (owner) sel.value = owner.name;
}

async function loadRoutines() {
  try {
    const res = await fetch(`${API}/api/routines`);
    state.routines = res.ok ? await res.json() : [];
  } catch {
    state.routines = [];
  }
  renderRoutines();
}

function renderRoutines() {
  const list = el("routine-list");
  if (!list) return;
  list.innerHTML = "";
  if (!state.routines.length) {
    list.innerHTML = `<li class="empty-state">No routines. Automate a skill after it is reliable.</li>`;
    return;
  }
  for (const r of state.routines) {
    const li = document.createElement("li");
    const title = document.createElement("div");
    title.textContent = `${r.title} · @${r.agent_name} · every ${r.interval_minutes}m`;
    const meta = document.createElement("div");
    meta.className = "bot-job";
    meta.textContent = r.enabled ? "enabled" : "paused";
    const run = document.createElement("button");
    run.type = "button";
    run.className = "btn ghost";
    run.textContent = "Test run";
    run.addEventListener("click", async () => {
      const res = await fetch(`${API}/api/routines/${r.id}/run`, { method: "POST", headers: authHeaders() });
      toast(res.ok ? "Routine started" : "Couldn't start routine", !res.ok);
    });
    const toggle = document.createElement("button");
    toggle.type = "button";
    toggle.className = "btn ghost";
    toggle.textContent = r.enabled ? "Pause" : "Enable";
    toggle.addEventListener("click", async () => {
      await fetch(`${API}/api/routines/${r.id}`, {
        method: "PATCH", headers: authHeaders(),
        body: JSON.stringify({ enabled: !r.enabled }),
      });
      loadRoutines();
    });
    const del = document.createElement("button");
    del.type = "button";
    del.className = "btn ghost";
    del.textContent = "Delete";
    del.addEventListener("click", async () => {
      await fetch(`${API}/api/routines/${r.id}`, { method: "DELETE", headers: authHeaders(false) });
      loadRoutines();
    });
    li.append(title, meta, run, toggle, del);
    list.appendChild(li);
  }
}

function upsertApproval(row) {
  if (!row) return;
  const i = state.approvals.findIndex((a) => a.id === row.id);
  if (i >= 0) state.approvals[i] = row;
  else state.approvals.unshift(row);
}

async function loadApprovals() {
  try {
    const res = await fetch(`${API}/api/approvals?status=pending`);
    state.approvals = res.ok ? await res.json() : [];
  } catch {
    state.approvals = [];
  }
  renderApprovals();
  renderApprovalDock();
}

function renderApprovals() {
  const list = el("approval-list");
  if (!list) return;
  const pending = state.approvals.filter((a) => a.status === "pending");
  list.innerHTML = "";
  if (!pending.length) {
    list.innerHTML = `<li class="empty-state">Nothing waiting. Consequential actions pause here.</li>`;
    return;
  }
  for (const a of pending) list.appendChild(approvalItem(a));
}

function renderApprovalDock() {
  const dock = el("approval-dock");
  if (!dock) return;
  dock.innerHTML = "";
  const pending = state.approvals.filter((a) => a.status === "pending" && a.channel_id === state.channel);
  pending.forEach((a) => dock.appendChild(approvalCard(a)));
}

function approvalItem(a) {
  const li = document.createElement("li");
  li.appendChild(approvalCard(a));
  return li;
}

function approvalCard(a) {
  const card = document.createElement("div");
  card.className = "approval-card";
  card.innerHTML = `<div class="who">${a.agent_name} needs approval</div><p></p>`;
  card.querySelector("p").textContent = a.detail ? `${a.action} — ${a.detail}` : a.action;
  const actions = document.createElement("div");
  actions.className = "actions";
  const allow = document.createElement("button");
  allow.type = "button";
  allow.className = "btn primary";
  allow.textContent = "Allow once";
  allow.addEventListener("click", () => resolveApproval(a.id, "approved"));
  const deny = document.createElement("button");
  deny.type = "button";
  deny.className = "btn";
  deny.textContent = "Deny";
  deny.addEventListener("click", () => resolveApproval(a.id, "denied"));
  actions.append(allow, deny);
  card.appendChild(actions);
  return card;
}

async function resolveApproval(id, status) {
  const res = await fetch(`${API}/api/approvals/${id}/resolve`, {
    method: "POST", headers: authHeaders(), body: JSON.stringify({ status }),
  });
  if (!res.ok) {
    toast("Couldn't resolve approval", true);
    return;
  }
  const updated = await res.json();
  upsertApproval(updated);
  renderApprovals();
  renderApprovalDock();
}

async function loadComputer() {
  try {
    const res = await fetch(`${API}/api/computer`);
    state.computer = res.ok ? await res.json() : null;
  } catch {
    state.computer = null;
  }
  renderComputer();
}

function renderComputer() {
  const data = state.computer;
  el("computer-note").textContent = data?.note || "";
  el("computer-sub").textContent = data
    ? `Shared workspace · ${data.files.length} file${data.files.length === 1 ? "" : "s"}`
    : "Shared workspace";
  const list = el("file-list");
  list.innerHTML = "";
  if (!data || !data.files.length) {
    list.innerHTML = `<li class="empty-state">Workspace is empty. Ask a bot to write a file here.</li>`;
  } else {
    for (const f of data.files) {
      const li = document.createElement("li");
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "linkish";
      btn.textContent = `${f.path} (${f.size} B)`;
      btn.addEventListener("click", () => previewFile(f.path));
      li.appendChild(btn);
      list.appendChild(li);
    }
  }
  const act = el("activity-list");
  act.innerHTML = "";
  for (const m of (data?.activity || []).slice(0, 12)) {
    const li = document.createElement("li");
    li.textContent = m.body;
    act.appendChild(li);
  }
}

async function previewFile(path) {
  const preview = el("file-preview");
  try {
    const res = await fetch(`${API}/api/computer/file?path=${encodeURIComponent(path)}`);
    if (!res.ok) throw new Error("file");
    const data = await res.json();
    preview.hidden = false;
    preview.textContent = data.content;
  } catch {
    toast("Couldn't open that file", true);
  }
}

el("toggle-computer").addEventListener("click", () => {
  setComputerOpen(!document.body.classList.contains("computer-open"));
});
el("computer-close").addEventListener("click", () => setComputerOpen(false));
document.querySelectorAll(".panel-tabs .tab").forEach((btn) => {
  btn.addEventListener("click", () => showPanelTab(btn.dataset.tab));
});
el("configure-bot").addEventListener("click", () => {
  const bot = botForChannel(state.channel);
  if (bot) openAgentPanel(bot.name);
});
el("skill-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const name = el("skill-name").value.trim();
  const body = el("skill-body").value.trim();
  if (!name || !body) return;
  const res = await fetch(`${API}/api/skills`, {
    method: "POST", headers: authHeaders(), body: JSON.stringify({ name, body }),
  });
  if (res.ok) {
    el("skill-form").reset();
    loadSkills();
    toast(`Saved /${name}`);
  } else toast("Couldn't save skill", true);
});
el("routine-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const payload = {
    agent_name: el("routine-agent").value,
    title: el("routine-title").value.trim(),
    instructions: el("routine-instructions").value.trim(),
    interval_minutes: Number(el("routine-interval").value) || 60,
    enabled: true,
  };
  if (!payload.agent_name || !payload.title || !payload.instructions) return;
  const res = await fetch(`${API}/api/routines`, {
    method: "POST", headers: authHeaders(), body: JSON.stringify(payload),
  });
  if (res.ok) {
    el("routine-form").reset();
    fillRoutineAgents();
    loadRoutines();
    toast("Routine created");
  } else toast("Couldn't create routine", true);
});

const origIngest = ingestLive;
ingestLive = function (m) {
  origIngest(m);
  if (m.author_kind === "system") loadComputer();
};

(async function init() {
  applyToolsVisibility();
  if (localStorage.getItem("swarm_computer") === "0") setComputerOpen(false);
  const saved = localStorage.getItem("swarm_last_handle");
  if (saved) el("login-input").value = saved;
  const token = saved && localStorage.getItem(`swarm_token_${saved}`);
  if (saved && token) {
    try {
      await enterWorkspace(saved, token);
      return;
    } catch {
      /* show login */
    }
  }
})();
