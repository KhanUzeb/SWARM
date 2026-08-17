const API = "";
const HISTORY_LIMIT = 50;

const state = {
  user: null,
  token: null,
  channel: "general",
  ws: null,
  channels: [],
  agents: [],
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

function updateComposerPlaceholder() {
  const c = currentChannel();
  el("msg-input").placeholder = `Message #${c.name || c.id} — try @swarm`;
}

function updateTopbar() {
  const c = currentChannel();
  el("topbar-name").textContent = "#" + (c.name || c.id);
  el("topbar-topic").textContent = c.topic || "No topic set";
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
  if (!state.channels.length) {
    list.innerHTML = `<li class="empty-state">No channels</li>`;
    return;
  }
  for (const c of state.channels) {
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
      ? "Agents ready"
      : "Set GROQ_API_KEY in .env so agents can reply";
  } catch {
    state.agentsReady = false;
    n.className = "missing";
    n.textContent = "Set GROQ_API_KEY in .env so agents can reply";
  }
}

function roleLine(prompt) {
  const text = String(prompt || "").replace(/\s+/g, " ").trim();
  if (!text) return "custom agent";
  const cut = text.search(/[.!?]/);
  const first = cut === -1 ? text : text.slice(0, cut + 1);
  return first.length > 72 ? first.slice(0, 69) + "…" : first;
}

async function loadAgents(channelId) {
  try {
    const res = await fetch(`${API}/api/agents?channel_id=${encodeURIComponent(channelId)}`);
    state.agents = res.ok ? await res.json() : [];
  } catch {
    state.agents = [];
  }
  const box = el("agents-list");
  if (!state.agents.length) {
    box.innerHTML = `<div class="empty">No agents in this channel</div>`;
    return;
  }
  box.innerHTML = "";
  for (const a of state.agents) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "name";
    btn.title = `Open @${a.name}`;
    const meta = document.createElement("span");
    meta.className = "name-meta";
    const label = document.createElement("span");
    label.className = "name-label";
    label.textContent = a.name;
    const role = document.createElement("span");
    role.className = "name-role";
    role.textContent = roleLine(a.system_prompt);
    meta.append(label, role);
    btn.innerHTML = `<span class="dot"></span>`;
    btn.appendChild(meta);
    btn.addEventListener("click", () => openAgentPanel(a.name));
    box.appendChild(btn);
  }
}

function insertMention(name, textarea = el("msg-input")) {
  const start = textarea.selectionStart;
  const value = textarea.value;
  const before = value.slice(0, start);
  const after = value.slice(textarea.selectionEnd);
  const replaced = before.replace(/(^|\s)@[a-zA-Z0-9_\-]*$/, `$1@${name} `);
  const usedReplace = replaced !== before;
  textarea.value = usedReplace ? replaced + after : `${before}@${name} ${after}`;
  const pos = usedReplace ? replaced.length : before.length + name.length + 2;
  textarea.focus();
  textarea.setSelectionRange(pos, pos);
  hideMention();
  autosize(textarea);
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
      showEmpty(log, "No messages yet. Agents live in this room — try @swarm hi");
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
    toast("Agents can't reply until GROQ_API_KEY is set in .env", true);
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
  const m = before.match(/(^|\s)@([a-zA-Z0-9_\-]*)$/);
  return m ? m[2].toLowerCase() : null;
}

function hideMention() {
  el("mention-menu").hidden = true;
}

function renderMentionMenu(query) {
  const menu = el("mention-menu");
  const matches = state.agents.filter((a) => a.name.toLowerCase().startsWith(query));
  if (!matches.length) {
    hideMention();
    return;
  }
  if (state.mentionIndex >= matches.length) state.mentionIndex = 0;
  menu.innerHTML = "";
  matches.forEach((a, i) => {
    const li = document.createElement("li");
    const btn = document.createElement("button");
    btn.type = "button";
    btn.setAttribute("role", "option");
    btn.textContent = `@${a.name}`;
    if (i === state.mentionIndex) btn.classList.add("active");
    btn.addEventListener("mousedown", (ev) => {
      ev.preventDefault();
      insertMention(a.name);
    });
    li.appendChild(btn);
    menu.appendChild(li);
  });
  menu.hidden = false;
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
      renderMentionMenu(mentionQuery(el("msg-input")) || "");
      return;
    }
    if (ev.key === "ArrowUp") {
      ev.preventDefault();
      state.mentionIndex = (state.mentionIndex - 1 + buttons.length) % buttons.length;
      renderMentionMenu(mentionQuery(el("msg-input")) || "");
      return;
    }
    if (ev.key === "Enter" || ev.key === "Tab") {
      ev.preventDefault();
      const name = buttons[state.mentionIndex]?.textContent?.replace(/^@/, "");
      if (name) insertMention(name);
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
  sel.innerHTML = `<option value="">Every channel</option>`;
  for (const c of state.channels) {
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

function openCreateAgent() {
  state.editingAgent = null;
  el("agent-err").textContent = "";
  el("agent-form").reset();
  el("agent-name").disabled = false;
  el("agent-model").value = "llama-3.3-70b-versatile";
  el("agent-window").value = "12";
  setToolChecks(["read_only_shell", "search_channel_history", "remember", "recall"]);
  fillScopeOptions("");
  renderMemories([]);
  el("agent-modal-title").textContent = "New agent";
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
    el("agent-model").value = a.model || "";
    el("agent-window").value = String(a.history_window || 12);
    fillScopeOptions(a.channel_scope || "");
    setToolChecks(a.tools);
    renderMemories(a.memories);
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
  const tools = selectedTools();
  if (!name || !system_prompt) {
    el("agent-err").textContent = "name and prompt are required";
    return;
  }
  const payload = { system_prompt, model, channel_scope, history_window, max_tool_calls: 3, tools };
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
      closeAgentModal();
      await loadAgents(state.channel);
      toast(wasEdit ? `Updated @${name}` : `Created @${name}`);
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
  if (btn) btn.textContent = show ? "Hide agent tools" : "Show agent tools";
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
  await loadChannels();
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
  closeAgentModal();
  insertMention(name);
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

(async function init() {
  applyToolsVisibility();
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
