import { useState } from "react";
import { api } from "../lib.js";

export default function ToolsPanel({ token, toolCatalog, plugins, onReload, flash }) {
  const [showForm, setShowForm] = useState(false);
  const [busy, setBusy] = useState(false);
  const [form, setForm] = useState({
    name: "",
    description: "",
    handler_type: "template",
    template: "Result: {{query}}",
  });

  async function createTool(ev) {
    ev.preventDefault();
    const name = form.name.trim();
    const description = form.description.trim();
    if (!name || !description) return;
    setBusy(true);
    try {
      const res = await api("/api/tools/custom", {
        token,
        method: "POST",
        body: {
          name,
          description,
          handler_type: form.handler_type,
          handler_config: form.handler_type === "template"
            ? { template: form.template }
            : form.handler_type === "http_get"
              ? { url: form.template }
              : {},
          parameters: form.handler_type === "echo"
            ? { type: "object", properties: { text: { type: "string" } }, required: ["text"] }
            : { type: "object", properties: { query: { type: "string" } }, required: ["query"] },
        },
      });
      if (res.ok) {
        flash?.(`Custom tool ${name} saved`);
        setShowForm(false);
        setForm({ name: "", description: "", handler_type: "template", template: "Result: {{query}}" });
        onReload?.();
      } else if (res.status === 409) flash?.("Tool name already exists", true);
      else flash?.("Couldn't save custom tool", true);
    } catch {
      flash?.("Couldn't save custom tool", true);
    }
    setBusy(false);
  }

  async function deleteTool(id, name) {
    const res = await api(`/api/tools/custom/${id}`, { token, method: "DELETE", json: false });
    if (res.ok) {
      flash?.(`Removed ${name}`);
      onReload?.();
    } else flash?.("Couldn't delete tool", true);
  }

  const builtins = (toolCatalog || []).filter((t) => t.kind === "builtin");
  const customs = (toolCatalog || []).filter((t) => t.kind === "custom");
  const pluginTools = (toolCatalog || []).filter((t) => t.kind === "plugin");

  return (
    <div className="panel-body tools-panel">
      <p className="panel-note">
        Built-in tools, custom handlers (template / HTTP / echo), and plugin tools from <code>plugins/</code>.
      </p>
      <div className="panel-section">
        <div className="mem-title">Built-in ({builtins.length})</div>
        <ul className="panel-list tool-grid">
          {builtins.map((t) => (
            <li key={t.name}><span className="tool-tag builtin">{t.name}</span></li>
          ))}
        </ul>
      </div>
      {pluginTools.length > 0 && (
        <div className="panel-section">
          <div className="mem-title">Plugin tools ({pluginTools.length})</div>
          <ul className="panel-list tool-grid">
            {pluginTools.map((t) => (
              <li key={t.name} title={t.description}>
                <span className="tool-tag plugin">{t.tool_name || t.name}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
      <div className="panel-section">
        <div className="mem-title">Custom tools ({customs.length})</div>
        {!customs.length && <p className="empty-state">No custom tools yet.</p>}
        <ul className="panel-list">
          {customs.map((t) => (
            <li key={t.name}>
              <span className="tool-tag custom">{t.name}</span>
              <span className="hint"> — {t.description}</span>
              <button type="button" className="btn ghost" onClick={() => deleteTool(t.id, t.name)}>Delete</button>
            </li>
          ))}
        </ul>
        {!showForm ? (
          <button type="button" className="btn primary" onClick={() => setShowForm(true)}>+ Custom tool</button>
        ) : (
          <form className="mini-form" onSubmit={createTool}>
            <input required maxLength={64} pattern="[A-Za-z0-9_\-]+" placeholder="tool_name" value={form.name} onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))} />
            <input required maxLength={500} placeholder="What it does" value={form.description} onChange={(e) => setForm((f) => ({ ...f, description: e.target.value }))} />
            <select value={form.handler_type} onChange={(e) => setForm((f) => ({ ...f, handler_type: e.target.value }))}>
              <option value="template">Template handler</option>
              <option value="http_get">HTTP GET handler</option>
              <option value="echo">Echo handler</option>
            </select>
            <textarea rows={2} placeholder={form.handler_type === "http_get" ? "https://api.example.com?q={{query}}" : "Result: {{query}}"} value={form.template} onChange={(e) => setForm((f) => ({ ...f, template: e.target.value }))} />
            <div className="modal-actions">
              <button type="button" className="btn" onClick={() => setShowForm(false)}>Cancel</button>
              <button type="submit" className="btn primary" disabled={busy}>{busy ? "Saving…" : "Save"}</button>
            </div>
          </form>
        )}
      </div>
      {plugins?.length > 0 && (
        <div className="panel-section">
          <div className="mem-title">Loaded plugins</div>
          <ul className="panel-list dim">
            {plugins.map((p) => <li key={p.id}>{p.name} v{p.version} — {p.description}</li>)}
          </ul>
        </div>
      )}
    </div>
  );
}
