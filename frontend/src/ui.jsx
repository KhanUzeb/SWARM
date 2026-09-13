import { useCallback, useEffect, useMemo, useRef, useState } from "react";

// ── API & Utilities (ported from lib.js) ──

const CACHE_TTL = { list: 60_000, status: 10_000, catalog: 300_000 };
const HISTORY_LIMIT = 50;
const DEFAULT_MODEL = "openai/gpt-oss-120b";
const ALL_TOOLS = [
  "read_only_shell", "search_channel_history", "remember", "recall",
  "forget", "knowledge_search", "knowledge_save",
  "list_workspace", "read_workspace", "write_workspace", "fetch_url",
  "channel_digest", "save_skill", "request_approval",
  "computer_run", "computer_open", "computer_screenshot",
  "browser_navigate", "browser_snapshot", "browser_click",
  "browser_type", "browser_press", "browser_wait", "browser_screenshot",
  "exa_search", "tavily_search", "firecrawl_scrape", "browser_use",
  "cua_desktop", "system_run", "system_ls", "system_read", "system_write",
];

function api(path, options = {}) {
  const { token, cacheTtl, method = "GET", body, headers = {} } = options;
  const h = { "Content-Type": "application/json", "X-Swarm-Client": "web", ...headers };
  if (token) h["Authorization"] = `Bearer ${token}`;
  if (cacheTtl) h["Cache-Control"] = `max-age=${Math.floor(cacheTtl / 1000)}`;
  return fetch(path, { method, headers: h, body: body ? JSON.stringify(body) : undefined });
}

async function apiJson(path, options = {}) {
  const res = await api(path, options);
  const data = await res.json().catch(() => ({}));
  return { ok: res.ok, status: res.status, data };
}

function authHeaders(token) {
  return { Authorization: `Bearer ${token}` };
}

