import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  CircleDashed,
  Folder,
  MessageSquare,
  Monitor,
  Play,
  Search,
  Workflow,
  X,
} from "lucide-react";
import { escapeHtml, fmtTime, initials } from "./lib.js";

// ── API & utilities ──
// lib.js is the single owner of the API client and the shared formatters.
// This module re-exports them so `import { apiJson } from "../ui.jsx"` keeps
// working while there is only one implementation of each. Two clients used to
// live here, with different cache lifetimes for the same data; the one here
// also ignored the `cacheTtl` its callers passed. See AGENTS.md.

export {
  api,
  apiJson,
  authHeaders,
  bustCache,
  CACHE_TTL,
  HISTORY_LIMIT,
  DEFAULT_MODEL,
  ALL_TOOLS,
  escapeHtml,
  fmtTime,
  fmtBytes,
  initials,
  botLabel,
  slugFromName,
  statusLabel,
} from "./lib.js";

// ── Math Rendering ──
// KaTeX was dropped from the bundle (258KB, never reached: the tokenizer
// below emits text only). If math parts return, load KaTeX lazily via
// `await import("katex")` inside the math branch instead of a top import.
function renderMath(tex, display) {
  void display;
  return `<code class="math-fallback">${escapeHtml(tex)}</code>`;
}

// ── Rich Text Tokenizer (simplified) ──

function tokenizeRichBody(body) {
  if (!body) return [{ type: "text", text: "" }];
  const parts = [];
  const codeRegex = /```(\w*)\r?\n([\s\S]*?)```/g;
  let lastIndex = 0;
  let match;
  while ((match = codeRegex.exec(body)) !== null) {
    if (match.index > lastIndex) {
      parts.push({ type: "text", text: body.slice(lastIndex, match.index) });
    }
    parts.push({
      type: "code",
      lang: match[1] || "",
      text: match[2].trimEnd(),
    });
    lastIndex = match.index + match[0].length;
  }
  if (lastIndex < body.length) {
    parts.push({ type: "text", text: body.slice(lastIndex) });
  }
  return parts.length > 0 ? parts : [{ type: "text", text: body }];
}

function formatRichInline(text) {
  return escapeHtml(text)
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/\*(.+?)\*/g, "<em>$1</em>")
    .replace(/`(.+?)`/g, "<code>$1</code>")
    .replace(/@(\w+)/g, '<span class="mention">@$1</span>')
    .replace(/(https?:\/\/[^\s]+)/g, '<a href="$1" target="_blank" rel="noopener">$1</a>');
}

function RichBody({ body }) {
  const parts = useMemo(() => tokenizeRichBody(body), [body]);
  return (
    <div className="body rich">
      {parts.map((part, i) => {
        if (part.type === "code") return <CodeBlock key={i} lang={part.lang} text={part.text} />;
        if (part.type === "math") return (
          <span key={i} className={part.display ? "math-display" : "math-inline"} dangerouslySetInnerHTML={{ __html: renderMath(part.tex, part.display) }} />
        );
        return <span key={i} dangerouslySetInnerHTML={{ __html: formatRichInline(part.text) }} />;
      })}
    </div>
  );
}

function CodeBlock({ lang, text }) {
  const [copied, setCopied] = useState(false);
  return (
    <div className="my-2.5 overflow-hidden rounded-xl border border-zinc-800 bg-zinc-950/90 shadow-sm text-xs font-mono">
      <div className="flex items-center justify-between border-b border-zinc-850 px-3.5 py-1.5 bg-zinc-900/60 select-none">
        <span className="text-[11px] font-medium text-zinc-400 font-mono lowercase">
          {lang || "code"}
        </span>
        <button
          type="button"
          className="inline-flex items-center gap-1 text-[11px] text-zinc-400 hover:text-zinc-100 transition-colors py-0.5 px-1.5 rounded hover:bg-zinc-800"
          onClick={async () => {
            try {
              await navigator.clipboard.writeText(text);
              setCopied(true);
              setTimeout(() => setCopied(false), 1400);
            } catch { /* ignore */ }
          }}
          aria-live="polite"
        >
          {copied ? "Copied" : "Copy"}
        </button>
      </div>
      <pre className="p-3.5 overflow-x-auto text-[12px] leading-relaxed text-zinc-200">
        {/* React escapes text nodes itself — never pre-escape here. */}
        <code>{text}</code>
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
          {items.map((item, i) => {
            if (item === "divider") return <div key={`div-${i}`} className="dropdown-divider" role="separator" />;
            if (item?.section) return <div key={`sec-${i}`} className="dropdown-section">{item.section}</div>;
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
          <button className="toast-close" onClick={() => setToasts(prev => prev.filter(x => x.id !== t.id))}><X size={14} /></button>
        </div>
      ))}
    </div>
  );
}

function Modal({ open, onClose, title, children, footer, size = "md" }) {
  const closeRef = useRef(null);
  useEffect(() => {
    if (!open) return undefined;
    closeRef.current?.focus();
    function onKey(e) {
      if (e.key === "Escape") onClose?.();
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, onClose]);
  if (!open) return null;
  const sizes = { sm: "max-w-[360px]", md: "max-w-[480px]", lg: "max-w-[640px]" };
  
  return (
    <div className="modal-overlay" onClick={onClose} role="dialog" aria-modal="true" aria-labelledby="modal-title">
      <div className={`modal ${sizes[size]}`} onClick={e => e.stopPropagation()}>
        <div className="modal-header">
          <h2 id="modal-title" className="modal-title">{title}</h2>
          <button ref={closeRef} className="modal-close" onClick={onClose} aria-label="Close dialog"><X size={14} /></button>
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

// One icon library, one stroke. The empty state picks its mark from the
// same set as the rest of the interface rather than carrying its own SVGs.
const EMPTY_ICON = {
  default: CircleDashed,
  chat: MessageSquare,
  folder: Folder,
  run: Play,
  workflow: Workflow,
  sandbox: Monitor,
  search: Search,
};

function EmptyState({ kind = "default", icon, title, message, action }) {
  const Mark = EMPTY_ICON[kind] || EMPTY_ICON.default;
  return (
    <div className="empty-state" role="status">
      <div className="empty-state-icon" aria-hidden>
        {icon ?? <Mark size={18} strokeWidth={1.5} />}
      </div>
      <div className="empty-state-title">{title}</div>
      {message && <div className="empty-state-message">{message}</div>}
      {action && <div className="empty-state-action">{action}</div>}
    </div>
  );
}

function ScrollArea({ children, className = "", ...props }) {
  return <div className={`scrollable ${className}`} {...props}>{children}</div>;
}

function Divider({ className = "", vertical }) {
  return vertical ? <div className={`divider-vertical ${className}`} /> : <hr className={`divider ${className}`} />;
}

// ── Export the local surface ──
// The API client and the shared formatters are re-exported from lib.js at the
// top of this file; only what this module actually owns is listed here.

export {
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
