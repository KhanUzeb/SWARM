import { useState } from "react";
import { Badge, Button, Card, Avatar, ScrollArea, Input, Textarea, Tooltip, EmptyState } from "../ui.jsx";
import { statusLabel, fmtTime } from "../lib.js";

export function ComputerPanel({ computer, onClose, onRefresh }) {
  const [path, setPath] = useState(computer?.cwd || "/workspace");
  const [activeTab, setActiveTab] = useState("files");

  if (!computer) {
    return (
      <aside id="computer-panel">
        <div className="panel-header">
          <h2 className="panel-title">Computer</h2>
          <button className="panel-close" onClick={onClose} aria-label="Close">×</button>
        </div>
        <EmptyState
          icon="🖥"
          title="No computer connected"
          message="The shared sandbox is offline. Agents can still use tools, but the workspace files aren't visible here."
        />
      </aside>
    );
  }

  const tabs = [
    { id: "files", label: "Files" },
    { id: "terminal", label: "Terminal" },
    { id: "screenshot", label: "Screen" },
  ];

  return (
    <aside id="computer-panel">
      <div className="panel-header">
        <div className="panel-header-main">
          <span className="panel-icon">🖥</span>
          <div>
            <h2 className="panel-title">Computer</h2>
            <span className="panel-subtitle text-mono-xs text-subtle">{computer.cwd}</span>
          </div>
        </div>
        <div className="panel-header-actions">
          <Tooltip content="Refresh"><button className="btn btn-ghost btn-icon btn-sm" onClick={onRefresh} aria-label="Refresh">⟳</button></Tooltip>
          <button className="panel-close" onClick={onClose} aria-label="Close">×</button>
        </div>
      </div>

      <div className="panel-tabs">
        {tabs.map(t => (
          <button key={t.id} className={`panel-tab ${activeTab === t.id ? "active" : ""}`} onClick={() => setActiveTab(t.id)}>
            {t.label}
          </button>
        ))}
      </div>

      <ScrollArea className="panel-body">
        {activeTab === "files" && <FilesView computer={computer} />}
        {activeTab === "terminal" && <TerminalView computer={computer} />}
        {activeTab === "screenshot" && <ScreenshotView computer={computer} />}
      </ScrollArea>
    </aside>
  );
}

function FilesView({ computer }) {
  const files = computer.files || [];
  if (files.length === 0) return <EmptyState icon="📁" title="No files" message="The workspace is empty." />;

  return (
    <div className="file-list">
      {files.map(f => (
        <div key={f.name} className="file-row">
          <span className="file-icon">{f.is_dir ? "📁" : "📄"}</span>
          <div className="file-info">
            <span className="file-name">{f.name}</span>
            {!f.is_dir && f.size != null && <span className="file-size text-mono-xs text-subtle">{fmtSize(f.size)}</span>}
          </div>
          {!f.is_dir && (
            <Tooltip content="Open"><button className="btn btn-ghost btn-sm file-open" aria-label="Open">↗</button></Tooltip>
          )}
        </div>
      ))}
    </div>
  );
}

function TerminalView({ computer }) {
  const [cmd, setCmd] = useState("");
  const [history, setHistory] = useState(computer.last_output ? [computer.last_output] : []);

  return (
    <div className="terminal-view">
      <ScrollArea className="terminal-output">
        {history.map((line, i) => (
          <pre key={i} className="terminal-line text-mono-xs">{line}</pre>
        ))}
      </ScrollArea>
      <div className="terminal-input-row">
        <span className="terminal-prompt text-mono-xs">$</span>
        <input
          className="terminal-input text-mono-xs"
          value={cmd}
          onChange={e => setCmd(e.target.value)}
          placeholder="Run a command in the sandbox…"
          onKeyDown={e => { if (e.key === "Enter" && cmd.trim()) { setHistory([...history, `$ ${cmd}`, "✓ done"]); setCmd(""); } }}
        />
      </div>
    </div>
  );
}

function ScreenshotView({ computer }) {
  if (!computer.screenshot) return <EmptyState icon="📷" title="No screenshot" message="Capture the screen to see what agents see." />;
  return (
    <div className="screenshot-view">
      <img src={computer.screenshot} alt="Computer screen" className="screenshot-img" />
    </div>
  );
}

function fmtSize(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}
