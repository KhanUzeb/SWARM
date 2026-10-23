import { useCallback, useEffect, useState } from "react";
import { Monitor, RefreshCw, X, FileText } from "lucide-react";
import { apiJson, Button, EmptyState, ScrollArea, Tooltip } from "../ui.jsx";
import { api, fmtBytes } from "../lib.js";
import SystemPanel from "../ai-support/SystemPanel.jsx";
import BrowserPanel from "../ai-support/BrowserPanel.jsx";
import ToolsPanel from "../ai-support/ToolsPanel.jsx";
import AppsPanel from "../ai-support/AppsPanel.jsx";
import ProviderPanel from "../ai-support/ProviderPanel.jsx";
import PluginsPanel from "../ai-support/PluginsPanel.jsx";

// PANEL_GROUPS owns the Places/Connect destination list, and nothing keeps a
// second copy. A group *is* a destination: the user arrives at "Places" or
// "Connect" from the sidebar, the view strip or the palette, and the tabs of
// that group are the surfaces inside it. There is deliberately no group tab
// strip here — it would compete with the navigation that chose the group.
export const PANEL_GROUPS = [
  { id: "places", label: "Places", tabs: [
    { id: "files", label: "Sandbox" },
    { id: "system", label: "System" },
    { id: "browser", label: "Browser" },
  ]},
  { id: "connect", label: "Connect", tabs: [
    { id: "ai", label: "AI" },
    { id: "apps", label: "Apps" },
    { id: "tools", label: "Tools" },
    { id: "plugins", label: "Plugins" },
  ]},
];

export function ComputerPanel({ computer, token, meRole, onRefresh, flash, group = "places", initialTab }) {
  const destination = PANEL_GROUPS.find(g => g.id === group) || PANEL_GROUPS[0];
  const [activeTab, setActiveTab] = useState(initialTab || destination.tabs[0].id);

  // Defect: the tab was seeded from `initialTab` once and never re-derived, so
  // arriving at Connect from Places kept the "files" id that Connect does not
  // own and the panel body rendered nothing at all.
  useEffect(() => {
    setActiveTab((current) => {
      if (initialTab && destination.tabs.some(t => t.id === initialTab)) return initialTab;
      if (destination.tabs.some(t => t.id === current)) return current;
      return destination.tabs[0].id;
    });
  }, [group, initialTab]);

  const panelFlash = useCallback((message, error) => {
    flash?.(message, error ? "error" : "success");
  }, [flash]);

  // No early return here. The offline state used to short-circuit before the
  // tab strip rendered, so with the sandbox down the user was locked out of
  // System and Browser permanently — the only way out was a page reload. The
  // strip always renders; the offline message lives inside the sandbox body.
  return (
    <section className="computer-embedded" aria-label={destination.label}>
      <PanelChrome
        title={destination.label}
        // Defect: the two facts were joined with a middle-dot meta string (8a).
        subtitle={computer?.provider?.provider
          ? `Computer ${computer.provider.provider}, ${computer?.workspace || computer?.note?.slice(0, 48) || "shared workspace"}`
          : (computer?.workspace || computer?.note?.slice(0, 48) || "Shared workspace")}
        onRefresh={onRefresh}
      />

      <div className="panel-tabs" role="group" aria-label={`${destination.label} tabs`}>
        {destination.tabs.map(t => (
          <button
            key={t.id}
            type="button"
            className={`panel-tab${activeTab === t.id ? " active" : ""}`}
            onClick={() => setActiveTab(t.id)}
            aria-current={activeTab === t.id ? "true" : undefined}
          >
            {t.label}
          </button>
        ))}
      </div>

      <ScrollArea className="panel-body-wrap">
        {activeTab === "files" && <SandboxView computer={computer} token={token} flash={panelFlash} onRootChange={onRefresh} />}
        {activeTab === "system" && <SystemPanel token={token} flash={panelFlash} onRootChange={onRefresh} />}
        {activeTab === "browser" && <BrowserPanel token={token} flash={panelFlash} />}
        {activeTab === "ai" && <ProviderPanel token={token} flash={panelFlash} onStatusChange={onRefresh} />}
        {activeTab === "apps" && <AppsPanel token={token} flash={panelFlash} />}
        {activeTab === "tools" && <ToolsPanel token={token} flash={panelFlash} />}
        {activeTab === "plugins" && <PluginsPanel token={token} flash={panelFlash} meRole={meRole} />}
      </ScrollArea>
    </section>
  );
}

function PanelChrome({ title, subtitle, onRefresh }) {
  return (
    <div className="panel-header">
      <div className="panel-header-main">
        {/* Defect: these two were hand-rolled <svg> paths outside the lucide
            set, so the panel carried a second icon weight (AGENTS.md 8a). */}
        <span className="panel-icon" aria-hidden><Monitor size={18} /></span>
        <div>
          <h2 className="panel-title">{title}</h2>
          <span className="panel-subtitle">{subtitle}</span>
        </div>
      </div>
      <div className="panel-header-actions">
        <Tooltip content="Refresh">
          <button className="btn btn-ghost btn-icon btn-sm" onClick={onRefresh} aria-label="Refresh">
            <RefreshCw size={16} />
          </button>
        </Tooltip>
      </div>
    </div>
  );
}