function escapeHtml(s) {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function fmtTime(ts) {
  if (!ts) return "";
  const d = new Date(ts * 1000);
  const now = new Date();
  const diff = now - d;
  if (diff < 60_000) return "just now";
  if (diff < 3_600_000) return `${Math.floor(diff / 60_000)}m ago`;
  if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)}h ago`;
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

function fmtBytes(bytes) {
  if (!bytes && bytes !== 0) return "";
  const units = ["B", "KB", "MB", "GB"];
  let i = 0;
  while (bytes >= 1024 && i < units.length - 1) { bytes /= 1024; i++; }
  return `${bytes.toFixed(i ? 1 : 0)} ${units[i]}`;
}

function initials(name) {
  return name?.split(/[\s-]+/).map(w => w[0]).join("").toUpperCase().slice(0, 2) || "?";
}

function botLabel(agent) {
  return agent.display_name ? `${agent.display_name} (@${agent.name})` : `@${agent.name}`;
}

function slugFromName(name) {
  return name.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/(^-|-$)/g, "");
}

function statusLabel(s) {
  const map = { idle: "Idle", working: "Working", needs_approval: "Needs approval" };
  return map[s] || s;
}

// ── Math Rendering ──
// KaTeX was dropped from the bundle (258KB, never reached: the tokenizer
// below emits text only). If math parts return, load KaTeX lazily via
// `await import("katex")` inside the math branch instead of a top import.
function renderMath(tex, display) {
  void display;
  return `<code class="math-fallback">${escapeHtml(tex)}</code>`;
}

// ── Rich Text Tokenizer (simplified) ──

function tokenizeBody(body) {
  if (!body) return [{ type: "text", text: "" }];
  const parts = [];
  let last = 0;
  const codeRegex = /```(\w*)\n([\s\S]*?)```/g;
  const mathDisplayRegex = /\$\$([\s\S]*?)\$\$/g;
  const mathInlineRegex = /\$([^\$\n]+?)\$/g;
  
  // Simple approach: split by code blocks first
  const codeMatches = [...body.matchAll(codeRegex)];
  if (codeMatches.length === 0) {
    // Check for math
    const displayMatches = [...body.matchAll(mathDisplayRegex)];
    const inlineMatches = [...body.matchAll(mathInlineRegex)];
    if (displayMatches.length === 0 && inlineMatches.length === 0) {
      return [{ type: "text", text: body }];
    }
  }
  
  // For simplicity, return as text with basic formatting
  return [{ type: "text", text: body }];
}

function formatInline(text) {
  return escapeHtml(text)
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/\*(.+?)\*/g, "<em>$1</em>")
    .replace(/`(.+?)`/g, "<code>$1</code>")
    .replace(/@(\w+)/g, '<span class="mention">@$1</span>')
    .replace(/(https?:\/\/[^\s]+)/g, '<a href="$1" target="_blank" rel="noopener">$1</a>');
}

function RichBody({ body }) {
  const parts = useMemo(() => tokenizeBody(body), [body]);
  return (
    <div className="body rich">
      {parts.map((part, i) => {
        if (part.type === "code") return <CodeBlock key={i} lang={part.lang} text={part.text} />;
        if (part.type === "math") return (
          <span key={i} className={part.display ? "math-display" : "math-inline"} dangerouslySetInnerHTML={{ __html: renderMath(part.tex, part.display) }} />
        );
        return <span key={i} dangerouslySetInnerHTML={{ __html: formatInline(part.text) }} />;
      })}
    </div>
  );
}

function CodeBlock({ lang, text }) {
  const [copied, setCopied] = useState(false);
  return (
    <div className="code-block">
      <div className="code-head">
        <span className="text-mono-xs text-subtle">{lang || "text"}</span>
        <button type="button" className="btn btn-ghost btn-sm" onClick={async () => {
          try {
            await navigator.clipboard.writeText(text);
            setCopied(true);
            setTimeout(() => setCopied(false), 1400);
          } catch { /* ignore */ }
        }}>
          {copied ? "Copied" : "Copy"}
        </button>
      </div>
      <pre><code>{escapeHtml(text)}</code></pre>
    </div>
  );
}

// ── UI Primitives ──

function Avatar({ name, kind = "human", size = "md", src, avatar }) {
  const sizes = { sm: "avatar-sm", md: "", lg: "avatar-lg", xl: "avatar-xl" };
  const kindClass = { human: "avatar-human", agent: "avatar-agent", system: "avatar-system" }[kind] || "avatar-human";
  const pfp = (avatar || src || "").trim();
  const isImage = /^https?:\/\//i.test(pfp) || pfp.startsWith("data:image");

  if (isImage) return <img className={`avatar ${sizes[size]} avatar-image`} src={pfp} alt={name} loading="lazy" />;

  if (pfp) {
    return (
      <div className={`avatar ${sizes[size]} avatar-emoji`} aria-label={name} role="img">
        {pfp}
      </div>
    );
  }

  return (
    <div className={`avatar ${sizes[size]} ${kindClass}`} aria-label={name}>
      {initials(name)}
    </div>
  );
}

function Badge({ children, variant = "neutral", className = "" }) {
  const variants = {
    neutral: "badge-neutral",
    brand: "badge-brand",
    success: "badge-success",
    warning: "badge-warning",
    error: "badge-error",
    subtle: "badge-subtle",
  };
  return <span className={`badge ${variants[variant]} ${className}`}>{children}</span>;
}

function Button({ children, variant = "secondary", size = "md", className = "", icon, loading, ...props }) {
  const variants = {
    primary: "btn-primary",
    secondary: "btn-secondary",
    ghost: "btn-ghost",
    subtle: "btn-subtle",
    danger: "btn-danger",
  };
  const sizes = { sm: "btn-sm", md: "", lg: "btn-lg" };
  
  return (
    <button
      className={`btn ${variants[variant]} ${sizes[size]} ${className}`}
      disabled={loading || props.disabled}
      {...props}
    >
      {loading && <span className="spinner spinner-sm" />}
      {!loading && icon && <span className="btn-icon-start">{icon}</span>}
      {children}
    </button>
  );
}

function Input({ className = "", error, ...props }) {
  return (
    <input
      className={`input ${error ? "input-error" : ""} ${className}`}
      {...props}
    />
  );
}

function Textarea({ className = "", ...props }) {
  return <textarea className={`textarea ${className}`} {...props} />;
}

function Card({ children, className = "", padded = true, interactive, ...props }) {
  return (
    <div className={`card ${padded ? "card-padded" : ""} ${interactive ? "card-interactive" : ""} ${className}`} {...props}>
      {children}
    </div>
  );
}

function Dropdown({ trigger, items, align = "right", label = "Menu" }) {
  const [open, setOpen] = useState(false);
  const ref = useRef(null);

  useEffect(() => {
    function handleClick(e) { if (ref.current && !ref.current.contains(e.target)) setOpen(false); }
    function handleKey(e) { if (e.key === "Escape") setOpen(false); }
    document.addEventListener("mousedown", handleClick);
    document.addEventListener("keydown", handleKey);
    return () => {
      document.removeEventListener("mousedown", handleClick);
      document.removeEventListener("keydown", handleKey);
    };
  }, []);

  return (
    <div className="dropdown" ref={ref}>
      <span
        role="button"
        tabIndex={0}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={label}
        onClick={() => setOpen(o => !o)}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") { e.preventDefault(); setOpen(o => !o); }
        }}
      >{trigger}</span>
      {open && (
        <div className="dropdown-menu" role="menu" style={{ [align]: 0 }}>
          {items.filter(item => item !== "divider").map((item, i) => {
            if (item === "divider") return <div key={`div-${i}`} className="dropdown-divider" />;
            if (item.section) return <div key={`sec-${i}`} className="dropdown-section">{item.section}</div>;
            return (
              <button
                key={item.id || i}
                role="menuitem"
                className={`dropdown-item ${item.danger ? "danger" : ""}`}
                onClick={() => { item.onClick?.(); setOpen(false); }}
              >
                {item.icon && <span>{item.icon}</span>}
                {item.label}
                {item.shortcut && <span className="text-mono-xs text-subtle ml-auto">{item.shortcut}</span>}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}

function Tooltip({ children, content }) {
  return <span className="tooltip-trigger" data-tooltip={content}>{children}</span>;
}

function ToastContainer() {
  const [toasts, setToasts] = useState([]);
  
  const addToast = useCallback((message, type = "info", title) => {
    const id = Date.now();
    setToasts(prev => [...prev, { id, message, type, title }]);
    setTimeout(() => setToasts(prev => prev.filter(t => t.id !== id)), 4000);
  }, []);
  
  if (toasts.length === 0) return null;
  
  return (
    <div className="toast-container">
      {toasts.map(t => (
        <div key={t.id} className={`toast ${t.type}`}>
          <div className="toast-content">
            {t.title && <div className="toast-title">{t.title}</div>}
            <div className="toast-message">{t.message}</div>
          </div>
          <button className="toast-close" onClick={() => setToasts(prev => prev.filter(x => x.id !== t.id))}>×</button>
        </div>
      ))}
    </div>
  );
}

function Modal({ open, onClose, title, children, footer, size = "md" }) {
  if (!open) return null;
  const sizes = { sm: "max-w-[360px]", md: "max-w-[480px]", lg: "max-w-[640px]" };
  
  return (
    <div className="modal-overlay" onClick={onClose} role="dialog" aria-modal="true" aria-labelledby="modal-title">
      <div className={`modal ${sizes[size]}`} onClick={e => e.stopPropagation()}>
        <div className="modal-header">
          <h2 id="modal-title" className="modal-title">{title}</h2>
          <button className="modal-close" onClick={onClose} aria-label="Close">×</button>
        </div>
        <div className="modal-body">{children}</div>
        {footer && <div className="modal-footer">{footer}</div>}
      </div>
    </div>
  );
}

function Skeleton({ className = "", ...props }) {
  return <div className={`skeleton ${className}`} {...props} />;
}

function Spinner({ size = "md", className = "" }) {
  return <span className={`spinner spinner-${size} ${className}`} />;
}

const EMPTY_GRAPHICS = {
  default: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="12" cy="12" r="9" />
    </svg>
  ),
  chat: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
    </svg>
  ),
  folder: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z" />
    </svg>
  ),
  run: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <polygon points="5 3 19 12 5 21 5 3" />
    </svg>
  ),
  workflow: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <rect x="3" y="3" width="7" height="7" rx="1.5" /><rect x="14" y="3" width="7" height="7" rx="1.5" /><rect x="8.5" y="14" width="7" height="7" rx="1.5" />
    </svg>
  ),
  sandbox: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <rect x="2" y="3" width="20" height="14" rx="2" /><line x1="8" y1="21" x2="16" y2="21" /><line x1="12" y1="17" x2="12" y2="21" />
    </svg>
  ),
  search: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="11" cy="11" r="8" /><path d="M21 21l-4.3-4.3" />
    </svg>
  ),
};

function EmptyState({ kind = "default", icon, title, message, action }) {
  const graphic = EMPTY_GRAPHICS[kind] || EMPTY_GRAPHICS.default;
  return (
    <div className="empty-state">
      <div className="empty-state-icon" aria-hidden>
        {typeof icon === "object" ? icon : graphic}
      </div>
      <div className="empty-state-title">{title}</div>
      <div className="empty-state-message">{message}</div>
      {action}
    </div>
  );
}

function ScrollArea({ children, className = "", ...props }) {
  return <div className={`scrollable ${className}`} {...props}>{children}</div>;
}

function Divider({ className = "", vertical }) {
  return vertical ? <div className={`divider-vertical ${className}`} /> : <hr className={`divider ${className}`} />;
}

// ── Export everything ──

export {
  // API
  api, apiJson, authHeaders, CACHE_TTL, HISTORY_LIMIT, DEFAULT_MODEL, ALL_TOOLS,
  // Utils
  escapeHtml, fmtTime, fmtBytes, initials, botLabel, slugFromName, statusLabel,
  // Math
  renderMath,
  // Rich text
  RichBody, CodeBlock,
  // Primitives
  Avatar, Badge, Button, Input, Textarea, Card, Dropdown, Tooltip, ToastContainer, Modal,
  Skeleton, Spinner, EmptyState, ScrollArea, Divider,
  // State
  useState, useEffect, useCallback, useMemo, useRef,
};
