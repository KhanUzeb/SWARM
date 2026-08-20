export const HISTORY_LIMIT = 50;
export const DEFAULT_MODEL = "openai/gpt-oss-120b";
export const ALL_TOOLS = [
  "read_only_shell",
  "search_channel_history",
  "remember",
  "recall",
  "list_workspace",
  "write_workspace",
  "save_skill",
  "request_approval",
];
export const EMOJI = ["🔥", "✅", "👀", "❤️", "🎉", "👍"];

export function initials(name) {
  const parts = String(name || "?").trim().split(/\s+/);
  if (parts.length >= 2) return (parts[0][0] + parts[1][0]).toUpperCase();
  return String(name || "?").slice(0, 2).toUpperCase();
}

export function fmtTime(ts) {
  if (!ts) return "";
  return new Date(ts * 1000).toTimeString().slice(0, 5);
}

export function authHeaders(token, json = true) {
  const h = {};
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
  });
  return res;
}
