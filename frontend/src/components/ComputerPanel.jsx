import { useCallback, useEffect, useMemo, useState } from "react";
import { apiJson, Button, EmptyState, ScrollArea, Tooltip } from "../ui.jsx";
import { fmtBytes } from "../lib.js";
import SystemPanel from "../ai-support/SystemPanel.jsx";
import BrowserPanel from "../ai-support/BrowserPanel.jsx";
import ToolsPanel from "../ai-support/ToolsPanel.jsx";
import AppsPanel from "../ai-support/AppsPanel.jsx";
import ProviderPanel from "../ai-support/ProviderPanel.jsx";
import PluginsPanel from "../ai-support/PluginsPanel.jsx";

const PANEL_GROUPS = [
  { id: "places", label: "Places", tabs: [
    { id: "files", label: "Sandbox", icon: "📁" },
    { id: "system", label: "System", icon: "💻" },
    { id: "browser", label: "Browser", icon: "🌐" },
  ]},
  { id: "connect", label: "Connect", tabs: [
    { id: "ai", label: "AI", icon: "✦" },
    { id: "apps", label: "Apps", icon: "🔌" },
    { id: "tools", label: "Tools", icon: "🔧" },
    { id: "plugins", label: "Plugins", icon: "🧩" },
  ]},
];

export function ComputerPanel({ computer, token, user, meRole, onClose, onRefresh, flash }) {
  const [activeGroup, setActiveGroup] = useState("places");
  const [activeTab, setActiveTab] = useState("files");

  const tabs = useMemo(
    () => PANEL_GROUPS.find(g => g.id === activeGroup)?.tabs || PANEL_GROUPS[0].tabs,
    [activeGroup],
  );

  function selectTab(groupId, tabId) {
    setActiveGroup(groupId);
    setActiveTab(tabId);
  }

  const panelFlash = useCallback((message, error) => {
    flash?.(message, error ? "error" : "success");
  }, [flash]);

  if (!computer && activeTab === "files") {
    return (
      <aside id="computer-panel">
        <PanelChrome activeGroup={activeGroup} activeTab={activeTab} onSelectTab={selectTab} onClose={onClose} onRefresh={onRefresh} subtitle="Workspace offline" />
        <EmptyState icon="🖥" title="Sandbox offline" message="Could not load workspace data. Agents can still use tools when configured." />
      </aside>
    );
  }

  return (
    <aside id="computer-panel">
      <PanelChrome
        activeGroup={activeGroup}
        activeTab={activeTab}
        onSelectTab={selectTab}
        onClose={onClose}
        onRefresh={onRefresh}
        subtitle={computer?.workspace || computer?.note?.slice(0, 48) || "Shared workspace"}
      />

      <div className="panel-group-tabs">
        {PANEL_GROUPS.map(group => (
          <button
            key={group.id}
            type="button"
            className={`panel-group-tab${activeGroup === group.id ? " active" : ""}`}
            onClick={() => selectTab(group.id, group.tabs[0].id)}
          >
            {group.label}
          </button>
        ))}
      </div>

      <div className="panel-tabs">
        {tabs.map(t => (
          <button
            key={t.id}
            type="button"
            className={`panel-tab${activeTab === t.id ? " active" : ""}`}
            onClick={() => setActiveTab(t.id)}
          >
            <span className="panel-tab-icon" aria-hidden>{t.icon}</span>
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
    </aside>
  );
}

function PanelChrome({ activeGroup, activeTab, onSelectTab, onClose, onRefresh, subtitle }) {
  return (
    <div className="panel-header">
      <div className="panel-header-main">
        <span className="panel-icon">🖥</span>
        <div>
          <h2 className="panel-title">Workspace</h2>
          <span className="panel-subtitle text-mono-xs text-subtle">{subtitle}</span>
        </div>
      </div>
      <div className="panel-header-actions">
        <Tooltip content="Refresh">
          <button className="btn btn-ghost btn-icon btn-sm" onClick={onRefresh} aria-label="Refresh">⟳</button>
        </Tooltip>
        <button className="panel-close" onClick={onClose} aria-label="Close">×</button>
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
        <EmptyState icon="📁" title="Empty sandbox" message="Agents can write files here with write_workspace." />
      ) : (
        <ul className="file-list">
          {files.map(f => (
            <li key={f.path}>
              <button type="button" className="file-row" onClick={() => openFile(f.path)} disabled={busy}>
                <span className="file-icon">📄</span>
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
            <button type="button" className="btn ghost btn-sm" onClick={() => { setPreview(""); setPreviewPath(""); }}>Close</button>
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