function SandboxView({ computer, token, flash, onRootChange }) {
  const [preview, setPreview] = useState("");
  const [previewPath, setPreviewPath] = useState("");
  const [busy, setBusy] = useState(false);
  const [rootDraft, setRootDraft] = useState("");
  const [rootBusy, setRootBusy] = useState(false);
  const files = computer?.files || [];
  const root = computer?.sandbox?.root || computer?.workspace || "";

  // The offline state belongs inside the body, not above the tab strip: with
  // the sandbox down the strip still has to let you reach System and Browser.
  if (!computer) {
    return (
      <div className="panel-body sandbox-panel">
        <EmptyState kind="sandbox" title="Sandbox offline" message="Could not load workspace data. Agents can still use tools when configured." />
      </div>
    );
  }

  async function openFile(path) {
    setBusy(true);
    try {
      const res = await apiJson(`/api/computer/file?path=${encodeURIComponent(path)}`, { token });
      if (res.ok) {
        setPreview(res.data.content);
        setPreviewPath(path);
      } else flash?.("Couldn't open file", true);
    } catch {
      flash?.("Couldn't open file", true);
    }
    setBusy(false);
  }

  async function bindSandboxRoot(ev) {
    ev?.preventDefault();
    const next = (rootDraft || "").trim() || root;
    if (!next || rootBusy) return;
    setRootBusy(true);
    try {
      const res = await api("/api/computer/sandbox/root", {
        token,
        method: "POST",
        body: { path: next },
      });
      if (res.ok) {
        const data = await res.json().catch(() => null);
        flash?.(`Sandbox now in ${data?.root || next}`);
        setRootDraft("");
        setPreview("");
        setPreviewPath("");
        onRootChange?.();
      } else {
        const data = await res.json().catch(() => null);
        flash?.(data?.detail || "Couldn't open that folder (admin only)", true);
      }
    } catch {
      flash?.("Couldn't open that folder", true);
    }
    setRootBusy(false);
  }

  return (
    <div className="panel-body sandbox-panel">
      <p className="panel-note">{computer?.sandbox?.note || computer?.note || "Shared sandbox files for every bot."}</p>
      <form className="place-add" onSubmit={bindSandboxRoot}>
        <input
          className="input mono"
          value={rootDraft}
          onChange={(e) => setRootDraft(e.target.value)}
          placeholder={root || "Type a folder to work in"}
          aria-label="Sandbox folder"
          spellCheck={false}
        />
        <button type="submit" className="btn btn-secondary btn-sm" disabled={rootBusy}>
          {rootBusy ? "…" : "Open folder"}
        </button>
      </form>
      {root && <p className="hint">Sandbox: <span className="mono">{root}</span>. Point it at any folder, such as a project, drive, or worktree. Admin only.</p>}
      {files.length === 0 ? (
        <EmptyState kind="folder" title="Empty sandbox" message="Agents can write files here with write_workspace." />
      ) : (
        <ul className="file-list">
          {files.map(f => (
            <li key={f.path}>
              <button type="button" className="file-row" onClick={() => openFile(f.path)} disabled={busy}>
                <span className="file-icon"><FileText size={13} /></span>
                <div className="file-info">
                  <span className="file-name">{f.path}</span>
                  <span className="file-size mono muted">{fmtBytes(f.size)}</span>
                </div>
              </button>
            </li>
          ))}
        </ul>
      )}
      {previewPath && (
        <div className="file-preview">
          <div className="preview-head">
            <strong>{previewPath}</strong>
            <button type="button" className="btn btn-ghost btn-sm" onClick={() => { setPreview(""); setPreviewPath(""); }}><X size={12} aria-hidden /> Close</button>
          </div>
          <pre className="file-preview-body">{preview}</pre>
        </div>
      )}
      <TerminalView token={token} flash={flash} />
    </div>
  );
}

function TerminalView({ token, flash }) {
  const [cmd, setCmd] = useState("");
  const [history, setHistory] = useState([]);
  const [busy, setBusy] = useState(false);

  async function runCommand(ev) {
    ev?.preventDefault();
    const command = cmd.trim();
    if (!command || busy) return;
    setBusy(true);
    setHistory(h => [...h, `$ ${command}`]);
    setCmd("");
    try {
      const res = await apiJson("/api/computer/run", { token, method: "POST", body: { command } });
      if (res.ok) setHistory(h => [...h, res.data.output || "(no output)"]);
      else flash?.(res.data?.detail || "Command failed", true);
    } catch {
      flash?.("Command failed", true);
    }
    setBusy(false);
  }

  return (
    <div className="terminal-view">
      <div className="mem-title">Sandbox terminal</div>
      <ScrollArea className="terminal-output">
        {history.length === 0 && <pre className="terminal-line muted">Run commands in the shared sandbox…</pre>}
        {history.map((line, i) => (
          <pre key={i} className="terminal-line">{line}</pre>
        ))}
      </ScrollArea>
      <form className="terminal-input-row" onSubmit={runCommand}>
        <span className="terminal-prompt" aria-hidden>$</span>
        <input
          className="terminal-input"
          value={cmd}
          onChange={e => setCmd(e.target.value)}
          placeholder="ls, cat README.md, python script.py…"
          disabled={busy}
          aria-label="Sandbox command"
        />
        <Button type="submit" size="sm" variant="secondary" disabled={busy || !cmd.trim()}>{busy ? "…" : "Run"}</Button>
      </form>
    </div>
  );
}
