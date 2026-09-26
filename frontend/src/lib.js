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
  "forget",
  "knowledge_search",
  "knowledge_save",
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
  "system_run",
  "system_ls",
  "system_read",
  "system_write",
];
export const EMOJI = ["👍", "❤️", "😂", "🎉", "😮", "😢", "🙏", "👀", "🔥", "✅", "🚀", "💡"];

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

/** Single timestamp formatter for the whole frontend.
 * Accepts seconds, milliseconds, numeric strings, ISO strings, or Dates.
 * Recent → relative ("just now", "5m ago", "3h ago"), then "Yesterday",
 * then "Mon D" (with year when not this year). */
export function fmtTime(ts) {
  const ms = toMs(ts);
  if (!ms) return "";
  const d = new Date(ms);
  const now = new Date();
  const diff = now.getTime() - d.getTime();
  if (diff >= 0 && diff < 60_000) return "just now";
  if (diff >= 0 && diff < 3_600_000) return `${Math.floor(diff / 60_000)}m ago`;
  if (diff >= 0 && diff < 86_400_000 && d.getDate() === now.getDate()) {
    return `${Math.floor(diff / 3_600_000)}h ago`;
  }
  const yesterday = new Date(now);
  yesterday.setDate(now.getDate() - 1);
  if (d.toDateString() === yesterday.toDateString()) return "Yesterday";
  const opts = { month: "short", day: "numeric" };
  if (d.getFullYear() !== now.getFullYear()) opts.year = "numeric";
  return d.toLocaleDateString(undefined, opts);
}

function toMs(ts) {
  if (ts == null || ts === "") return 0;
  if (ts instanceof Date) return ts.getTime() || 0;
  if (typeof ts === "number") return ts < 1e12 ? ts * 1000 : ts;
  const text = String(ts).trim();
  if (!text) return 0;
  const n = Number(text);
  if (Number.isFinite(n)) return n < 1e12 ? n * 1000 : n;
  const parsed = Date.parse(text);
  return Number.isFinite(parsed) ? parsed : 0;
}

export function fmtBytes(n) {
  if (n == null || n === "") return "";
  const bytes = Number(n);
  if (!Number.isFinite(bytes)) return "";
  const units = ["B", "KB", "MB", "GB"];
  let i = 0;
  let v = bytes;
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
  return `${v.toFixed(i ? 1 : 0)} ${units[i]}`;
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

export function isAgentError(body) {
  return String(body || "").trim().startsWith("[agent error:");
}

/** An agent reply the user can resume: hard errors plus stopped/cut-off streams. */
export function isResumable(body) {
  const text = String(body || "");
  return isAgentError(text) || text.includes("[reply cut off");
}

/** Compact display id for a model: drop provider prefix, cap length. */
export function shortModel(modelId, maxLen = 28) {
  const raw = String(modelId || "").trim();
  if (!raw) return "";
  const short = raw.includes("/") ? raw.slice(raw.lastIndexOf("/") + 1) : raw;
  return short.length > maxLen ? `${short.slice(0, maxLen - 1)}…` : short;
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

export async function api(path, { token, method = "GET", body, json = true } = {}) {
  const res = await fetch(path, {
    method,
    headers: authHeaders(token, json && body != null),
    body: body == null ? undefined : (json ? JSON.stringify(body) : body),
    credentials: "same-origin",
  });
  return res;
}

/** GET with TTL cache; mutations can pass invalidate prefix to bust related keys.
 * @param {string} path
 * @param {{token?: string, method?: string, body?: unknown, cacheTtl?: number, invalidate?: string | string[] | null}} [options]
 */
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

/** HTML-escape for interpolated text. Quotes and apostrophes are escaped too,
 *  so the output is safe inside an attribute as well as in text content. */
export function escapeHtml(value) {
  return String(value || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

