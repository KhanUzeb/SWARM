export const HISTORY_LIMIT = 50;
export const DEFAULT_MODEL = "openai/gpt-oss-120b";
export const SWARM_CLIENT = "web";
export const CACHE_TTL = {
  status: 15_000,
  catalog: 60_000,
  list: 30_000,
};
export const ALL_TOOLS = [
  "read_only_shell",
  "search_channel_history",
  "remember",
  "recall",
  "list_workspace",
  "read_workspace",
  "write_workspace",
  "fetch_url",
  "channel_digest",
  "save_skill",
  "request_approval",
  "computer_run",
  "computer_open",
  "computer_screenshot",
  "browser_navigate",
  "browser_snapshot",
  "browser_click",
  "browser_type",
  "browser_press",
  "browser_wait",
  "browser_screenshot",
  "exa_search",
  "tavily_search",
  "firecrawl_scrape",
  "browser_use",
  "cua_desktop",
];
export const EMOJI = ["🔥", "✅", "👀", "❤️", "🎉", "👍"];

export function initials(name) {
  const parts = String(name || "?").trim().split(/\s+/);
  if (parts.length >= 2) return (parts[0][0] + parts[1][0]).toUpperCase();
  return String(name || "?").slice(0, 2).toUpperCase();
}

export function slugFromName(text) {
  const slug = String(text || "").trim().toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 32);
  return slug || "bot";
}

export function botLabel(agent) {
  if (!agent) return "";
  return String(agent.display_name || agent.name || "").trim() || agent.name;
}

export function fmtTime(ts) {
  if (!ts) return "";
  return new Date(ts * 1000).toTimeString().slice(0, 5);
}

import { cacheGet, cacheInvalidate, cacheKey, cacheSet } from "./lib/cache.js";

export function authHeaders(token, json = true) {
  const h = { "X-Swarm-Client": SWARM_CLIENT };
  if (json) h["Content-Type"] = "application/json";
  if (token) h.Authorization = `Bearer ${token}`;
  return h;
}

export function statusLabel(status) {
  return ({ working: "Working", needs_approval: "Needs approval", idle: "Idle" })[status] || status || "Idle";
}

export function roleLine(prompt) {
  const text = String(prompt || "").replace(/\s+/g, " ").trim();
  if (!text) return "custom bot";
  const cut = text.search(/[.!?]/);
  const first = cut === -1 ? text : text.slice(0, cut + 1);
  return first.length > 72 ? first.slice(0, 69) + "…" : first;
}

export function groupedWith(prev, m) {
  return !!(
    prev
    && !m.parent_id
    && prev.author_kind === m.author_kind
    && prev.author === m.author
    && !prev.streaming
  );
}

export function mentionQuery(value, pos) {
  const before = value.slice(0, pos);
  const at = before.match(/(^|\s)@([a-zA-Z0-9_\-]*)$/);
  if (at) return { mode: "at", query: at[2].toLowerCase() };
  const slash = before.match(/(^|\s)\/([a-zA-Z0-9_\-]*)$/);
  if (slash) return { mode: "slash", query: slash[2].toLowerCase() };
  return null;
}

export function insertMention(value, start, end, name, kind) {
  const before = value.slice(0, start);
  const after = value.slice(end);
  const needle = kind === "slash" ? /(^|\s)\/[a-zA-Z0-9_\-]*$/ : /(^|\s)@[a-zA-Z0-9_\-]*$/;
  const mark = kind === "slash" ? "/" : "@";
  const replaced = before.replace(needle, `$1${mark}${name} `);
  const usedReplace = replaced !== before;
  const next = usedReplace ? replaced + after : `${before}${mark}${name} ${after}`;
  const pos = usedReplace ? replaced.length : before.length + name.length + 2;
  return { value: next, pos };
}

export async function api(path, { token, method = "GET", body, json = true } = {}) {
  const res = await fetch(path, {
    method,
    headers: authHeaders(token, json && body != null),
    body: body == null ? undefined : (json ? JSON.stringify(body) : body),
    credentials: "same-origin",
  });
  return res;
}

/** GET with TTL cache; mutations can pass invalidate prefix to bust related keys. */
export async function apiJson(path, {
  token,
  method = "GET",
  body,
  cacheTtl = 0,
  invalidate = null,
} = {}) {
  if (invalidate) cacheInvalidate(invalidate);

  const isGet = method === "GET";
  const key = isGet && cacheTtl > 0 ? cacheKey(path, token) : null;
  if (key) {
    const hit = cacheGet(key);
    if (hit !== undefined) return hit;
  }

  const res = await api(path, { token, method, body });
  let data = null;
  if (res.status !== 204) {
    try {
      data = await res.json();
    } catch {
      data = null;
    }
  }
  const out = { ok: res.ok, status: res.status, data };
  if (key && res.ok) cacheSet(key, out, cacheTtl);
  return out;
}

export function bustCache(...prefixes) {
  for (const p of prefixes) cacheInvalidate(p);
}

export function escapeHtml(value) {
  return String(value || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

const TOKEN_RE = /```([\w+-]*)[ \t]*\n?([\s\S]*?)```|\$\$([\s\S]+?)\$\$|\$(?!\$)([^$\n]+?)\$/g;

export function tokenizeBody(body) {
  const text = String(body || "");
  const parts = [];
  let last = 0;
  TOKEN_RE.lastIndex = 0;
  let match;
  while ((match = TOKEN_RE.exec(text))) {
    if (match.index > last) parts.push({ type: "text", text: text.slice(last, match.index) });
    if (match[2] != null) {
      parts.push({ type: "code", lang: (match[1] || "").trim() || "text", text: match[2].replace(/\n$/, "") });
    } else if (match[3] != null) {
      parts.push({ type: "math", display: true, tex: match[3].trim() });
    } else {
      parts.push({ type: "math", display: false, tex: match[4].trim() });
    }
    last = match.index + match[0].length;
  }
  if (last < text.length) parts.push({ type: "text", text: text.slice(last) });
  return parts.length ? parts : [{ type: "text", text }];
}

export function formatInline(text) {
  return escapeHtml(text)
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\\subsection\*\{([^}]+)\}/g, '<span class="tex-sub">$1</span>')
    .replace(/\n/g, "<br />");
}

export function extractPaper(messages) {
  const code = [];
  const math = [];
  for (const m of messages || []) {
    if (!m?.body || m.author_kind === "system") continue;
    for (const part of tokenizeBody(m.body)) {
      if (part.type === "code") code.push({ ...part, author: m.author, id: m.id });
      if (part.type === "math") math.push({ ...part, author: m.author, id: m.id });
    }
  }
  return { code, math };
}
