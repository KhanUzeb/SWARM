import { useCallback, useEffect, useMemo, useRef, useState } from "react";
// Single source of truth for rich-text parsing lives in lib.js —
// this module only owns rendering (AGENTS.md: one source of truth).
import { tokenizeBody, formatInline } from "./lib.js";

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

// ── Rich Text (parsing: lib.js; rendering: here) ──

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
  const lines = String(text ?? "").split("\n");
  return (
    <div className="code-block">
      <div className="code-head">
        <span className="text-mono-xs text-subtle">{lang || "text"}</span>
        <button
          type="button"
          className="btn btn-ghost btn-sm"
          aria-label={copied ? "Copied to clipboard" : `Copy ${lang || "code"} to clipboard`}
          onClick={async () => {
            try {
              await navigator.clipboard.writeText(text);
              setCopied(true);
              setTimeout(() => setCopied(false), 1400);
            } catch { /* clipboard unavailable — selection still works */ }
          }}
        >
          <span aria-live="polite">{copied ? "Copied" : "Copy"}</span>
        </button>
      </div>
      <pre className="code-lines" tabIndex={0} aria-label={`${lang || "Code"} block, ${lines.length} line${lines.length === 1 ? "" : "s"}`}>
        <code>
          {lines.map((line, i) => {
            const trimmed = line.trimStart();
            const diffClass = trimmed.startsWith("+") && !trimmed.startsWith("+++")
              ? " diff-add"
              : trimmed.startsWith("-") && !trimmed.startsWith("---")
                ? " diff-del"
                : "";
            return (
              <span key={i} className={`code-line${diffClass}`}>
                <span className="code-lineno" aria-hidden>{i + 1}</span>
                <span className="code-linetext">{line === "" ? " " : line}</span>
                {"\n"}
              </span>
            );
          })}
        </code>
      </pre>
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

function Button({ children, variant = "secondary", size = "md", className = "", icon, loading, type = "button", ...props }) {
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
      type={type}
      className={`btn ${variants[variant]} ${sizes[size]} ${className}`}
      disabled={loading || props.disabled}
      aria-busy={loading || undefined}
      {...props}
    >
      {loading && <span className="spinner spinner-sm" aria-hidden />}
      {!loading && icon && <span className="btn-icon-start" aria-hidden>{icon}</span>}
      {children}
    </button>
  );
}

function Input({ className = "", error, ...props }) {
  return (
    <input
      className={`input ${error ? "input-error" : ""} ${className}`}
      aria-invalid={error ? true : undefined}
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
  const triggerRef = useRef(null);
  const itemRefs = useRef([]);

  const actionable = (items || []).filter(item => item && item !== "divider" && !item.section);

  useEffect(() => {
    function handleClick(e) { if (ref.current && !ref.current.contains(e.target)) setOpen(false); }
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, []);

  // Focus the first item on open; return focus to the trigger on close.
  useEffect(() => {
    if (open) {
      itemRefs.current = [];
      requestAnimationFrame(() => itemRefs.current[0]?.focus());
    }
  }, [open ]);

  function close(returnFocus = false) {
    setOpen(false);
    if (returnFocus) triggerRef.current?.focus();
  }

  function onTriggerKey(e) {
    if (e.key === "Enter" || e.key === " " || e.key === "ArrowDown") {
      e.preventDefault();
      setOpen(o => !o);
    }
  }

  function onMenuKey(e) {
    const idx = itemRefs.current.indexOf(document.activeElement);
    if (e.key === "Escape") { e.preventDefault(); close(true); }
    else if (e.key === "ArrowDown") { e.preventDefault(); itemRefs.current[(idx + 1) % itemRefs.current.length]?.focus(); }
    else if (e.key === "ArrowUp") { e.preventDefault(); itemRefs.current[(idx - 1 + itemRefs.current.length) % itemRefs.current.length]?.focus(); }
    else if (e.key === "Home") { e.preventDefault(); itemRefs.current[0]?.focus(); }
    else if (e.key === "End") { e.preventDefault(); itemRefs.current[itemRefs.current.length - 1]?.focus(); }
    else if (e.key === "Tab") { setOpen(false); }
  }

  let actionIdx = -1;
  return (
    <div className="dropdown" ref={ref}>
      <span
        ref={triggerRef}
        role="button"
        tabIndex={0}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={label}
        onClick={() => setOpen(o => !o)}
        onKeyDown={onTriggerKey}
      >{trigger}</span>
      {open && (
        <div className="dropdown-menu" role="menu" aria-label={label} style={{ [align]: 0 }} onKeyDown={onMenuKey}>
          {(items || []).map((item, i) => {
            if (item === "divider") return <div key={`div-${i}`} className="dropdown-divider" role="separator" />;
            if (item.section) return <div key={`sec-${i}`} className="dropdown-section">{item.section}</div>;
            actionIdx++;
            const refIdx = actionIdx;
            return (
              <button
                key={item.id || i}
                ref={el => { itemRefs.current[refIdx] = el; }}
                role="menuitem"
                className={`dropdown-item ${item.danger ? "danger" : ""}`}
                onClick={() => { item.onClick?.(); close(); }}
              >
                {item.icon && <span aria-hidden>{item.icon}</span>}
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
    <div className="toast-container" role="status" aria-live="polite">
      {toasts.map(t => (
        <div key={t.id} className={`toast ${t.type}`}>
          <div className="toast-content">
            {t.title && <div className="toast-title">{t.title}</div>}
            <div className="toast-message">{t.message}</div>
          </div>
          <button className="toast-close" aria-label="Dismiss notification" onClick={() => setToasts(prev => prev.filter(x => x.id !== t.id))}>×</button>
        </div>
      ))}
    </div>
  );
}

function Modal({ open, onClose, title, children, footer, size = "md" }) {
  const panelRef = useRef(null);
  const previouslyFocused = useRef(null);

  useEffect(() => {
    if (!open) return;
    previouslyFocused.current = document.activeElement;
    // Move focus into the dialog; restore it on unmount.
    requestAnimationFrame(() => {
      const first = panelRef.current?.querySelector(
        'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'
      );
      (first || panelRef.current)?.focus?.();
    });
    function onKey(e) {
      if (e.key === "Escape") { e.preventDefault(); onClose?.(); return; }
      if (e.key !== "Tab" || !panelRef.current) return;
      const focusables = [...panelRef.current.querySelectorAll(
        'button:not(:disabled), [href], input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex]:not([tabindex="-1"])'
      )];
      if (!focusables.length) return;
      const first = focusables[0];
      const last = focusables[focusables.length - 1];
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    }
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      previouslyFocused.current?.focus?.();
    };
  }, [open, onClose]);

  if (!open) return null;
  const sizes = { sm: "max-w-[360px]", md: "max-w-[480px]", lg: "max-w-[640px]" };
  
  return (
    <div className="modal-overlay" onClick={onClose} role="dialog" aria-modal="true" aria-labelledby="modal-title">
      <div ref={panelRef} tabIndex={-1} className={`modal ${sizes[size]}`} onClick={e => e.stopPropagation()}>
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
  return <span className={`spinner spinner-${size} ${className}`} aria-hidden />;
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
      <p className="empty-state-title">{title}</p>
      <p className="empty-state-message">{message}</p>
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
