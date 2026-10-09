import { useCallback, useState } from "react";
import { X, FileText } from "lucide-react";
import { apiJson, Button, EmptyState, ScrollArea, Tooltip } from "../ui.jsx";
import { fmtBytes } from "../lib.js";
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

  const panelFlash = useCallback((message, error) => {
    flash?.(message, error ? "error" : "success");
  }, [flash]);

  if (!computer && activeTab === "files") {
    return (
      <section className="computer-embedded" aria-label={destination.label}>
        <PanelChrome
          title={destination.label}
          subtitle="Shared workspace offline"
          onRefresh={onRefresh}
        />
        <EmptyState kind="sandbox" title="Sandbox offline" message="Could not load workspace data. Agents can still use tools when configured." />
      </section>
    );
  }

  return (
    <section className="computer-embedded" aria-label={destination.label}>
      <PanelChrome
        title={destination.label}
        subtitle={[
          computer?.provider?.provider && `Computer: ${computer.provider.provider}`,
          computer?.workspace || computer?.note?.slice(0, 48) || "Shared workspace",
        ].filter(Boolean).join(" · ")}
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
        {activeTab === "files" && <SandboxView computer={computer} token={token} flash={panelFlash} />}
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
        <span className="panel-icon" aria-hidden>
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><rect x="2" y="3" width="20" height="14" rx="2"/><line x1="8" y1="21" x2="16" y2="21"/><line x1="12" y1="17" x2="12" y2="21"/></svg>
        </span>
        <div>
          <h2 className="panel-title">{title}</h2>
          <span className="panel-subtitle">{subtitle}</span>
        </div>
      </div>
      <div className="panel-header-actions">
        <Tooltip content="Refresh">
          <button className="btn btn-ghost btn-icon btn-sm" onClick={onRefresh} aria-label="Refresh">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M23 4v6h-6M1 20v-6h6"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/></svg>
          </button>
        </Tooltip>
      </div>
    </div>
  );
}

function SandboxView({ computer, token, flash }) {
  const [preview, setPreview] = useState("");
  const [previewPath, setPreviewPath] = useState("");
  const [busy, setBusy] = useState(false);
  const files = computer?.files || [];

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

  return (
    <div className="panel-body sandbox-panel">
      <p className="panel-note">{computer?.note || "Shared sandbox files for every bot."}</p>
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
                  <span className="file-size text-mono-xs text-subtle">{fmtBytes(f.size)}</span>
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
        {history.length === 0 && <pre className="terminal-line text-subtle">Run commands in the shared sandbox…</pre>}
        {history.map((line, i) => (
          <pre key={i} className="terminal-line text-mono-xs">{line}</pre>
        ))}
      </ScrollArea>
      <form className="terminal-input-row" onSubmit={runCommand}>
        <span className="terminal-prompt text-mono-xs">$</span>
        <input
          className="terminal-input text-mono-xs"
          value={cmd}
          onChange={e => setCmd(e.target.value)}
          placeholder="ls, cat README.md, python script.py…"
          disabled={busy}
        />
        <Button type="submit" size="sm" variant="secondary" disabled={busy || !cmd.trim()}>{busy ? "…" : "Run"}</Button>
      </form>
    </div>
  );
}
